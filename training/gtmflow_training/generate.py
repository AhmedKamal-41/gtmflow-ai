"""Phase 9 generation: pinned Qwen base vs. the Phase 8 LoRA adapter on the
frozen held-out inputs.

    python -m gtmflow_training.generate plan  --config configs/phase9-eval-v1.json
    python -m gtmflow_training.generate smoke --config configs/phase9-eval-v1.json
    python -m gtmflow_training.generate run   --config configs/phase9-eval-v1.json --confirm-paid-compute

plan  -- verifies the pinned inputs and adapter hashes and counts prompt
         tokens. No model weights. Free.
smoke -- the full generation path on CPU with a tiny random model of the base
         architecture and a tiny random adapter (real tokenizer, real prompts,
         real batching, adapter on/off). Proves the code path, not quality.
run   -- the real generation on a CUDA GPU. Refuses without
         --confirm-paid-compute.

Inputs are inputs-only files (data.load_eval_inputs refuses anything that
carries references, stored predictions or review data); scoring happens in
the workspace with the frozen Phase 7 metrics. One base model is loaded and
both systems use it: `qwen3-4b-base` runs with the adapter disabled,
`qwen3-4b-lora-v1` with it enabled -- same weights, tokenizer, prompts,
batches and greedy decoding. Nothing is sampled. Every input gets a
prediction record, including empty and truncated outputs; a pass skipped by
the deadline or the validation gate is recorded as not generated.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import torch

from gtmflow_training import data as D
from gtmflow_training import train as T

SYSTEMS = ("qwen3-4b-base", "qwen3-4b-lora-v1")


def load_config(path: str) -> dict[str, Any]:
    cfg = json.loads(Path(path).read_text())
    base = Path(path).resolve().parent
    cfg["adapter"]["dir"] = D.resolve(cfg["adapter"]["dir"], base)
    cfg["base_model"]["cache_dir"] = D.resolve(cfg["base_model"]["cache_dir"], base)
    cfg["output_dir"] = D.resolve(cfg["output_dir"], base)
    for spec in cfg["inputs"].values():
        spec["path"] = D.resolve(spec["path"], base)
    cfg["_config_path"] = str(Path(path).resolve())
    cfg["_config_sha256"] = D.sha256_file(path)
    return cfg


def verify_adapter(adapter_dir: str | Path, pinned: dict[str, str]) -> dict[str, str]:
    actual = {name: D.sha256_file(Path(adapter_dir) / name) for name in pinned}
    bad = [n for n in pinned if actual[n] != pinned[n]]
    if bad:
        raise D.DataGuardError(f"adapter files do not match the pinned sha256: {bad}")
    return actual


def load_inputs(cfg: dict[str, Any], limit: int | None = None) -> dict[str, list[dict[str, Any]]]:
    out = {}
    for name in cfg["pass_order"]:
        rows = D.load_eval_inputs(cfg["inputs"][name])
        out[name] = rows[:limit] if limit else rows
    return out


def prompt_text(example: dict[str, Any], tokenizer) -> str:
    return tokenizer.apply_chat_template(D.prompt_messages(example), tokenize=False, add_generation_prompt=True)


def batches(rows: list[dict[str, Any]], tokenizer, size: int) -> list[list[tuple[dict[str, Any], str, int]]]:
    """Deterministic batches: ascending prompt length, then example_id."""
    items = []
    for r in rows:
        text = prompt_text(r, tokenizer)
        items.append((r, text, len(tokenizer(text, add_special_tokens=False)["input_ids"])))
    items.sort(key=lambda it: (it[2], it[0]["example_id"]))
    return [items[i:i + size] for i in range(0, len(items), size)]


def eos_ids(model, tokenizer) -> list[int]:
    ids = getattr(model.generation_config, "eos_token_id", None) or tokenizer.eos_token_id
    return sorted(set(ids if isinstance(ids, list) else [ids]))


@torch.no_grad()
def generate_batch(model, tokenizer, batch, max_new_tokens: int, stop_ids: list[int], device: str):
    from transformers import GenerationConfig
    enc = tokenizer([text for _, text, _ in batch], return_tensors="pt", padding=True,
                    add_special_tokens=False).to(device)
    gen_cfg = GenerationConfig(do_sample=False, num_beams=1, max_new_tokens=max_new_tokens,
                               eos_token_id=stop_ids, pad_token_id=tokenizer.pad_token_id)
    out = model.generate(**enc, generation_config=gen_cfg)
    new = out[:, enc["input_ids"].shape[1]:]
    records = []
    for (row, _, prompt_tokens), seq in zip(batch, new.tolist()):
        stop_at = next((i for i, t in enumerate(seq) if t in stop_ids), None)
        tokens = seq if stop_at is None else seq[:stop_at]
        records.append({
            "example_id": row["example_id"], "split": row["split"], "subset": row["subset"], "task": row["task"],
            "text": tokenizer.decode(tokens, skip_special_tokens=True),
            "finish": "eos" if stop_at is not None else "length",
            "prompt_tokens": prompt_tokens, "new_tokens": len(tokens),
        })
    return records


def load_systems(cfg: dict[str, Any], tiny: bool, adapter_dir: str | Path):
    """One base model with the adapter attached; returns (peft_model, device)."""
    from peft import PeftModel
    model = T.load_model(cfg, tiny=tiny)
    model.eval()
    peft_model = PeftModel.from_pretrained(model, str(adapter_dir), is_trainable=False)
    peft_model.eval()
    return peft_model, ("cpu" if tiny else "cuda")


def run_pass(peft_model, tokenizer, rows, system: str, gen: dict[str, Any], device: str, log) -> tuple[list, float]:
    started = time.time()
    records = []
    stop = eos_ids(peft_model, tokenizer)
    for batch in batches(rows, tokenizer, gen["batch_size"]):
        if system == "qwen3-4b-base":
            with peft_model.disable_adapter():
                out = generate_batch(peft_model, tokenizer, batch, gen["max_new_tokens"], stop, device)
        else:
            out = generate_batch(peft_model, tokenizer, batch, gen["max_new_tokens"], stop, device)
        records.extend({"system": system, **r} for r in out)
    seconds = time.time() - started
    return records, seconds


def gate(records: dict[str, list[dict[str, Any]]], cfg: dict[str, Any]) -> dict[str, Any]:
    g = cfg["validation_gate"]
    rates = {}
    for system, recs in records.items():
        n = max(1, len(recs))
        rates[system] = {"truncated": sum(r["finish"] == "length" for r in recs) / n,
                         "empty": sum(not r["text"].strip() for r in recs) / n, "n": len(recs)}
    ok = (rates["qwen3-4b-lora-v1"]["truncated"] <= g["max_lora_truncated_rate"]
          and all(v["empty"] <= g["max_empty_rate"] for v in rates.values()))
    return {"passed": ok, "rates": rates, "thresholds": {k: v for k, v in g.items() if k.startswith("max_")}}


def run(cfg: dict[str, Any], mode: str, out_dir: Path, deadline: float | None = None,
        reserve: float = 0.0, enforce_gate: bool | None = None) -> dict[str, Any]:
    """enforce_gate defaults to True for `run`; smoke records the gate but
    continues, so every pass of the code path is exercised."""
    enforce_gate = (mode == "run") if enforce_gate is None else enforce_gate
    started = time.time()
    T.seed_everything(cfg["seed"])
    out_dir.mkdir(parents=True, exist_ok=True)
    log_file = open(out_dir / "log.jsonl", "w")

    def log(record: dict[str, Any]) -> None:
        log_file.write(json.dumps(record) + "\n")
        log_file.flush()
        print(json.dumps(record), flush=True)

    tokenizer = T.load_tokenizer(cfg)
    tokenizer.padding_side = "left"
    tiny = mode == "smoke"
    inputs = load_inputs(cfg, limit=cfg["smoke"]["examples"] if tiny else None)
    manifest: dict[str, Any] = {
        "mode": mode, "started_at": datetime.now(timezone.utc).isoformat(), "command": sys.argv,
        "config": {k: v for k, v in cfg.items() if not k.startswith("_")},
        "config_sha256": cfg["_config_sha256"],
        "inputs_sha256": {k: v["sha256"] for k, v in cfg["inputs"].items()},
        "inputs_examples": {k: len(v) for k, v in inputs.items()},
        "environment": T.environment(), "git": T._git(), "seed": cfg["seed"],
        "note": "inputs only on this machine; references and scoring stay in the workspace",
    }
    gen = dict(cfg["generation"])
    if tiny:
        gen.update(max_new_tokens=cfg["smoke"]["max_new_tokens"], batch_size=cfg["smoke"]["batch_size"])
    if mode == "plan":
        manifest["adapter_sha256"] = verify_adapter(cfg["adapter"]["dir"], cfg["adapter"]["sha256"])
        manifest["prompt_tokens"] = {
            name: {"examples": len(rows), "batches": math.ceil(len(rows) / gen["batch_size"]),
                   "max": max(len(tokenizer(prompt_text(r, tokenizer), add_special_tokens=False)["input_ids"])
                              for r in rows)}
            for name, rows in inputs.items()}
        return _finish(manifest, out_dir, "plan.json", started, log_file)

    if tiny:
        adapter_dir = _tiny_adapter(cfg, Path(tempfile.mkdtemp()))
        manifest["adapter_sha256"] = {"tiny random adapter (smoke)": D.sha256_file(adapter_dir / "adapter_model.safetensors")}
    else:
        adapter_dir = Path(cfg["adapter"]["dir"])
        manifest["adapter_sha256"] = verify_adapter(adapter_dir, cfg["adapter"]["sha256"])
        torch.cuda.reset_peak_memory_stats()
    peft_model, device = load_systems(cfg, tiny, adapter_dir)
    pred_dir = out_dir / "predictions"
    pred_dir.mkdir(exist_ok=True)
    files = {s: open(pred_dir / f"{s}.jsonl", "w") for s in SYSTEMS}
    passes: list[dict[str, Any]] = []
    last_rate = None  # seconds per example of the previous pass
    stop_reason = None
    for name in cfg["pass_order"]:
        rows = inputs[name]
        if name == cfg["validation_gate"]["applies_before"]:
            val = {s: [r for p in passes if p["name"] == "validation-eligible" and p["system"] == s for r in p["records"]]
                   for s in SYSTEMS}
            manifest["validation_gate"] = gate(val, cfg)
            log({"type": "validation_gate", **manifest["validation_gate"]})
            manifest["validation_gate"]["enforced"] = enforce_gate
            if enforce_gate and not manifest["validation_gate"]["passed"]:
                stop_reason = "validation gate failed"
        for system in SYSTEMS:
            if stop_reason is None and deadline is not None and last_rate is not None \
                    and time.time() + 1.2 * last_rate * len(rows) + reserve > deadline:
                stop_reason = "deadline"
            if stop_reason is not None:
                passes.append({"name": name, "system": system, "generated": False, "reason": stop_reason,
                               "examples": len(rows), "records": []})
                log({"type": "pass_skipped", "pass": name, "system": system, "reason": stop_reason})
                continue
            records, seconds = run_pass(peft_model, tokenizer, rows, system, gen, device, log)
            for r in records:
                files[system].write(json.dumps({"pass": name, **r}, ensure_ascii=False, sort_keys=True) + "\n")
                files[system].flush()
            last_rate = seconds / max(1, len(rows))
            passes.append({"name": name, "system": system, "generated": True, "examples": len(rows),
                           "records": records, "seconds": round(seconds, 1)})
            log({"type": "pass", "pass": name, "system": system, "examples": len(rows), "seconds": round(seconds, 1),
                 "truncated": sum(r["finish"] == "length" for r in records),
                 "empty": sum(not r["text"].strip() for r in records),
                 "new_tokens_mean": round(sum(r["new_tokens"] for r in records) / max(1, len(records)), 1)})
    for f in files.values():
        f.close()
    manifest["passes"] = [{k: v for k, v in p.items() if k != "records"} for p in passes]
    manifest["stopped"] = stop_reason
    manifest["predictions_sha256"] = {f"{s}.jsonl": D.sha256_file(pred_dir / f"{s}.jsonl") for s in SYSTEMS}
    if device == "cuda":
        manifest["gpu_memory"] = T.gpu_memory(device)
    return _finish(manifest, out_dir, "run-manifest.json", started, log_file)


def _tiny_adapter(cfg: dict[str, Any], where: Path) -> Path:
    """A random (non-zero) LoRA adapter for the tiny smoke model."""
    from peft import LoraConfig, get_peft_model
    model = T.load_model(cfg, tiny=True)
    lc = cfg["smoke"]["lora"]
    torch.manual_seed(cfg["seed"] + 1)
    peft_model = get_peft_model(model, LoraConfig(r=lc["r"], lora_alpha=lc["alpha"], target_modules=lc["target_modules"],
                                                  init_lora_weights=False, task_type="CAUSAL_LM"))
    peft_model.save_pretrained(where)
    return where


def _finish(manifest, out_dir: Path, name: str, started: float, log_file) -> dict[str, Any]:
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    manifest["wall_seconds"] = round(time.time() - started, 1)
    (out_dir / name).write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str))
    log_file.close()
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gtmflow_training.generate")
    parser.add_argument("mode", choices=("plan", "smoke", "run"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", help="output directory (default: config output_dir/<mode>)")
    parser.add_argument("--confirm-paid-compute", action="store_true")
    parser.add_argument("--deadline-unix", type=float, help="run: skip passes that cannot finish before this time")
    parser.add_argument("--deadline-reserve-seconds", type=float, default=600)
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    if args.mode == "run":
        if not args.confirm_paid_compute:
            print("refused: 'run' needs --confirm-paid-compute (paid GPU run requires explicit approval)", file=sys.stderr)
            return 2
        if not torch.cuda.is_available():
            print("refused: 'run' needs a CUDA GPU", file=sys.stderr)
            return 2
        if torch.cuda.get_device_properties(0).total_memory / 2**30 < cfg["hardware"]["min_gpu_memory_gib"] \
                or not torch.cuda.is_bf16_supported():
            print("refused: needs a bf16-capable GPU with enough memory", file=sys.stderr)
            return 2
    out_dir = Path(args.out) if args.out else Path(cfg["output_dir"]) / args.mode
    try:
        manifest = run(cfg, args.mode, out_dir, args.deadline_unix, args.deadline_reserve_seconds)
    except D.DataGuardError as error:
        print(f"data guard: {error}", file=sys.stderr)
        return 3
    print(json.dumps({k: manifest.get(k) for k in ("mode", "inputs_examples", "prompt_tokens", "passes",
                                                   "validation_gate", "stopped", "gpu_memory", "wall_seconds")},
                     indent=2, default=str))
    if args.mode == "run" and manifest.get("stopped") == "validation gate failed":
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
