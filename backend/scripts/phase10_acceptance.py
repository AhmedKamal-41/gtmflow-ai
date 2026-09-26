"""Phase 10 acceptance test: the app against a real server of the Phase 8 adapter.

    LORA_INFERENCE_API_KEY=<token> python scripts/phase10_acceptance.py \\
        --base-url http://127.0.0.1:18000/v1 --out <dir> \\
        --database-url postgresql+psycopg://postgres:acceptonly@127.0.0.1:55498/gtmflow_accept

Run from backend/. Normally started by `launch.py run --profile phase10-accept`
through an SSH tunnel; it can also be pointed at any local server (a stub for
a dry run). It never approves, rejects or pushes anything, and Slack stays
unconfigured. Exit code 0 only if every required criterion passes.

A. Model parity -- 20 fixed test-v1 eligible cases through `LocalLoRAClient`:
   A1 every request completes (no provider error, no truncation);
   A2 every output passes the app's grounding validator;
   A3 every output gets the same Phase 9 report categories (valid structure,
      factual support, missing-info handling, automated writing) as the stored
      Phase 9 prediction for that case;
   A4 (informational) outputs identical to the stored Phase 9 prediction.
B. App path -- a disposable database, the real API and a real worker process:
   B1 POST generate-summary and generate-outreach return 200 with the adapter
      provenance and runtime flags;
   B2 a generate_outreach background job completes (2 generated, 1 skipped);
   B3 no review and no push rows exist afterwards.
C. Negative -- C1 an unserved model name is refused before any generation;
   C2 a wrong token is refused.
D. (informational) latency and tokens per request.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
PHASE9 = REPO / "training" / "runs" / "phase9-eval-v1"
SHARED_FAILURE = "39fd8237"
CSV = (b"company_name,industry,contact_title,contact_name,contact_email,website,company_size,source,notes\n"
       b"Cascade Modular,Housing,VP Operations,Sarah Chen,sarah@cascade.com,cascade.com,240,referral,tenant maintenance leasing scheduling pain\n"
       b"Northbridge Clinics,Healthcare,Practice Manager,Anika Rao,anika@northbridge.com,northbridge.com,1000+,webinar,patient appointment scheduling intake forms\n"
       b"Vault Outfitters,Retail,COO,Jamie Russo,hello@vault.com,vault.com,560,csv,seasonal staff\n")
CATEGORIES = ("valid_structure", "factual_support", "missing_info_handling", "writing_acceptability_automated")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--skip-app-path", action="store_true", help="parts A and C only")
    parser.add_argument("--real-inference", action="store_true",
                        help="set only by launch.py when the server is the real adapter on the GPU pod")
    return parser.parse_args()


def safe_database(url: str) -> None:
    parts = urlsplit(url.replace("postgresql+psycopg", "postgresql"))
    if parts.hostname not in ("127.0.0.1", "localhost") or not parts.path.endswith("_accept"):
        sys.exit("refused: --database-url must be a local, disposable database whose name ends in _accept")


def select_cases(rows: list[dict], review: dict) -> list[dict]:
    """Deterministic: 10 outreach (the shared Phase 9 failure, 5 the blind
    reviewer rejected, 4 it accepted) and 10 summaries (5 per segment)."""
    lora = {i["example_id"]: i for i in review["per_item"] if i["system"] == "qwen3-4b-lora-v1"}
    by_id = sorted(rows, key=lambda r: r["example_id"])
    outreach = [r for r in by_id if r["task"] == "outreach_email"]
    shared = [r for r in outreach if r["example_id"].startswith(SHARED_FAILURE)]
    rest = [r for r in outreach if r not in shared]
    rejected = [r for r in rest if not lora[r["example_id"]]["acceptable_as_is"]][:5]
    accepted = [r for r in rest if lora[r["example_id"]]["acceptable_as_is"]][:4]
    summaries = [r for r in by_id if r["task"] == "company_summary"]
    picked = [r for seg in ("healthcare", "real_estate") for r in [s for s in summaries if s["segment"] == seg][:5]]
    return shared + rejected + accepted + picked


def main() -> int:
    args = parse_args()
    safe_database(args.database_url)
    token = os.environ.get("LORA_INFERENCE_API_KEY", "")
    # The app reads its configuration at import: set it first. Real LoRA mode,
    # no OpenAI key, no Slack webhook.
    os.environ.update({"DATABASE_URL": args.database_url, "USE_MOCK_AI": "false", "AI_PROVIDER": "qwen3-4b-lora-v1",
                       "LORA_INFERENCE_BASE_URL": args.base_url, "LORA_INFERENCE_API_KEY": token,
                       "OPENAI_API_KEY": "", "SLACK_WEBHOOK_URL": ""})
    sys.path.insert(0, str(BACKEND))
    from app.ai import quality_checks
    from app.ai.client import AIConfigError, AIProviderError
    from app.ai.grounding import validate_outreach, validate_summary
    from app.ai.json_parser import parse_json_strict
    from app.ai.lora_client import ADAPTER_REVISION, LocalLoRAClient
    from app.evaluation import phase9

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    criteria: dict[str, dict] = {}

    def criterion(key: str, required: bool, passed: bool, detail) -> None:
        criteria[key] = {"required": required, "passed": bool(passed), "detail": detail}

    # ---------------------------------------------------------------- A
    rows = [json.loads(line) for line in (BACKEND / "data/datasets/test-v1/eligible.jsonl").read_text().splitlines()]
    stored = {r["example_id"]: r for r in map(json.loads, (PHASE9 / "run/predictions/qwen3-4b-lora-v1.jsonl").read_text().splitlines())}
    review = json.loads((PHASE9 / "review/review-result.json").read_text())
    cases = select_cases(rows, review)
    client = LocalLoRAClient(base_url=args.base_url, api_key=token, timeout_seconds=600)
    records = []
    for row in cases:
        ctx, task = row["input_snapshot"], row["task"]
        rec = {"example_id": row["example_id"], "task": task, "segment": row["segment"]}
        started = time.time()
        try:
            output = client.generate_outreach(ctx) if task == "outreach_email" else client.generate_company_summary(ctx)
        except Exception as error:  # noqa: BLE001 -- recorded; A1 fails
            rec.update(error=f"{type(error).__name__}: {str(error)[:200]}", seconds=round(time.time() - started, 2))
            records.append(rec)
            continue
        rec["seconds"] = round(time.time() - started, 2)
        rec["usage"] = client.last_usage
        try:
            (validate_outreach if task == "outreach_email" else validate_summary)(output, ctx)
            rec["validator_ok"] = True
        except Exception as error:  # noqa: BLE001
            rec["validator_ok"], rec["validator_error"] = False, str(error)[:200]
        new_scores = phase9.score_prediction(row, {"output": output})
        old_scores = phase9.score_prediction(row, stored[row["example_id"]])
        rec["categories"] = {c: new_scores[c] for c in CATEGORIES}
        rec["categories_phase9"] = {c: old_scores[c] for c in CATEGORIES}
        rec["identical_to_phase9"] = output == parse_json_strict(stored[row["example_id"]]["text"])
        rec["runtime_flags"] = quality_checks.flag_codes(quality_checks.check_output(task, output, ctx))
        rec["output"] = output
        records.append(rec)
    done = [r for r in records if "error" not in r]
    criterion("A1_all_complete", True, len(done) == len(cases), f"{len(done)}/{len(cases)} completed without error or truncation")
    criterion("A2_all_valid", True, all(r.get("validator_ok") for r in done) and len(done) == len(cases),
              f"{sum(bool(r.get('validator_ok')) for r in done)}/{len(cases)} pass the app validator")
    same = [r for r in done if r["categories"] == r["categories_phase9"]]
    criterion("A3_same_phase9_categories", True, len(same) == len(cases), f"{len(same)}/{len(cases)} match Phase 9 categories")
    identical = sum(bool(r.get("identical_to_phase9")) for r in done)
    criterion("A4_identical_outputs", False, True, f"{identical}/{len(cases)} identical to the stored Phase 9 output")

    # ---------------------------------------------------------------- C
    try:
        LocalLoRAClient(base_url=args.base_url, api_key=token, served_model="not-served").generate_company_summary(cases[-1]["input_snapshot"])
        criterion("C1_unserved_model_refused", True, False, "no error raised")
    except AIConfigError as error:
        criterion("C1_unserved_model_refused", True, "does not serve" in str(error), str(error)[:120])
    except Exception as error:  # noqa: BLE001
        criterion("C1_unserved_model_refused", True, False, f"{type(error).__name__}")
    from app.ai import lora_client as lc
    lc._verified.clear()
    try:
        LocalLoRAClient(base_url=args.base_url, api_key="wrong-token").generate_company_summary(cases[-1]["input_snapshot"])
        criterion("C2_wrong_token_refused", True, False, "no error raised")
    except (AIProviderError, AIConfigError) as error:
        criterion("C2_wrong_token_refused", True, True, f"{type(error).__name__}: {str(error)[:100]}")
    lc._verified.clear()

    # ---------------------------------------------------------------- B
    app_path: dict = {}
    if not args.skip_app_path:
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, check=True,
                       env=os.environ.copy(), capture_output=True)
        from fastapi.testclient import TestClient
        from sqlalchemy import func, select

        from app.core.database import get_sessionmaker
        from app.main import app
        from app.models import AIOutput, AIOutputReview, IntegrationPush

        session = get_sessionmaker()()
        if session.scalar(select(func.count()).select_from(AIOutput)):
            sys.exit("refused: the acceptance database is not empty; use a fresh disposable database")
        api = TestClient(app)
        template = api.get("/api/seller-profile/demonstration-template").json()
        saved = api.post("/api/seller-profile", json={"expected_version": 0, "profile": template}).json()
        seq = api.get("/api/seller-profile/status").json()["activation_sequence"]
        api.post("/api/seller-profile/activate", json={"seller_profile_id": saved["id"], "expected_activation_sequence": seq,
                                                      "confirm_reviewed": True, "acknowledge_demo": True})
        batch = api.post("/api/batches/upload", files={"file": ("acceptance.csv", CSV, "text/csv")}).json()["batch_id"]
        leads = api.get(f"/api/leads?batch_id={batch}").json()["items"]
        first = leads[0]["id"]
        started = time.time()
        summary = api.post(f"/api/leads/{first}/generate-summary")
        outreach = api.post(f"/api/leads/{first}/generate-outreach")
        app_path["sync_seconds"] = round(time.time() - started, 2)
        sync = [summary, outreach]
        prov_ok = all(r.status_code == 200 and r.json()["model_used"] == "qwen3-4b-lora-v1"
                      and r.json()["adapter_revision"] == ADAPTER_REVISION and "quality_flags" in r.json() for r in sync)
        app_path["sync"] = [{"status": r.status_code, "flags": [f["code"] for f in r.json().get("quality_flags", [])]
                             if r.status_code == 200 else None, "detail": r.json().get("detail") if r.status_code != 200 else None}
                            for r in sync]
        criterion("B1_api_generation_with_provenance", True, prov_ok, app_path["sync"])
        job = api.post(f"/api/batches/{batch}/jobs", json={"job_type": "generate_outreach"}).json()
        started = time.time()
        worker = subprocess.run([sys.executable, "-m", "app.jobs.worker", "--drain", "--worker-id", "acceptance"],
                                cwd=BACKEND, env=os.environ.copy(), capture_output=True, text=True, timeout=1800)
        app_path["job_seconds"] = round(time.time() - started, 2)
        final = api.get(f"/api/jobs/{job['id']}").json()
        app_path["job"] = {"status": final["status"], "counts": final["counts"], "last_error": final["last_error"],
                           "worker_rc": worker.returncode}
        criterion("B2_background_job", True, final["status"] == "completed" and final["counts"].get("succeeded") == 2
                  and final["counts"].get("skipped") == 1, app_path["job"])
        session.expire_all()
        reviews = session.scalar(select(func.count()).select_from(AIOutputReview))
        pushes = session.scalar(select(func.count()).select_from(IntegrationPush))
        outputs = session.scalars(select(AIOutput)).all()
        app_path["outputs"] = [{"type": o.output_type, "model_used": o.model_used, "adapter_ok": o.adapter_revision == ADAPTER_REVISION}
                               for o in outputs]
        criterion("B3_no_review_or_push", True, reviews == 0 and pushes == 0 and all(o["adapter_ok"] for o in app_path["outputs"]),
                  {"reviews": reviews, "pushes": pushes, "outputs": len(outputs)})
        session.close()

    # ---------------------------------------------------------------- D
    secs = [r["seconds"] for r in done]
    tokens = [r["usage"]["completion_tokens"] for r in done if r.get("usage")]
    latency = {"requests": len(secs), "p50_seconds": round(statistics.median(secs), 2) if secs else None,
               "p95_seconds": round(sorted(secs)[max(0, int(0.95 * len(secs)) - 1)], 2) if secs else None,
               "mean_completion_tokens": round(statistics.mean(tokens), 1) if tokens else None}
    criterion("D_latency", False, True, latency)

    required_ok = all(c["passed"] for c in criteria.values() if c["required"])
    result = {"label": "Phase 10 acceptance -- real inference of the Phase 8 adapter through the app"
                       if args.real_inference else f"DRY RUN against {args.base_url} (not real inference)",
              "passed": required_ok, "criteria": criteria, "cases": records, "app_path": app_path,
              "adapter_revision": ADAPTER_REVISION}
    (out / "acceptance-result.json").write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps({"passed": required_ok, "criteria": {k: (v["passed"], v["detail"]) for k, v in criteria.items()}},
                     indent=1, default=str))
    return 0 if required_ok else 1


if __name__ == "__main__":
    sys.exit(main())
