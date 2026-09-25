"""Dataset loading with split guards, and production-identical formatting.

Guards (all raise DataGuardError before any model is loaded):
* every pinned file must match its sha256 in the config;
* roles are "train" and "validation" only -- a test split is refused
  everywhere in this package (reserved for final evaluation);
* the train set must be an all-train dataset whose manifest allows training;
  the validation set must be all-validation;
* no uncertain-flagged example may appear (they are excluded upstream);
* every example must come from the pinned prompt version.

Formatting uses the backend's own prompt builders and system message
(app.ai.prompts), so the fine-tuned model sees exactly the input the real
client sends. The target is the reviewed JSON with sorted keys. Loss is
computed on the assistant tokens only.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"


def _load_prompts():
    """Load backend/app/ai/prompts.py by file path. Importing the `app.ai`
    package would run its __init__, which loads the backend settings and
    .env (API keys); training must never touch those. prompts.py itself
    imports only the standard library."""
    spec = importlib.util.spec_from_file_location("gtmflow_backend_prompts", BACKEND_DIR / "app" / "ai" / "prompts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_prompts = _load_prompts()
JSON_SYSTEM_MESSAGE = _prompts.JSON_SYSTEM_MESSAGE
PROMPT_VERSION = _prompts.PROMPT_VERSION
build_outreach_prompt = _prompts.build_outreach_prompt
build_summary_prompt = _prompts.build_summary_prompt

ROLES = ("train", "validation")


class DataGuardError(Exception):
    pass


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_split(spec: dict[str, Any], role: str) -> list[dict[str, Any]]:
    if role not in ROLES:
        raise DataGuardError(f"role '{role}' refused: test data is reserved for the final evaluation")
    directory = Path(spec["dir"])
    for name, expected in spec["sha256"].items():
        actual = sha256_file(directory / name)
        if actual != expected:
            raise DataGuardError(f"{role}: {name} sha256 {actual[:12]}... does not match the pinned {expected[:12]}...")
    with open(directory / "dataset-manifest.json") as f:
        manifest = json.load(f)
    with open(directory / "eligible.jsonl") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    if not rows:
        raise DataGuardError(f"{role}: no examples")
    splits = {r["split"] for r in rows}
    if "test" in splits:
        raise DataGuardError(f"{role}: contains test examples; test data is reserved for the final evaluation")
    if role == "train" and (manifest.get("allowed_use") != "training" or splits != {"train"}):
        raise DataGuardError("train: dataset is not an all-train dataset allowed for training")
    if role == "validation" and splits != {"validation"}:
        raise DataGuardError("validation: dataset is not all-validation")
    if any(r.get("uncertain") for r in rows):
        raise DataGuardError(f"{role}: contains uncertain-flagged examples")
    versions = {r.get("prompt_version") for r in rows}
    if versions != {PROMPT_VERSION}:
        raise DataGuardError(f"{role}: prompt versions {sorted(map(str, versions))}, expected {PROMPT_VERSION}")
    if role == "train":
        bad = [r["example_id"] for r in rows if not (0 < float(r.get("weight", 0)) <= 1)]
        if bad:
            raise DataGuardError(f"train: {len(bad)} examples lack a weight in (0, 1]")
    return rows


def build_messages(example: dict[str, Any]) -> tuple[list[dict[str, str]], str]:
    ctx = example["input_snapshot"]
    if example["task"] == "company_summary":
        user = build_summary_prompt(ctx)
    elif example["task"] == "outreach_email":
        user = build_outreach_prompt(ctx)
    else:
        raise DataGuardError(f"unknown task {example['task']}")
    prompt = [{"role": "system", "content": JSON_SYSTEM_MESSAGE}, {"role": "user", "content": user}]
    target = json.dumps(example["target"], ensure_ascii=False, sort_keys=True)
    return prompt, target


def encode(example: dict[str, Any], tokenizer, max_seq_len: int) -> dict[str, Any]:
    """Token ids plus labels masked (-100) over the prompt. Never truncates:
    an example longer than max_seq_len is an error, not silently cut."""
    prompt, target = build_messages(example)
    prompt_text = tokenizer.apply_chat_template(prompt, tokenize=False, add_generation_prompt=True)
    full_text = tokenizer.apply_chat_template(prompt + [{"role": "assistant", "content": target}], tokenize=False)
    if not full_text.startswith(prompt_text):
        raise DataGuardError("chat template: the full conversation does not extend the prompt")
    prompt_ids = tokenizer(prompt_text, add_special_tokens=False)["input_ids"]
    full_ids = tokenizer(full_text, add_special_tokens=False)["input_ids"]
    if full_ids[: len(prompt_ids)] != prompt_ids:
        raise DataGuardError("tokenization: prompt tokens change when the answer is appended")
    if len(full_ids) > max_seq_len:
        raise DataGuardError(f"example {example['example_id']} has {len(full_ids)} tokens > max_seq_len {max_seq_len}")
    labels = [-100] * len(prompt_ids) + full_ids[len(prompt_ids):]
    return {
        "example_id": example["example_id"],
        "task": example["task"],
        "input_ids": full_ids,
        "labels": labels,
        "weight": float(example.get("weight", 1.0)),
        "prompt_tokens": len(prompt_ids),
        "target_tokens": len(full_ids) - len(prompt_ids),
    }


def resolve(path: str, base: str | Path) -> str:
    return path if os.path.isabs(path) else str((Path(base) / path).resolve())
