"""Phase 10 acceptance: serve the Phase 8 adapter behind a minimal
OpenAI-compatible HTTP API, using exactly the Phase 9 generation stack.

    python -m gtmflow_training.serve --config configs/phase9-eval-v1.json --out runs/phase10-accept-v1/serve --smoke
    python -m gtmflow_training.serve --config configs/phase9-eval-v1.json --out runs/phase10-accept-v1/serve \\
        --confirm-paid-compute --stop-file /workspace/job/stop --deadline-unix <t>

Why not vLLM: the hash-locked GPU environment verified in Phases 8 and 9 has
no vLLM, and adding it would pull a different torch build. This server loads
the pinned base model and the pinned adapter with the same code and decodes
with the same settings as `generate.py` (greedy, <= 1,024 new tokens,
the model's chat template, eos from the pinned generation config), one
request at a time. Phase 9 decoded left-padded batches of 8; a single
unpadded request can differ numerically in bf16, so exact text parity is
measured, not assumed.

Endpoints: GET /v1/models, POST /v1/chat/completions, GET /healthz.
It binds 127.0.0.1 only (reached through an SSH tunnel) and, when
GTMFLOW_SERVE_TOKEN is set, requires `Authorization: Bearer <token>`.
Request logs record sizes and timings, never prompt or output text.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import torch

from gtmflow_training import data as D
from gtmflow_training import generate as G
from gtmflow_training import train as T

SERVED_MODEL = "qwen3-4b-lora-v1"
MAX_NEW_TOKENS = 1024


class Generator:
    def __init__(self, cfg: dict[str, Any], smoke: bool) -> None:
        T.seed_everything(cfg["seed"])
        self.tokenizer = T.load_tokenizer(cfg)
        self.tokenizer.padding_side = "left"
        if smoke:
            adapter_dir = G._tiny_adapter(cfg, Path(tempfile.mkdtemp()))
            self.adapter_sha256 = {"tiny random adapter (smoke)": D.sha256_file(adapter_dir / "adapter_model.safetensors")}
            self.max_new_tokens = cfg["smoke"]["max_new_tokens"]
        else:
            adapter_dir = Path(cfg["adapter"]["dir"])
            self.adapter_sha256 = G.verify_adapter(adapter_dir, cfg["adapter"]["sha256"])  # refuses on mismatch
            self.max_new_tokens = MAX_NEW_TOKENS
            torch.cuda.reset_peak_memory_stats()
        self.model, self.device = G.load_systems(cfg, smoke, adapter_dir)
        self.stop_ids = G.eos_ids(self.model, self.tokenizer)
        self.lock = threading.Lock()

    def complete(self, messages: list[dict[str, str]], max_tokens: int) -> dict[str, Any]:
        text = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        prompt_tokens = len(self.tokenizer(text, add_special_tokens=False)["input_ids"])
        row = {"example_id": "request", "split": "serve", "subset": "serve", "task": "serve"}
        with self.lock:  # one request at a time
            started = time.time()
            [record] = G.generate_batch(self.model, self.tokenizer, [(row, text, prompt_tokens)],
                                        min(max_tokens, self.max_new_tokens), self.stop_ids, self.device)
            record["seconds"] = round(time.time() - started, 3)
        return record


def _error(status: int, message: str) -> tuple[int, dict[str, Any]]:
    return status, {"error": {"message": message, "type": "invalid_request_error"}}


def handle_chat(gen: Generator, body: dict[str, Any]) -> tuple[int, dict[str, Any], dict[str, Any] | None]:
    if body.get("model") != SERVED_MODEL:
        return (*_error(404, f"model '{body.get('model')}' is not served"), None)
    if body.get("temperature") not in (None, 0, 0.0):
        return (*_error(400, "only greedy decoding (temperature 0) is served"), None)
    if body.get("n") not in (None, 1):
        return (*_error(400, "n must be 1"), None)
    max_tokens = body.get("max_tokens") or MAX_NEW_TOKENS
    if not isinstance(max_tokens, int) or not 1 <= max_tokens <= MAX_NEW_TOKENS:
        return (*_error(400, f"max_tokens must be 1..{MAX_NEW_TOKENS}"), None)
    messages = body.get("messages")
    if not isinstance(messages, list) or not all(
            isinstance(m, dict) and m.get("role") in ("system", "user", "assistant") and isinstance(m.get("content"), str)
            for m in messages):
        return (*_error(400, "messages must be a list of {role, content}"), None)
    record = gen.complete([{"role": m["role"], "content": m["content"]} for m in messages], max_tokens)
    finish = "stop" if record["finish"] == "eos" else "length"
    response = {
        "id": f"chatcmpl-{int(time.time() * 1000)}", "object": "chat.completion", "created": int(time.time()),
        "model": SERVED_MODEL,
        "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": record["text"]}}],
        "usage": {"prompt_tokens": record["prompt_tokens"], "completion_tokens": record["new_tokens"],
                  "total_tokens": record["prompt_tokens"] + record["new_tokens"]},
    }
    return 200, response, {"prompt_tokens": record["prompt_tokens"], "new_tokens": record["new_tokens"],
                           "finish": finish, "seconds": record["seconds"]}


def make_handler(gen: Generator, token: str | None, log):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            if not token:
                return True
            given = self.headers.get("Authorization", "")
            return hmac.compare_digest(given.encode(), f"Bearer {token}".encode())

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                return self._send(200, {"status": "ok"})
            if not self._authorized():
                log({"path": self.path, "status": 401})
                return self._send(401, {"error": {"message": "unauthorized"}})
            if self.path == "/v1/models":
                log({"path": self.path, "status": 200})
                return self._send(200, {"object": "list", "data": [
                    {"id": SERVED_MODEL, "object": "model", "created": 0, "owned_by": "gtmflow"}]})
            self._send(404, {"error": {"message": "not found"}})

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                log({"path": self.path, "status": 401})
                return self._send(401, {"error": {"message": "unauthorized"}})
            if self.path != "/v1/chat/completions":
                return self._send(404, {"error": {"message": "not found"}})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            except (ValueError, TypeError):
                return self._send(400, {"error": {"message": "invalid JSON"}})
            status, response, stats = handle_chat(gen, body)
            log({"path": self.path, "status": status, **(stats or {})})
            self._send(status, response)

        def log_message(self, *args) -> None:  # no default access log (keeps output small)
            pass

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gtmflow_training.serve")
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--smoke", action="store_true", help="tiny random model and adapter on CPU (free)")
    parser.add_argument("--confirm-paid-compute", action="store_true")
    parser.add_argument("--stop-file", default=None)
    parser.add_argument("--deadline-unix", type=float, default=None)
    args = parser.parse_args(argv)
    if not args.smoke:
        if not args.confirm_paid_compute:
            print("refused: real serving needs --confirm-paid-compute", file=sys.stderr)
            return 2
        if not torch.cuda.is_available():
            print("refused: real serving needs a CUDA GPU", file=sys.stderr)
            return 2

    cfg = G.load_config(args.config)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    log_file = open(out / "requests.jsonl", "a")
    counts = {"requests": 0}

    def log(record: dict[str, Any]) -> None:
        counts["requests"] += 1
        log_file.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), **record}) + "\n")
        log_file.flush()

    started = time.time()
    gen = Generator(cfg, args.smoke)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(gen, os.environ.get("GTMFLOW_SERVE_TOKEN"), log))
    ready = {"mode": "smoke" if args.smoke else "serve", "served_model": SERVED_MODEL, "port": args.port,
             "adapter_sha256": gen.adapter_sha256, "config_sha256": cfg["_config_sha256"],
             "base_model": cfg["base_model"], "load_seconds": round(time.time() - started, 1)}
    print(json.dumps({"type": "serving", **ready}), flush=True)

    def watch() -> None:
        while True:
            time.sleep(1)
            if (args.stop_file and Path(args.stop_file).exists()) or (args.deadline_unix and time.time() > args.deadline_unix):
                server.shutdown()
                return

    threading.Thread(target=watch, daemon=True).start()
    server.serve_forever()
    server.server_close()
    manifest = {**ready, "environment": T.environment(), "requests_logged": counts["requests"],
                "wall_seconds": round(time.time() - started, 1),
                "stopped_by": "stop_file" if args.stop_file and Path(args.stop_file).exists() else "deadline"}
    if not args.smoke:
        manifest["gpu_memory"] = {"peak_allocated_gib": round(torch.cuda.max_memory_allocated() / 2**30, 2),
                                  "peak_reserved_gib": round(torch.cuda.max_memory_reserved() / 2**30, 2)}
    (out / "serve-manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    print(json.dumps({"type": "stopped", "requests": counts["requests"]}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
