"""Repeatable Phase 12 release check. Synthetic data and mock integrations only.

Run from backend/: python scripts/verify_release.py [--database-url URL]
Without a URL, a temporary SQLite file is created and removed. PostgreSQL
must be empty, loopback-only, and named gtmflow_phase12_*; populated databases
are refused. --with-frontend also starts the already-built frontend and
exercises its same-origin API proxy. No deployment or external calls.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url")
    parser.add_argument("--with-frontend", action="store_true")
    args = parser.parse_args()
    checks: list[str] = []
    processes = []

    def check(condition, label):
        if not condition:
            raise AssertionError(label)
        checks.append(label)

    with tempfile.TemporaryDirectory(prefix="gtmflow_phase12_") as temp:
        db_url = args.database_url or f"sqlite:///{temp}/release.db"
        url = make_url(db_url)
        if args.database_url and not (url.drivername == "postgresql+psycopg" and url.host in ("127.0.0.1", "localhost")
                                      and (url.database or "").startswith("gtmflow_phase12_")):
            raise SystemExit("Refusing database: use a fresh loopback PostgreSQL database named gtmflow_phase12_*.")
        env = {**os.environ, "DATABASE_URL": db_url, "USE_MOCK_AI": "true", "OPENAI_API_KEY": "",
               "SLACK_WEBHOOK_URL": "", "AI_PROVIDER": "openai", "LORA_INFERENCE_BASE_URL": "",
               "LORA_INFERENCE_API_KEY": "", "SESSION_COOKIE_SECURE": "false", "PASSWORD_HASH_N": "32768",
               "ALLOWED_ORIGINS": "http://127.0.0.1:13000", "PYTHONUNBUFFERED": "1",
               "NEXT_TELEMETRY_DISABLED": "1", "API_PROXY_TARGET": "http://127.0.0.1:18000",
               "NEXT_PUBLIC_API_BASE_URL": "", "SELF_SIGNUP_ENABLED": "true", "GUEST_ACCESS_ENABLED": "true",
               "SMTP_HOST": ""}
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env.pop(key, None)
        os.environ.update({key: env[key] for key in ("DATABASE_URL", "USE_MOCK_AI", "OPENAI_API_KEY", "SLACK_WEBHOOK_URL")})
        engine = create_engine(db_url)
        check(not inspect(engine).get_table_names(), "database was empty before verification")

        def run(*command, extra_env=None):
            result = subprocess.run(command, cwd=BACKEND, env={**env, **(extra_env or {})},
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
            if result.returncode:
                # Commands never contain passwords/keys, and their tools do
                # not echo them. Keep failures useful without dumping data.
                raise RuntimeError(f"{command[2:]} failed:\n{result.stdout[-4000:]}")

        def migration(*command):
            run(sys.executable, "-m", "alembic", *command)

        if engine.dialect.name == "postgresql":
            migration("upgrade", "0011_slack_delivery_ledger")
        from app.models import AIOutput, AIOutputReview, Lead, LeadBatch, WorkflowEvent
        from app.models.auth import UserSession
        if engine.dialect.name == "sqlite":
            # Historical migrations target PostgreSQL. SQLite is the
            # Docker-free mock smoke path, not migration verification.
            from app.core.database import Base
            Base.metadata.create_all(engine)
        from app.models.background_job import BackgroundJob
        from app.models.integration_push import IntegrationPush

        # Seed a legacy batch, lead, output, approval and audit row BEFORE
        # the auth migrations. Compare every column after upgrade/rollback.
        legacy_id = uuid4()
        with Session(engine) as session:
            batch = LeadBatch(name="synthetic migration sentinel", source="synthetic", total_leads=1)
            session.add(batch)
            session.flush()
            lead = Lead(batch_id=batch.id, company_name="Synthetic migration sentinel", status="do_not_contact")
            session.add(lead)
            session.flush()
            output = AIOutput(lead_id=lead.id, output_type="outreach_email", content={"body": "historical sentinel"}, model_used="mock")
            session.add(output)
            session.flush()
            session.add(AIOutputReview(lead_id=lead.id, ai_output_id=output.id, decision="approve",
                                      reviewer_label="local-demo-unauthenticated", content_hash="0" * 64))
            session.execute(WorkflowEvent.__table__.insert().values(id=legacy_id, lead_id=lead.id,
                event_type="legacy_sentinel", event_data={"actor": "local-demo-unauthenticated", "keep": True}))
            session.commit()

        def snapshot():
            with engine.connect() as connection:
                return {model.__tablename__: [dict(row) for row in connection.execute(select(model.__table__)).mappings()]
                        for model in (LeadBatch, Lead, AIOutput, AIOutputReview, WorkflowEvent)}

        before = snapshot()
        if engine.dialect.name == "postgresql":
            migration("upgrade", "head")
            check(snapshot() == before, "auth migrations preserve every sentinel data/audit column")
            migration("check")
            migration("downgrade", "0011_slack_delivery_ledger")
            check(snapshot() == before, "disposable auth downgrade preserves legacy sentinel rows")
            migration("upgrade", "head")
            migration("check")
            check(snapshot() == before, "auth re-upgrade and model/migration parity")

        password = secrets.token_urlsafe(24)
        for name, role in (("release-operator", "operator"), ("release-viewer", "viewer")):
            run(sys.executable, "-m", "app.auth_cli", "create-user", name, "--role", role,
                extra_env={"GTMFLOW_NEW_PASSWORD": password})
        check(True, "CLI account creation with unprinted generated passwords")

        def start(command, cwd=BACKEND, guard=False):
            if guard:
                # Reuse the test suite's socket guard: external DNS/connect
                # attempts fail before traffic leaves either child process.
                module, *argv = command
                bootstrap = "import tests.conftest, runpy, sys; sys.argv=sys.argv[1:]; runpy.run_module(sys.argv[0],run_name='__main__')"
                command = [sys.executable, "-c", bootstrap, module, *argv]
            log = open(Path(temp) / f"process-{len(processes)}.log", "w+")
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
            processes.append((process, log))
            return process

        def wait_http(url):
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    if httpx.get(url, timeout=1, trust_env=False).status_code == 200:
                        return
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            raise AssertionError("local release server did not become ready")

        try:
            start(["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "18000"], guard=True)
            wait_http("http://127.0.0.1:18000/api/health")
            base = "http://127.0.0.1:18000"
            if args.with_frontend:
                start(["node", "node_modules/next/dist/bin/next", "start", "-H", "127.0.0.1", "-p", "13000"],
                      cwd=BACKEND.parent / "frontend")
                base = "http://127.0.0.1:13000"
                wait_http(base + "/api/health")
                check(httpx.get(base + "/login", trust_env=False).status_code == 200, "production frontend serves sign-in")

            with httpx.Client(base_url=base, timeout=30, trust_env=False) as client:
                def request(method, path, code=200, **kwargs):
                    response = client.request(method, path, **kwargs)
                    check(response.status_code == code, f"{method} {path.split('?')[0]} returns {code}")
                    return response.json() if code != 204 else None

                request("GET", "/api/health")
                options = request("GET", "/api/auth/options")
                check(options["self_signup"] and options["guest_access"] and options["email_delivery"] == "mock",
                      "sign-up and guest access offered, email mocked")
                with httpx.Client(base_url=base, timeout=30, trust_env=False) as visitor:
                    def visit(method, path, code=200, **kwargs):
                        response = visitor.request(method, path, **kwargs)
                        check(response.status_code == code, f"visitor {method} {path} returns {code}")
                        return response.json() if code != 204 else None

                    address = "release.visitor@example.com"
                    visit("POST", "/api/auth/register", 202, json={"email": address, "password": password})
                    visit("POST", "/api/auth/login", 401, json={"username": address, "password": password})
                    # Mock email: the code exists only in the API's own log.
                    api_log = Path(processes[0][1].name).read_text()
                    codes = re.findall(rf"verification code for {re.escape(address)}: (\d{{6}})", api_log)
                    check(len(codes) == 1, "one mock verification code issued")
                    member = visit("POST", "/api/auth/verify-email", json={"email": address, "code": codes[0]})
                    check(member["role"] == "operator", "verified sign-up is signed in")
                    visit("POST", "/api/auth/verify-email", 400, json={"email": address, "code": codes[0]})
                    visit("POST", "/api/auth/logout", 204, headers={"X-CSRF-Token": member["csrf_token"]})
                    visit("POST", "/api/auth/login", json={"username": address, "password": password})
                with httpx.Client(base_url=base, timeout=30, trust_env=False) as guest:
                    response = guest.post("/api/auth/guest", json={})
                    check(response.status_code == 200 and response.json()["role"] == "guest", "guest session through the proxy")
                    guest.headers["X-CSRF-Token"] = response.json()["csrf_token"]
                    check(guest.get("/api/metrics/dashboard").status_code == 200, "guest can use the app (mock-only server)")
                request("GET", "/api/leads", 401)
                request("POST", "/api/demo/run", 401)
                request("POST", "/api/auth/login", 401, json={"username": "release-operator", "password": "incorrect"})
                info = request("POST", "/api/auth/login", json={"username": "release-operator", "password": password})
                request("POST", "/api/demo/run", 403)
                client.headers["X-CSRF-Token"] = info["csrf_token"]
                demo = request("POST", "/api/demo/run", 201)
                check(demo["total_leads"] == 10 and demo["leads_pushed"] == 2, "standalone mock demo")

                profile = request("GET", "/api/seller-profile/demonstration-template")
                saved = request("POST", "/api/seller-profile", 201, json={"expected_version": 0, "profile": profile})
                request("POST", "/api/seller-profile/activate", 201, json={"seller_profile_id": saved["id"],
                    "expected_activation_sequence": 0, "confirm_reviewed": True, "acknowledge_demo": True})
                csv = (BACKEND.parent / "sample_data/leads_sample.csv").read_bytes()
                batch = request("POST", "/api/batches/upload", 201, files={"file": ("synthetic.csv", csv, "text/csv")})
                batch_id = batch["batch_id"]
                # Verify duplicate prevention/cancellation before starting
                # the worker, eliminating a timing race in this check.
                queued = request("POST", f"/api/batches/{batch_id}/jobs", 202, json={"job_type": "fit_score"})
                duplicate = request("POST", f"/api/batches/{batch_id}/jobs", json={"job_type": "fit_score"})
                check(duplicate["id"] == queued["id"] and duplicate["deduplicated"], "job enqueue is idempotent while active")
                request("POST", f"/api/jobs/{queued['id']}/cancel")
                worker = start(["app.jobs.worker", "--poll-seconds", "0.1"], guard=True)

                def job(kind):
                    # A cancelled queued job has already cleared its key.
                    row = request("POST", f"/api/batches/{batch_id}/jobs", 202, json={"job_type": kind})
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        response = client.get(f"/api/jobs/{row['id']}")
                        response.raise_for_status()
                        result = response.json()
                        if result["status"] in ("completed", "failed", "cancelled"):
                            check(result["status"] == "completed", f"real worker completes {kind}")
                            return result
                        if worker.poll() is not None:
                            raise AssertionError("worker exited before completing the job")
                        time.sleep(0.1)
                    raise AssertionError(f"worker timed out on {kind}")

                for kind in ("fit_score", "legacy_score", "generate_summary", "generate_outreach"):
                    job(kind)
                leads = request("GET", f"/api/leads?batch_id={batch_id}")["items"]
                scores = request("GET", f"/api/batches/{batch_id}/scores")["items"]
                hot_ids = {row["lead_id"] for row in scores if row["priority"] == "Hot"}
                check(len(hot_ids) == 2, "synthetic fixture has two Hot leads")
                drafts = {}
                for lead in leads:
                    draft = request("GET", f"/api/leads/{lead['id']}/latest-ai-output?output_type=outreach_email")
                    drafts[lead["id"]] = {"ai_output_id": draft["id"], "content_hash": draft["content_hash"]}
                first = sorted(hot_ids)[0]
                request("POST", f"/api/leads/{first}/push", 409, json={"force": True, "redeliver": True})
                for lead_id, body in drafts.items():
                    request("POST", f"/api/leads/{lead_id}/approve-outreach", json=body)
                request("POST", f"/api/leads/{first}/reject-outreach", json={**drafts[first], "reason": "synthetic check"})
                request("POST", f"/api/leads/{first}/push", 409, json={"redeliver": True})
                request("POST", f"/api/leads/{first}/approve-outreach", json=drafts[first])
                delivered = request("POST", f"/api/leads/{first}/push", json={})
                check(delivered["status"] == "mock_success", "delivery remains mock")
                replay = request("POST", f"/api/leads/{first}/push", json={})
                check(replay["replay"] and replay["id"] == delivered["id"], "repeat delivery replays without another send")
                job("push_hot")
                # Explicit synthetic unknown outcome, confined to this
                # disposable database, exercises the real resolution API.
                from uuid import UUID
                with Session(engine) as session:
                    row = session.get(IntegrationPush, UUID(delivered["id"]))
                    row.status = "unknown"
                    session.commit()
                request("POST", f"/api/leads/{first}/push", 409, json={"redeliver": True})
                request("POST", f"/api/pushes/{delivered['id']}/resolve", json={"resolution": "confirmed_delivered"})
                redelivered = request("POST", f"/api/leads/{first}/push", json={"redeliver": True})
                check(redelivered["id"] != delivered["id"] and redelivered["status"] == "mock_success", "explicit redelivery creates one new mock attempt")
                metrics = request("GET", "/api/metrics/dashboard")
                check(0 <= metrics["approval_rate"] <= 100 and metrics["data_mode"] == "mock_only"
                      and metrics["real_messages_delivered"] == 0, "cohort metrics bounded and explicitly mock")
                live_cookie = client.cookies.get("gtmflow_session")
                request("POST", "/api/auth/logout", 204)
                client.cookies.clear()
                client.cookies.set("gtmflow_session", live_cookie)
                request("GET", "/api/leads", 401)
                client.cookies.clear()
                info = request("POST", "/api/auth/login", json={"username": "release-viewer", "password": password})
                client.headers["X-CSRF-Token"] = info["csrf_token"]
                request("GET", "/api/metrics/dashboard")
                for path, body in ((f"/api/leads/{first}/approve-outreach", drafts[first]),
                                   (f"/api/leads/{first}/push", {"redeliver": True}),
                                   (f"/api/pushes/{delivered['id']}/resolve", {"resolution": "confirmed_not_delivered"}),
                                   (f"/api/jobs/{queued['id']}/cancel", {})):
                    request("POST", path, 403, json=body)
                request("POST", "/api/auth/logout", 204)

            with Session(engine) as session:
                events = session.scalars(select(WorkflowEvent).where(WorkflowEvent.id != legacy_id)).all()
                check(all(e.event_data.get("actor") for e in events), "every new audit event has an actor")
                job_events = [e for e in events if e.event_data["actor"].startswith("job:user:")]
                check(job_events and all(e.event_data.get("actor_user_id") for e in job_events), "worker preserves authenticated user IDs")
                check(all(job.created_by_user_id for job in session.scalars(select(BackgroundJob))), "every newly queued job has a stable creator ID")
                check(all(row.status != "success" for row in session.scalars(select(IntegrationPush))), "zero real deliveries in the release database")
                check(session.get(WorkflowEvent, legacy_id).event_data == before["workflow_events"][0]["event_data"], "legacy audit label unchanged after the live run")
                check(all(len(value) == 64 for value in session.scalars(select(UserSession.token_hash))), "only token hashes persisted")
            print(json.dumps({"passed": True, "database": engine.dialect.name, "frontend_proxy": args.with_frontend,
                              "migrations_verified": engine.dialect.name == "postgresql", "checks_passed": len(checks), "checks": checks}, indent=2))
        finally:
            for process, log in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                if process.returncode not in (0, -15):
                    log.seek(0)
                    print(log.read()[-2000:], file=sys.stderr)
                log.close()
            engine.dispose()


if __name__ == "__main__":
    main()
