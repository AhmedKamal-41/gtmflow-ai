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
