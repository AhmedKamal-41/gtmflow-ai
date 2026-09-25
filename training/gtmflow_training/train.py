"""Phase 8 LoRA runner.

    python -m gtmflow_training.train plan  --config configs/phase8-qwen3-4b-lora-v1.json
    python -m gtmflow_training.train smoke --config configs/phase8-qwen3-4b-lora-v1.json
    python -m gtmflow_training.train train --config configs/phase8-qwen3-4b-lora-v1.json --confirm-paid-compute

plan   -- verifies and loads the pinned datasets, tokenizes every example
          with the pinned tokenizer, reports token counts and steps. No
          model weights are loaded. Free.
smoke  -- the full pipeline on CPU with a tiny randomly initialized model of
          the base architecture (real tokenizer, real formatting, real
          weighting, real LoRA, real checkpoint selection) on a few examples.
          Free; proves the code path, not model quality.
preflight -- brief GPU check with the real pinned model: loads it, adds
          LoRA, runs forward+backward on the longest training example and a
          forward pass on the longest validation example, and reports peak
          GPU memory and a projected run time. No optimizer step, nothing
          saved but preflight.json. Refuses without --confirm-paid-compute
          and a CUDA GPU.
train  -- the real run on the pinned base model. Refuses to start without
          --confirm-paid-compute and a CUDA GPU. With --deadline-unix it
          stops after the last epoch that can finish (plus a reserve) before
          that time, keeping the best adapter so far.

Fitting uses the train split only (example-weighted); validation is used
only for tuning (per-epoch loss, best-checkpoint selection, early
stopping); test data is never loaded (data.load_split refuses it).
Every run writes run-manifest.json: config and data hashes, pinned model
revision, library versions, git commit, hardware, seed, per-step and
per-epoch metrics, and wall-clock time.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import torch

from gtmflow_training import data as D
from gtmflow_training import weights as W

REPO_ROOT = Path(__file__).resolve().parents[2]


# ----------------------------------------------------------------- config

def load_config(path: str) -> dict[str, Any]:
    with open(path, "rb") as f:
        raw = f.read()
    cfg = json.loads(raw)
    base = Path(path).resolve().parent
    for role in ("train", "validation"):
        cfg["data"][role]["dir"] = D.resolve(cfg["data"][role]["dir"], base)
    cfg["output_dir"] = D.resolve(cfg["output_dir"], base)
    cfg["base_model"]["cache_dir"] = D.resolve(cfg["base_model"].get("cache_dir", "../.cache/hf"), base)
    if "test" in cfg["data"]:
        raise D.DataGuardError("config names a test dataset; test data is reserved for the final evaluation")
    if cfg["training"]["per_device_batch_size"] != 1:
        raise ValueError("this runner feeds one example per micro-batch (per_device_batch_size must be 1)")
    cfg["_config_path"] = str(Path(path).resolve())
    cfg["_config_sha256"] = D.sha256_file(path)
    return cfg


def seed_everything(seed: int) -> None:
    random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")


# ------------------------------------------------------------ provenance

def _git() -> dict[str, Any]:
    def run(*args):
        try:
            return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.strip()
        except Exception:  # noqa: BLE001
            return None
    commit = run("rev-parse", "HEAD")
    if commit is None and (REPO_ROOT / "BUNDLE_COMMIT").exists():
        # Running from a make_bundle.sh bundle (git archive of a clean commit).
        return {"commit": (REPO_ROOT / "BUNDLE_COMMIT").read_text().strip(), "dirty": False, "source": "bundle"}
    status = run("status", "--porcelain")
    return {"commit": commit, "dirty": bool(status) if status is not None else None, "source": "git"}


def environment() -> dict[str, Any]:
    import accelerate
    import peft
    import transformers
    env = {
        "python": sys.version.split()[0], "platform": platform.platform(),
        "torch": torch.__version__, "transformers": transformers.__version__,
        "peft": peft.__version__, "accelerate": accelerate.__version__,
        "cuda_available": torch.cuda.is_available(), "cpu_count": os.cpu_count(),
    }
    if torch.cuda.is_available():
        env.update({"cuda": torch.version.cuda, "gpu": torch.cuda.get_device_name(0),
                    "gpu_memory_gb": round(torch.cuda.get_device_properties(0).total_memory / 2**30, 1)})
    return env


# ----------------------------------------------------------------- model

def load_tokenizer(cfg: dict[str, Any]):
    from transformers import AutoTokenizer
    bm = cfg["base_model"]
    return AutoTokenizer.from_pretrained(bm["repo"], revision=bm["revision"], cache_dir=bm["cache_dir"])


def load_model(cfg: dict[str, Any], tiny: bool):
    from transformers import AutoConfig, AutoModelForCausalLM
    bm = cfg["base_model"]
    if tiny:
        mc = AutoConfig.from_pretrained(bm["repo"], revision=bm["revision"], cache_dir=bm["cache_dir"])
        for key, value in cfg["smoke"]["tiny_model"].items():
            setattr(mc, key, value)
        if getattr(mc, "layer_types", None):
            mc.layer_types = mc.layer_types[: mc.num_hidden_layers]
        torch.manual_seed(cfg["seed"])
        model = AutoModelForCausalLM.from_config(mc, dtype=torch.float32)
    else:
        model = AutoModelForCausalLM.from_pretrained(
            bm["repo"], revision=bm["revision"], cache_dir=bm["cache_dir"],
            dtype=getattr(torch, bm["dtype"]), attn_implementation=bm["attn_implementation"])
        model.to("cuda")
    model.config.use_cache = False
    return model


def add_lora(model, cfg: dict[str, Any]):
    from peft import LoraConfig, get_peft_model
    lc = cfg["lora"]
    peft_model = get_peft_model(model, LoraConfig(
        r=lc["r"], lora_alpha=lc["alpha"], lora_dropout=lc["dropout"],
        target_modules=lc["target_modules"], bias=lc["bias"], task_type="CAUSAL_LM"))
    if cfg["training"]["gradient_checkpointing"]:
        peft_model.get_base_model().gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    return peft_model


def example_loss(peft_model, enc: dict[str, Any], device: str) -> torch.Tensor:
    """Mean CE over the answer tokens. Logits are computed only at the
    positions that predict answer tokens (the prompt needs none), which
    keeps memory flat for a 152k-token vocabulary."""
    base = peft_model.get_base_model()
    ids = torch.tensor([enc["input_ids"]], device=device)
    labels = torch.tensor(enc["labels"], device=device)
    hidden = base.model(input_ids=ids, use_cache=False).last_hidden_state[0]  # [T, H]
    next_labels = labels[1:]
    mask = next_labels != -100
    logits = base.lm_head(hidden[:-1][mask])
    return W.answer_token_loss(logits, next_labels[mask])


def lr_lambda(total: int, warmup: int):
    def f(step: int) -> float:
        if step < warmup:
            return (step + 1) / max(1, warmup)
        progress = (step - warmup) / max(1, total - warmup)
        return 0.5 * (1.0 + math.cos(math.pi * min(1.0, progress)))
    return f


@torch.no_grad()
def evaluate(peft_model, encoded: list[dict[str, Any]], device: str) -> dict[str, float]:
    peft_model.eval()
    losses, tokens = [], 0
    weighted_sum = 0.0
    for enc in encoded:
        loss = example_loss(peft_model, enc, device).item()
        losses.append(loss)
        tokens += enc["target_tokens"]
        weighted_sum += loss * enc["target_tokens"]
    peft_model.train()
    return {"loss": sum(losses) / len(losses), "token_weighted_loss": weighted_sum / max(1, tokens), "examples": len(losses)}


# ------------------------------------------------------------------- run

def prepare_data(cfg: dict[str, Any], tokenizer, limit_train: int | None = None, limit_val: int | None = None):
    train_rows = D.load_split(cfg["data"]["train"], "train")
    val_rows = D.load_split(cfg["data"]["validation"], "validation")
    if limit_train:
        train_rows = smoke_subset(train_rows, limit_train)
    if limit_val:
        val_rows = val_rows[:limit_val]
    max_len = cfg["training"]["max_seq_len"]
    return ([D.encode(r, tokenizer, max_len) for r in train_rows],
            [D.encode(r, tokenizer, max_len) for r in val_rows])


def smoke_subset(rows: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """Deterministic smoke subset that always includes down-weighted examples
    (so the weighting path is exercised) and both tasks."""
    low = [r for r in rows if float(r["weight"]) < 1]
    full = [r for r in rows if float(r["weight"]) >= 1]
    half = n // 2
    return low[:half] + full[: n - min(half, len(low))]


def stats(encoded: list[dict[str, Any]]) -> dict[str, Any]:
    lengths = sorted(len(e["input_ids"]) for e in encoded)
    return {
        "examples": len(encoded),
        "by_task": {t: sum(1 for e in encoded if e["task"] == t) for t in sorted({e["task"] for e in encoded})},
        "total_tokens": sum(lengths), "answer_tokens": sum(e["target_tokens"] for e in encoded),
        "max_tokens": lengths[-1], "median_tokens": lengths[len(lengths) // 2],
        "weight_sum": round(sum(e["weight"] for e in encoded), 3),
    }


def train_loop(cfg: dict[str, Any], train_enc, val_enc, peft_model, device: str, out_dir: Path, log) -> dict[str, Any]:
    tc = cfg["training"]
    accum = tc["gradient_accumulation_steps"]
    use_weights = cfg["data"]["use_example_weights"]
    weights = [e["weight"] if use_weights else 1.0 for e in train_enc]
    normalizer = W.weight_normalizer(weights)
    steps_per_epoch = math.ceil(len(train_enc) / accum)
    total_steps = steps_per_epoch * tc["epochs"]
    params = [p for p in peft_model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=tc["learning_rate"], betas=tuple(tc["adam_betas"]),
                                  eps=tc["adam_eps"], weight_decay=tc["weight_decay"])
    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda(total_steps, int(tc["warmup_ratio"] * total_steps)))
    selection = cfg["selection"]
    best, best_epoch, bad_epochs, history, step = math.inf, None, 0, [], 0
    deadline, reserve = cfg.get("_deadline_unix"), cfg.get("_deadline_reserve_seconds", 0)
    stopped_for_deadline, last_epoch_seconds = False, None
    peft_model.train()
    for epoch in range(1, tc["epochs"] + 1):
        if deadline is not None and last_epoch_seconds is not None and time.time() + 1.1 * last_epoch_seconds + reserve > deadline:
            log({"type": "deadline_stop", "before_epoch": epoch, "last_epoch_seconds": round(last_epoch_seconds, 1)})
            stopped_for_deadline = True
            break
        epoch_started = time.time()
        sum_wl = sum_w = sum_l = 0.0
        order = torch.randperm(len(train_enc), generator=torch.Generator().manual_seed(cfg["seed"] + epoch)).tolist()
        groups = [order[i:i + accum] for i in range(0, len(order), accum)]
        for group in groups:
            optimizer.zero_grad(set_to_none=True)
            step_loss = 0.0
            for idx in group:
                enc = train_enc[idx]
                raw = example_loss(peft_model, enc, device)
                raw_value = raw.item()
                sum_wl += weights[idx] * raw_value
                sum_w += weights[idx]
                sum_l += raw_value
                loss = W.weighted_loss(raw, weights[idx], normalizer) / len(group)
                loss.backward()
                step_loss += loss.item()
            grad_norm = torch.nn.utils.clip_grad_norm_(params, tc["max_grad_norm"]).item()
            optimizer.step()
            scheduler.step()
            step += 1
            log({"type": "step", "epoch": epoch, "step": step, "weighted_train_loss": round(step_loss, 6),
                 "lr": scheduler.get_last_lr()[0], "grad_norm": round(grad_norm, 4)})
        train_losses = {"weighted_objective": sum_wl / sum_w, "unweighted_mean": sum_l / len(order)}
        val = evaluate(peft_model, val_enc, device)
        improved = val["loss"] < best - selection["min_delta"]
        last_epoch_seconds = time.time() - epoch_started
        history.append({"epoch": epoch, "train": train_losses, "validation": val, "improved": improved,
                        "seconds": round(last_epoch_seconds, 1)})
        log({"type": "epoch", "epoch": epoch, "train": train_losses, "validation": val, "improved": improved})
        if improved:
            best, best_epoch, bad_epochs = val["loss"], epoch, 0
            peft_model.save_pretrained(out_dir / "best_adapter")
        else:
            bad_epochs += 1
            if bad_epochs > selection["early_stopping_patience"]:
                log({"type": "early_stop", "epoch": epoch})
                break
    return {"steps": step, "steps_per_epoch": steps_per_epoch, "planned_steps": total_steps,
            "best_epoch": best_epoch, "best_validation_loss": best, "history": history,
            "epochs_completed": len(history), "stopped_for_deadline": stopped_for_deadline,
            "weighting": {"applied": use_weights, "normalizer_mean_weight": round(normalizer, 6),
                          "weight_sum": round(sum(weights), 3), "examples_below_weight_1": sum(1 for w in weights if w < 1)}}


def gpu_memory(device: str) -> dict[str, Any] | None:
    if device != "cuda":
        return None
    return {"peak_allocated_gib": round(torch.cuda.max_memory_allocated() / 2**30, 2),
            "peak_reserved_gib": round(torch.cuda.max_memory_reserved() / 2**30, 2),
            "total_gib": round(torch.cuda.get_device_properties(0).total_memory / 2**30, 2)}


def preflight(cfg: dict[str, Any], train_enc, val_enc, peft_model, device: str, log) -> dict[str, Any]:
    """Worst-case memory and a throughput sample on the real model, without
    changing any weights (no optimizer step)."""
    longest_train = max(train_enc, key=lambda e: len(e["input_ids"]))
    longest_val = max(val_enc, key=lambda e: len(e["input_ids"]))
    peft_model.train()
    timings = []
    for _ in range(3):  # first pass warms up kernels; later passes are timed
        if device == "cuda":
            torch.cuda.synchronize()
        t0 = time.time()
        example_loss(peft_model, longest_train, device).backward()
        if device == "cuda":
            torch.cuda.synchronize()
        timings.append(time.time() - t0)
    peft_model.zero_grad(set_to_none=True)
    with torch.no_grad():
        peft_model.eval()
        t0 = time.time()
        example_loss(peft_model, longest_val, device)
        if device == "cuda":
            torch.cuda.synchronize()
        val_seconds = time.time() - t0
        peft_model.train()
    tokens = len(longest_train["input_ids"])
    train_sec_per_token = min(timings[1:]) / tokens
    val_sec_per_token = val_seconds / len(longest_val["input_ids"])
    train_tokens = sum(len(e["input_ids"]) for e in train_enc)
    val_tokens = sum(len(e["input_ids"]) for e in val_enc)
    epochs = cfg["training"]["epochs"]
    projected = epochs * train_tokens * train_sec_per_token + (epochs + 1) * val_tokens * val_sec_per_token
    result = {"longest_train_tokens": tokens, "longest_validation_tokens": len(longest_val["input_ids"]),
              "train_seconds_per_longest_example": round(min(timings[1:]), 3),
              "validation_seconds_per_longest_example": round(val_seconds, 3),
              "projected_training_minutes": round(projected / 60, 1), "gpu_memory": gpu_memory(device)}
    log({"type": "preflight", **result})
    return result


def tokenizer_record(tokenizer, cfg: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    tok_dir = out_dir / "tokenizer"
    tokenizer.save_pretrained(tok_dir)
    template = tokenizer.chat_template or ""
    return {"repo": cfg["base_model"]["repo"], "revision": cfg["base_model"]["revision"],
            "class": type(tokenizer).__name__, "vocab_size": len(tokenizer),
            "eos_token": tokenizer.eos_token, "pad_token": tokenizer.pad_token,
            "chat_template_sha256": hashlib.sha256(template.encode()).hexdigest(),
            "saved_files_sha256": {p.name: D.sha256_file(p) for p in sorted(tok_dir.iterdir()) if p.is_file()}}


def run(cfg: dict[str, Any], mode: str, out_dir: Path) -> dict[str, Any]:
    started = time.time()
    seed_everything(cfg["seed"])
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "log.jsonl"
    log_file = open(log_path, "w")

    def log(record: dict[str, Any]) -> None:
        log_file.write(json.dumps(record) + "\n")
        log_file.flush()
        print(json.dumps(record), flush=True)

    manifest: dict[str, Any] = {
        "mode": mode, "started_at": datetime.now(timezone.utc).isoformat(), "command": sys.argv,
        "config": {k: v for k, v in cfg.items() if not k.startswith("_")},
        "config_path": cfg["_config_path"], "config_sha256": cfg["_config_sha256"],
        "data_sha256": {role: cfg["data"][role]["sha256"] for role in ("train", "validation")},
        "test_data": "not loaded (reserved for final evaluation)",
        "environment": environment(), "git": _git(), "seed": cfg["seed"],
    }
    tokenizer = load_tokenizer(cfg)
    limits = (cfg["smoke"]["train_examples"], cfg["smoke"]["validation_examples"]) if mode == "smoke" else (None, None)
    train_enc, val_enc = prepare_data(cfg, tokenizer, *limits)
    manifest["data_stats"] = {"train": stats(train_enc), "validation": stats(val_enc)}
    if mode == "plan":
        tc = cfg["training"]
        manifest["plan"] = {
            "steps_per_epoch": math.ceil(len(train_enc) / tc["gradient_accumulation_steps"]),
            "max_epochs": tc["epochs"],
            "train_tokens_per_epoch": manifest["data_stats"]["train"]["total_tokens"],
            "validation_tokens_per_eval": manifest["data_stats"]["validation"]["total_tokens"],
        }
    else:
        tiny = mode == "smoke" or cfg.get("_tiny_preflight", False)
        if mode == "smoke":
            cfg = json.loads(json.dumps(cfg))
            cfg["training"].update(cfg["smoke"]["training_overrides"])
        device = "cpu" if tiny else "cuda"
        if device == "cuda":
            torch.cuda.reset_peak_memory_stats()
        model = load_model(cfg, tiny=tiny)
        peft_model = add_lora(model, cfg)
        trainable = sum(p.numel() for p in peft_model.parameters() if p.requires_grad)
        total = sum(p.numel() for p in peft_model.parameters())
        manifest["parameters"] = {"trainable": trainable, "total": total}
        if mode == "preflight":
            manifest["preflight"] = preflight(cfg, train_enc, val_enc, peft_model, device, log)
            return _finish(manifest, out_dir, "preflight.json", started, log_file)
        manifest["tokenizer"] = tokenizer_record(tokenizer, cfg, out_dir)
        with open(out_dir / "config.json", "wb") as f, open(cfg["_config_path"], "rb") as src:
            f.write(src.read())
        before = evaluate(peft_model, val_enc, device)
        log({"type": "baseline_validation", "validation": before})
        manifest["validation_before_training"] = before
        manifest["result"] = train_loop(cfg, train_enc, val_enc, peft_model, device, out_dir, log)
        manifest["gpu_memory"] = gpu_memory(device)
        adapter = out_dir / "best_adapter"
        manifest["artifact"] = str(adapter)
        manifest["artifact_sha256"] = ({p.name: D.sha256_file(p) for p in sorted(adapter.iterdir()) if p.is_file()}
                                       if adapter.exists() else None)
    return _finish(manifest, out_dir, "plan.json" if mode == "plan" else "run-manifest.json", started, log_file)


def _finish(manifest: dict[str, Any], out_dir: Path, name: str, started: float, log_file) -> dict[str, Any]:
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    manifest["wall_seconds"] = round(time.time() - started, 1)
    with open(out_dir / name, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True, default=str)
    log_file.close()
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gtmflow_training.train")
    parser.add_argument("mode", choices=("plan", "smoke", "preflight", "train"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", help="output directory (default: config output_dir/<mode>)")
    parser.add_argument("--confirm-paid-compute", action="store_true",
                        help="required for 'preflight' and 'train': confirms the user approved this paid GPU run")
    parser.add_argument("--deadline-unix", type=float,
                        help="train: do not start an epoch that cannot finish (plus --deadline-reserve-seconds) before this time")
    parser.add_argument("--deadline-reserve-seconds", type=float, default=1200)
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    if args.deadline_unix:
        cfg["_deadline_unix"] = args.deadline_unix
        cfg["_deadline_reserve_seconds"] = args.deadline_reserve_seconds
    if args.mode in ("preflight", "train"):
        if not args.confirm_paid_compute:
            print(f"refused: '{args.mode}' needs --confirm-paid-compute (paid GPU run requires explicit approval)", file=sys.stderr)
            return 2
        if not torch.cuda.is_available():
            print(f"refused: '{args.mode}' needs a CUDA GPU", file=sys.stderr)
            return 2
        gpu_gb = torch.cuda.get_device_properties(0).total_memory / 2**30
        if gpu_gb < cfg["hardware"]["min_gpu_memory_gib"] or not torch.cuda.is_bf16_supported():
            print(f"refused: needs a bf16-capable GPU with >= {cfg['hardware']['min_gpu_memory_gib']} GiB "
                  f"(found {gpu_gb:.1f} GiB)", file=sys.stderr)
            return 2
    out_dir = Path(args.out) if args.out else Path(cfg["output_dir"]) / args.mode
    try:
        manifest = run(cfg, args.mode, out_dir)
    except D.DataGuardError as error:
        print(f"data guard: {error}", file=sys.stderr)
        return 3
    summary = {k: manifest.get(k) for k in ("mode", "data_stats", "plan", "parameters", "preflight", "gpu_memory", "wall_seconds")}
    if "result" in manifest:
        summary["result"] = {k: manifest["result"][k] for k in
                             ("steps", "epochs_completed", "best_epoch", "best_validation_loss", "stopped_for_deadline", "weighting")}
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
