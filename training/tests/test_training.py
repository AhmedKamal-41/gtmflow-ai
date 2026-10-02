"""Phase 8 training package tests (CPU, synthetic data, no paid compute).

Tests that need the pinned tokenizer skip when it is not in the local cache
(training/.cache/hf); everything else runs anywhere.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest
import torch

from gtmflow_training import data as D
from gtmflow_training import train as T
from gtmflow_training import weights as W

CONFIG = Path(__file__).resolve().parents[1] / "configs" / "phase8-qwen3-4b-lora-v1.json"


def _tokenizer_available() -> bool:
    try:
        from transformers import AutoTokenizer
        cfg = T.load_config(str(CONFIG))
        AutoTokenizer.from_pretrained(cfg["base_model"]["repo"], revision=cfg["base_model"]["revision"],
                                      cache_dir=cfg["base_model"]["cache_dir"], local_files_only=True)
        return True
    except Exception:  # noqa: BLE001
        return False


needs_tokenizer = pytest.mark.skipif(not _tokenizer_available(), reason="pinned tokenizer not cached locally")

CTX = {"lead_facts": [{"id": "fact-company_name", "field": "company_name", "value": "Acme Dental", "kind": "structured_field"}],
       "unknowns": ["budget"], "seller": None}
SUMMARY = {"company_summary": "Acme Dental is listed as a medical practice.", "evidence": [], "unknowns": ["budget"],
           "hypotheses": [], "seller_relevance": None, "confidence": "low"}
OUTREACH = {"subject": "Hello", "email_body": "Hi,\n\nThis email is part of a portfolio demonstration and is not a commercial offer.",
            "lead_facts_used": ["fact-company_name"], "capabilities_used": [], "claims_used": [],
            "unknowns_acknowledged": [], "call_note": "", "confidence": "low"}


def _row(i: int, split: str, weight: float = 1.0, task: str = "company_summary", **extra):
    return {"example_id": f"ex-{split}-{i}", "task": task, "split": split, "input_snapshot": CTX,
            "target": SUMMARY if task == "company_summary" else OUTREACH, "weight": weight,
            "uncertain": False, "prompt_version": "grounded-v2", **extra}


def _dataset(tmp_path: Path, name: str, rows, allowed_use="training"):
    d = tmp_path / name
    d.mkdir()
    (d / "eligible.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (d / "dataset-manifest.json").write_text(json.dumps({"allowed_use": allowed_use}))
    return {"dir": str(d), "sha256": {n: D.sha256_file(d / n) for n in ("eligible.jsonl", "dataset-manifest.json")}}


# ------------------------------------------------------------- guards

def test_loading_prompts_does_not_touch_backend_settings():
    assert "app.core.config" not in sys.modules  # no settings / .env / API keys loaded
    assert D.JSON_SYSTEM_MESSAGE.startswith("Reply with strict JSON only")
    assert D.PROMPT_VERSION == "grounded-v2"


def test_split_guards(tmp_path):
    train = _dataset(tmp_path, "train", [_row(1, "train", 0.5), _row(2, "train")])
    assert len(D.load_split(train, "train")) == 2
    with pytest.raises(D.DataGuardError, match="reserved"):
        D.load_split(train, "test")
    test_as_val = _dataset(tmp_path, "t", [_row(1, "test")], allowed_use="evaluation only")
    with pytest.raises(D.DataGuardError, match="test"):
        D.load_split(test_as_val, "validation")
    val_as_train = _dataset(tmp_path, "v", [_row(1, "validation")], allowed_use="evaluation only")
    with pytest.raises(D.DataGuardError, match="not an all-train"):
        D.load_split(val_as_train, "train")
    flagged = _dataset(tmp_path, "f", [_row(1, "train", uncertain=True)])
    with pytest.raises(D.DataGuardError, match="uncertain"):
        D.load_split(flagged, "train")
    unweighted = _dataset(tmp_path, "w", [_row(1, "train", 0.0)])
    with pytest.raises(D.DataGuardError, match="weight"):
        D.load_split(unweighted, "train")
    tampered = dict(train, sha256=dict(train["sha256"], **{"eligible.jsonl": "0" * 64}))
    with pytest.raises(D.DataGuardError, match="does not match"):
        D.load_split(tampered, "train")


def test_config_refuses_a_test_dataset_and_train_needs_confirmation(tmp_path, monkeypatch):
    cfg = json.loads(CONFIG.read_text())
    cfg["data"]["test"] = {"dir": "x", "sha256": {}}
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(cfg))
    with pytest.raises(D.DataGuardError, match="reserved"):
        T.load_config(str(bad))
    # Force "no GPU": on a GPU host this call would otherwise start the real run.
    monkeypatch.setattr(T.torch.cuda, "is_available", lambda: False)
    assert T.main(["train", "--config", str(CONFIG)]) == 2  # no --confirm-paid-compute
    assert T.main(["train", "--config", str(CONFIG), "--confirm-paid-compute"]) == 2  # no CUDA


def test_pinned_config_matches_phase7_datasets():
    cfg = json.loads(CONFIG.read_text())
    assert cfg["base_model"]["revision"] == "cdbee75f17c01a7cc42f958dc650907174af0554"
    assert cfg["data"]["train"]["sha256"]["eligible.jsonl"].startswith("a69ea054")
    assert cfg["data"]["validation"]["sha256"]["eligible.jsonl"].startswith("8306c8d0")
    assert "test" not in cfg["data"] and cfg["data"]["use_example_weights"] is True


# ------------------------------------------------------------ weights

def test_weighted_objective_equals_weight_normalized_mean():
    losses = [2.0, 1.0, 4.0, 3.0]
    weights = [1.0, 0.25, 0.5, 1.0]
    norm = W.weight_normalizer(weights)
    per_micro = [W.weighted_loss(torch.tensor(l), w, norm).item() for l, w in zip(losses, weights)]
    expected = sum(w * l for w, l in zip(weights, losses)) / sum(weights)
    assert math.isclose(sum(per_micro) / len(per_micro), expected, rel_tol=1e-6)


def test_weight_scales_the_gradient():
    p = torch.nn.Parameter(torch.tensor([1.0, -1.0]))
    targets = torch.tensor([0])
    grads = []
    for w in (1.0, 0.25):
        p.grad = None
        W.weighted_loss(W.answer_token_loss(p.unsqueeze(0), targets), w, 1.0).backward()
        grads.append(p.grad.clone())
    assert torch.allclose(grads[1], grads[0] * 0.25)


# ---------------------------------------------------------- formatting

@needs_tokenizer
def test_formatting_is_production_identical_and_masks_the_prompt():
    cfg = T.load_config(str(CONFIG))
    tok = T.load_tokenizer(cfg)
    for task, build in (("company_summary", D.build_summary_prompt), ("outreach_email", D.build_outreach_prompt)):
        row = _row(1, "train", task=task)
        prompt, target = D.build_messages(row)
        assert prompt[0]["content"] == D.JSON_SYSTEM_MESSAGE and prompt[1]["content"] == build(CTX)
        enc = D.encode(row, tok, 4096)
        answer = [t for t in enc["labels"] if t != -100]
        assert enc["labels"][: enc["prompt_tokens"]] == [-100] * enc["prompt_tokens"]
        assert json.loads(tok.decode(answer).split("<|im_end|>")[0]) == row["target"]
    with pytest.raises(D.DataGuardError, match="max_seq_len"):
        D.encode(_row(1, "train"), tok, 16)


# --------------------------------------------------------------- smoke

def _smoke_config(tmp_path: Path) -> Path:
    cfg = json.loads(CONFIG.read_text())
    train_rows = [_row(i, "train", w, task=t) for i, (w, t) in enumerate(
        [(0.25, "outreach_email"), (0.5, "company_summary"), (1.0, "company_summary"), (1.0, "outreach_email")])]
    cfg["data"]["train"] = _dataset(tmp_path, "train", train_rows)
    cfg["data"]["validation"] = _dataset(tmp_path, "val", [_row(1, "validation"), _row(2, "validation", task="outreach_email")],
                                         allowed_use="evaluation only")
    cfg["base_model"]["cache_dir"] = str((CONFIG.parent / cfg["base_model"]["cache_dir"]).resolve())
    cfg["smoke"].update({"train_examples": 4, "validation_examples": 2})
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(cfg))
    return path


@needs_tokenizer
def test_smoke_run_is_deterministic_and_applies_weights(tmp_path):
    path = _smoke_config(tmp_path)
    manifests, logs = [], []
    for i in (1, 2):
        out = tmp_path / f"run{i}"
        assert T.main(["smoke", "--config", str(path), "--out", str(out)]) == 0
        manifests.append(json.loads((out / "run-manifest.json").read_text()))
        logs.append((out / "log.jsonl").read_text())
        assert (out / "best_adapter" / "adapter_model.safetensors").exists()
        assert (out / "config.json").read_bytes() == path.read_bytes()
        assert (out / "tokenizer" / "tokenizer.json").exists()
    assert logs[0] == logs[1]
    m = manifests[0]
    weighting = m["result"]["weighting"]
    assert weighting["applied"] and weighting["examples_below_weight_1"] == 2
    assert math.isclose(weighting["normalizer_mean_weight"], 0.6875)
    assert m["test_data"].startswith("not loaded")
    assert m["result"]["best_validation_loss"] < m["validation_before_training"]["loss"]
    assert m["result"]["epochs_completed"] == 2 and not m["result"]["stopped_for_deadline"]
    assert all({"weighted_objective", "unweighted_mean"} <= set(h["train"]) for h in m["result"]["history"])
    assert m["tokenizer"]["revision"] == "cdbee75f17c01a7cc42f958dc650907174af0554"
    assert "adapter_model.safetensors" in m["artifact_sha256"]


@needs_tokenizer
def test_deadline_stops_after_a_completed_epoch_and_keeps_the_best_adapter(tmp_path):
    cfg = T.load_config(str(_smoke_config(tmp_path)))
    cfg["_deadline_unix"] = 0  # already past: the first epoch runs, the second must not start
    m = T.run(cfg, "smoke", tmp_path / "out")
    assert m["result"]["stopped_for_deadline"] and m["result"]["epochs_completed"] == 1
    assert (tmp_path / "out" / "best_adapter" / "adapter_model.safetensors").exists()


@needs_tokenizer
def test_preflight_measures_without_changing_or_saving_weights(tmp_path, monkeypatch):
    cfg = T.load_config(str(_smoke_config(tmp_path)))
    cfg["_tiny_preflight"] = True  # CPU stand-in for the GPU path
    m = T.run(cfg, "preflight", tmp_path / "pf")
    assert m["preflight"]["projected_training_minutes"] >= 0
    assert m["preflight"]["longest_train_tokens"] > 0
    assert (tmp_path / "pf" / "preflight.json").exists()
    assert not (tmp_path / "pf" / "best_adapter").exists()
    monkeypatch.setattr(T.torch.cuda, "is_available", lambda: False)  # never the real GPU path in tests
    assert T.main(["preflight", "--config", str(CONFIG)]) == 2  # needs confirmation
    assert T.main(["preflight", "--config", str(CONFIG), "--confirm-paid-compute"]) == 2  # no CUDA


# ------------------------------------------------------- RunPod launcher

def _launcher():
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "scripts" / "runpod" / "launch.py"
    spec = importlib.util.spec_from_file_location("gtmflow_runpod_launch", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not (Path(__file__).resolve().parents[2] / ".git").exists(),
                    reason="needs a git checkout (git check-ignore); run bundles have none")
def test_launcher_reads_only_the_key_from_an_ignored_env_file_and_scrubs_it(tmp_path, monkeypatch, capsys):
    L = _launcher()
    dummy = "rpa_" + "X" * 20  # not a real key
    ignored_dir = Path(__file__).resolve().parents[1] / "runs" / "pytest-env"  # training/runs/ is git-ignored
    ignored_dir.mkdir(parents=True, exist_ok=True)
    env = ignored_dir / ".env"
    env.write_text(f"OPENAI_API_KEY=other\nexport RUNPOD_API_KEY=\"{dummy}\"\n")
    assert L._key_from_env_file(env) == dummy
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    monkeypatch.setattr(L, "ENV_FILE", env)
    monkeypatch.setattr(L, "STATE_DIR", tmp_path)
    monkeypatch.setattr(L, "EVENTS", tmp_path / "events.jsonl")
    assert L.api_key() == dummy
    L.event("probe", detail=f"Bearer {dummy}")
    assert dummy not in capsys.readouterr().out and dummy not in (tmp_path / "events.jsonl").read_text()
    tracked = Path(__file__)  # a tracked file must be refused as a credential source
    with pytest.raises(SystemExit, match="not git-ignored"):
        L._key_from_env_file(tracked)
    env.unlink()


# ------------------------------------------------------ Phase 9 generation

from gtmflow_training import generate as G  # noqa: E402

PHASE9_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "phase9-eval-v1.json"


def _eval_input(i: int, split: str, subset: str, task: str) -> dict:
    return {"example_id": f"{split}-{subset}-{i}", "split": split, "subset": subset, "task": task,
            "input_snapshot": CTX, "prompt_version": "grounded-v2"}


def _phase9_config(tmp_path: Path) -> Path:
    cfg = json.loads(PHASE9_CONFIG.read_text())
    tasks = ("company_summary", "outreach_email")
    for name in cfg["pass_order"]:
        split, subset = name.split("-")
        path = tmp_path / f"{name}.jsonl"
        path.write_text("".join(json.dumps(_eval_input(i, split, subset, tasks[i % 2])) + "\n" for i in range(3)))
        cfg["inputs"][name] = {"path": str(path), "sha256": D.sha256_file(path)}
    cfg["base_model"]["cache_dir"] = str((PHASE9_CONFIG.parent / cfg["base_model"]["cache_dir"]).resolve())
    cfg["smoke"]["examples"] = 3
    out = tmp_path / "phase9.json"
    out.write_text(json.dumps(cfg))
    return out


def test_eval_inputs_refuse_references_and_non_heldout_rows(tmp_path):
    good = tmp_path / "ok.jsonl"
    good.write_text(json.dumps(_eval_input(1, "test", "eligible", "company_summary")) + "\n")
    assert len(D.load_eval_inputs({"path": str(good), "sha256": D.sha256_file(good)})) == 1
    for name, row in (("target", {**_eval_input(1, "test", "eligible", "company_summary"), "target": SUMMARY}),
                      ("source", {**_eval_input(1, "test", "eligible", "company_summary"), "source_output": SUMMARY}),
                      ("train", _eval_input(1, "train", "eligible", "company_summary"))):
        bad = tmp_path / f"{name}.jsonl"
        bad.write_text(json.dumps(row) + "\n")
        with pytest.raises(D.DataGuardError):
            D.load_eval_inputs({"path": str(bad), "sha256": D.sha256_file(bad)})
    with pytest.raises(D.DataGuardError, match="does not match"):
        D.load_eval_inputs({"path": str(good), "sha256": "0" * 64})


def test_adapter_hashes_are_verified(tmp_path):
    (tmp_path / "adapter_config.json").write_text("{}")
    pinned = {"adapter_config.json": D.sha256_file(tmp_path / "adapter_config.json")}
    assert G.verify_adapter(tmp_path, pinned) == pinned
    with pytest.raises(D.DataGuardError, match="adapter"):
        G.verify_adapter(tmp_path, {"adapter_config.json": "0" * 64})


def test_generate_run_refuses_without_confirmation_or_gpu(monkeypatch):
    monkeypatch.setattr(G.torch.cuda, "is_available", lambda: False)  # never the real GPU path in tests
    assert G.main(["run", "--config", str(PHASE9_CONFIG)]) == 2
    assert G.main(["run", "--config", str(PHASE9_CONFIG), "--confirm-paid-compute"]) == 2


@needs_tokenizer
def test_generation_smoke_is_complete_deterministic_and_base_means_adapter_off(tmp_path):
    cfg_path = _phase9_config(tmp_path)
    runs = []
    for i in (1, 2):
        m = G.run(G.load_config(str(cfg_path)), "smoke", tmp_path / f"gen{i}")
        runs.append({s: (tmp_path / f"gen{i}" / "predictions" / f"{s}.jsonl").read_text() for s in G.SYSTEMS})
        assert m["stopped"] is None and not m["validation_gate"]["enforced"]
    assert runs[0] == runs[1]  # greedy + fixed batches: byte-identical
    recs = {s: [json.loads(l) for l in runs[0][s].splitlines()] for s in G.SYSTEMS}
    for s in G.SYSTEMS:  # every input of every pass has a prediction record
        assert len(recs[s]) == 12 and len({(r["pass"], r["example_id"]) for r in recs[s]}) == 12
    # The base system is the plain model: generating without any adapter gives the same tokens.
    cfg = G.load_config(str(cfg_path))
    tok = T.load_tokenizer(cfg)
    tok.padding_side = "left"
    plain = T.load_model(cfg, tiny=True).eval()
    rows = G.load_inputs(cfg, limit=3)["validation-eligible"]
    plain_out = []
    for batch in G.batches(rows, tok, cfg["smoke"]["batch_size"]):
        plain_out += G.generate_batch(plain, tok, batch, cfg["smoke"]["max_new_tokens"], G.eos_ids(plain, tok), "cpu")
    base_val = [r for r in recs["qwen3-4b-base"] if r["pass"] == "validation-eligible"]
    assert [r["text"] for r in plain_out] == [r["text"] for r in base_val]
    lora_val = [r for r in recs["qwen3-4b-lora-v1"] if r["pass"] == "validation-eligible"]
    assert [r["text"] for r in lora_val] != [r["text"] for r in base_val]  # the (random) adapter is applied


@needs_tokenizer
def test_enforced_validation_gate_stops_before_any_test_generation(tmp_path):
    cfg = G.load_config(str(_phase9_config(tmp_path)))
    m = G.run(cfg, "smoke", tmp_path / "gated", enforce_gate=True)  # tiny model: always truncated -> gate fails
    assert m["stopped"] == "validation gate failed" and not m["validation_gate"]["passed"]
    skipped = [p for p in m["passes"] if not p["generated"]]
    assert {p["name"] for p in skipped} == {"test-eligible", "test-flagged"} and len(skipped) == 4
    test_recs = [l for s in G.SYSTEMS for l in (tmp_path / "gated" / "predictions" / f"{s}.jsonl").read_text().splitlines()
                 if '"split": "test"' in l]
    assert test_recs == []


def test_phase9_bundle_allowlist_matches_the_pod_and_refuses_references():
    import re
    L = _launcher()
    job = (Path(__file__).resolve().parents[1] / "scripts" / "runpod" / "pod_eval_job.sh").read_text()
    assert f'PHASE9_ALLOWED = r"{L.PHASE9_ALLOWED}"' in job and f'PHASE9_FORBIDDEN = r"{L.PHASE9_FORBIDDEN}"' in job
    ok = lambda n: bool(re.match(L.PHASE9_ALLOWED, n)) and not re.search(L.PHASE9_FORBIDDEN, n, re.I)
    for name in ("BUNDLE_COMMIT", "backend/app/ai/prompts.py", "training/gtmflow_training/generate.py",
                 "training/runs/phase9-eval-inputs/test-eligible.jsonl", "training/runs/phase9-eval-inputs/validation-flagged.jsonl",
                 "training/runs/phase9-eval-inputs/inputs-manifest.json",
                 "training/runs/phase8-qwen3-4b-lora-v1/train/best_adapter/adapter_model.safetensors"):
        assert ok(name), name
    for name in ("backend/data/datasets/test-v1/eligible.jsonl", "backend/data/datasets/test-v1/flagged-uncertain.jsonl",
                 "backend/data/ai_reviews/test-v1/decisions/1.json", "backend/.env", "backend/app/core/config.py",
                 "training/runs/phase8-qwen3-4b-lora-v1/train/best_adapter/README.md",
                 "training/runs/phase8-qwen3-4b-lora-v1/train/tokenizer/tokenizer.json",
                 "training/runs/phase8-qwen3-4b-lora-v1/train/run-manifest.json",
                 "training/runs/phase9-eval-inputs/test-eligible-with-targets.jsonl", "training/runs/runpod/state.json"):
        assert not ok(name), name
    assert L.PROFILES["phase9-eval"]["max_cost_per_hr"] * L.PROFILES["phase9-eval"]["hard_limit_seconds"] / 3600 <= 1.20


@pytest.mark.parametrize("pod_removes_itself", [True, False])
def test_self_delete_proof_logic(tmp_path, monkeypatch, pod_removes_itself):
    L = _launcher()
    clock = {"t": 1000.0}
    monkeypatch.setattr(L, "now", lambda: clock["t"])
    monkeypatch.setattr(L.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(L, "STATE_DIR", tmp_path)
    monkeypatch.setattr(L, "EVENTS", tmp_path / "events.jsonl")
    monkeypatch.setattr(L, "balance", lambda: {"clientBalance": 9.0})
    monkeypatch.setattr(L, "start_local_watchdog", lambda pod, at: 999999)
    monkeypatch.setattr(L.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(L, "ssh", lambda *a, **k: type("R", (), {"stdout": "armed ... Unauthorized"})())
    pods = {"p1": {"id": "p1", "name": "gtmflow-proof-x", "costPerHr": 0.49, "publicIp": "1.2.3.4",
                   "portMappings": {"22": 2222}}}
    created = {}
    def create_pod(allow, name_prefix=None, hard_limit=None, claim_limit=None):
        created.update(name_prefix=name_prefix, hard_limit=hard_limit)
        return pods["p1"]
    def get_pod(pid):  # the pod's own watchdog removes it ~150 s after creation (if it can)
        if pod_removes_itself and clock["t"] > 1150:
            pods.pop(pid, None)
        return pods.get(pid)
    terminated = []
    monkeypatch.setattr(L, "create_pod", create_pod)
    monkeypatch.setattr(L, "get_pod", get_pod)
    monkeypatch.setattr(L, "terminate", lambda pid, reason: terminated.append(pid) or pods.pop(pid, None) or True)
    monkeypatch.setattr(L, "list_pods", lambda: [])  # nothing else running before/after
    rc = L.cmd_prove_self_delete(type("A", (), {"confirm_paid_compute": True})())
    record = json.loads((tmp_path / "self-delete-proof.json").read_text())
    assert created == {"name_prefix": "gtmflow-proof-", "hard_limit": L.PROOF_LIMIT_SECONDS}
    if pod_removes_itself:
        assert rc == 0 and record["self_delete_proven"] and terminated == []  # nothing but the pod removed it
    else:
        assert rc == 1 and not record["self_delete_proven"] and terminated == ["p1"]  # account key cleaned up
        assert record["watchdog_log_on_failure"] and clock["t"] >= 1000 + L.PROOF_WINDOW_SECONDS
    assert record["removal_confirmed"]


def test_each_profile_keeps_its_own_state_and_unfinished_runs_block_new_ones(tmp_path, monkeypatch):
    L = _launcher()
    monkeypatch.setattr(L, "STATE_DIR", tmp_path)
    monkeypatch.setattr(L, "EVENTS", tmp_path / "events.jsonl")
    phase8 = tmp_path / "state.json"
    phase8.write_text(json.dumps({"pod_id": "old", "finished": True}))
    before = phase8.read_bytes()
    monkeypatch.setattr(L, "P", L.PROFILES["phase9-eval"])
    L.save_state({"pod_id": "new"})
    assert phase8.read_bytes() == before  # the Phase 8 record is never overwritten
    assert json.loads((tmp_path / "state-phase9-eval-v1.json").read_text()) == {"pod_id": "new"}
    assert L.unfinished_profiles() == ["phase9-eval"]
    monkeypatch.setattr(L, "P", L.PROFILES["phase8-train"])
    monkeypatch.setattr(L, "list_pods", lambda: pytest.fail("refusal must come before any API call"))
    args = type("A", (), {"confirm_paid_compute": True, "bundle_sha256": "x", "profile": "phase8-train",
                          "allow_rtx4090": False})()
    assert L.cmd_run(args) == 2


# ------------------------------------------------------ Phase 10 acceptance

def test_phase10_accept_bundle_allowlist_carries_no_data_and_matches_the_pod_script():
    import re

    L = _launcher()
    job = (Path(__file__).resolve().parents[1] / "scripts" / "runpod" / "pod_serve_job.sh").read_text()
    assert f'PHASE10_ALLOWED = r"{L.PHASE10_ALLOWED}"' in job and f'PHASE10_FORBIDDEN = r"{L.PHASE10_FORBIDDEN}"' in job
    ok = lambda n: bool(re.match(L.PHASE10_ALLOWED, n)) and not re.search(L.PHASE10_FORBIDDEN, n, re.I)
    for name in ("BUNDLE_COMMIT", "training/gtmflow_training/serve.py", "training/configs/phase9-eval-v1.json",
                 "backend/app/ai/prompts.py",
                 "training/runs/phase8-qwen3-4b-lora-v1/train/best_adapter/adapter_model.safetensors",
                 "training/runs/phase8-qwen3-4b-lora-v1/train/best_adapter/adapter_config.json"):
        assert ok(name), name
    for name in ("training/runs/phase9-eval-inputs/test-eligible.jsonl", "backend/data/datasets/test-v1/eligible.jsonl",
                 "training/runs/phase9-eval-v1/run/predictions/qwen3-4b-lora-v1.jsonl", "backend/.env",
                 "backend/app/core/config.py", "training/runs/phase8-qwen3-4b-lora-v1/train/tokenizer/tokenizer.json",
                 "training/runs/runpod/state.json", "training/tests/fixtures/sample.jsonl"):
        assert not ok(name), name
    prof = L.PROFILES["phase10-accept"]
    assert prof["max_cost_per_hr"] * prof["hard_limit_seconds"] / 3600 <= 0.75
    assert prof["state_file"] not in (L.PROFILES["phase8-train"]["state_file"], L.PROFILES["phase9-eval"]["state_file"])


def test_serving_status_runs_the_acceptance_once_then_requests_a_stop(tmp_path, monkeypatch):
    L = _launcher()
    monkeypatch.setattr(L, "STATE_DIR", tmp_path)
    monkeypatch.setattr(L, "EVENTS", tmp_path / "events.jsonl")
    monkeypatch.setattr(L, "P", L.PROFILES["phase10-accept"])
    monkeypatch.setattr(L.time, "sleep", lambda s: None)
    statuses = iter(["running:load", "serving", "serving", "running:package", "done"])
    commands = []

    def fake_ssh(state, command, timeout=300, check=True, input_text=None):
        commands.append(command)
        out = f"{next(statuses)}\n---\n" if command.startswith("cat /workspace/job/status") else ""
        return type("R", (), {"returncode": 0, "stdout": out, "stderr": ""})()

    runs = []
    monkeypatch.setattr(L, "ssh", fake_ssh)
    monkeypatch.setattr(L, "run_acceptance", lambda state: runs.append(1) or {"passed": True, "exit_code": 0})
    state = {"pod_id": "p", "hard_limit_at": L.now() + 3600}
    assert L.poll(state) == "done"
    assert runs == [1] and state["acceptance"]["passed"] is True
    assert commands.count("touch /workspace/job/stop") == 1


def test_serve_accepts_only_greedy_bounded_requests_for_the_served_model():
    from gtmflow_training import serve as S

    class FakeGen:
        def complete(self, messages, max_tokens):
            return {"text": '{"reply": "REPLY-MARKER-456"}', "finish": "eos", "prompt_tokens": 10, "new_tokens": 4, "seconds": 0.1}

    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "PROMPT-MARKER-123"}]
    base = {"model": "qwen3-4b-lora-v1", "messages": msgs, "temperature": 0, "max_tokens": 1024}
    status, body, stats = S.handle_chat(FakeGen(), base)
    assert status == 200 and body["choices"][0]["finish_reason"] == "stop" and body["usage"]["completion_tokens"] == 4
    assert "PROMPT-MARKER" not in json.dumps(stats) and "REPLY-MARKER" not in json.dumps(stats)  # no text in the log
    for change, code in (({"model": "Qwen/Qwen3-4B-Instruct-2507"}, 404), ({"temperature": 0.7}, 400), ({"n": 2}, 400),
                         ({"max_tokens": 4096}, 400), ({"messages": [{"role": "tool", "content": "x"}]}, 400)):
        assert S.handle_chat(FakeGen(), {**base, **change})[0] == code, change
