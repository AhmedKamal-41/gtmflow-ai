"""Dry-run server for the Phase 10 acceptance test (no model, free).

    GTMFLOW_SERVE_TOKEN=<token> python scripts/phase10_replay_stub.py --port 18000

OpenAI-compatible like training/gtmflow_training/serve.py (same routes, auth
and request rules). A prompt whose grounded context equals a test-v1 input
snapshot is answered with the stored Phase 9 LoRA prediction for it; any
other prompt with the mock generator's output for that context. It lets the
whole acceptance flow (driver, API, worker, checks) be rehearsed locally.
Its results are labeled DRY RUN and prove nothing about the real model.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
PHASE9 = BACKEND.parent / "training" / "runs" / "phase9-eval-v1"


def key(ctx) -> str:
    return json.dumps(ctx, sort_keys=True, default=str)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18000)
    args = parser.parse_args()
    from app.ai.mock_client import MockAIClient

    rows = [json.loads(l) for l in (BACKEND / "data/datasets/test-v1/eligible.jsonl").read_text().splitlines()]
    stored = {r["example_id"]: r["text"] for r in map(json.loads, (PHASE9 / "run/predictions/qwen3-4b-lora-v1.jsonl").read_text().splitlines())}
    replay = {key(r["input_snapshot"]): stored[r["example_id"]] for r in rows}
    token = os.environ.get("GTMFLOW_SERVE_TOKEN")
    mock = MockAIClient()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _ok(self):
            return not token or hmac.compare_digest(self.headers.get("Authorization", "").encode(), f"Bearer {token}".encode())

        def do_GET(self):  # noqa: N802
            if self.path == "/healthz":
                return self._send(200, {"status": "ok"})
            if not self._ok():
                return self._send(401, {"error": {"message": "unauthorized"}})
            self._send(200, {"object": "list", "data": [{"id": "qwen3-4b-lora-v1", "object": "model", "created": 0, "owned_by": "stub"}]})

        def do_POST(self):  # noqa: N802
            if not self._ok():
                return self._send(401, {"error": {"message": "unauthorized"}})
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if body.get("model") != "qwen3-4b-lora-v1":
                return self._send(404, {"error": {"message": "model not served"}})
            prompt = body["messages"][-1]["content"]
            ctx = json.loads(prompt.split("<untrusted_data>\n", 1)[1].split("\n</untrusted_data>", 1)[0])
            text = replay.get(key(ctx))
            if text is None:
                gen = mock.generate_outreach if ctx["task"] == "outreach_email" else mock.generate_company_summary
                text = json.dumps(gen(ctx))
            self._send(200, {"id": "stub", "object": "chat.completion", "created": 0, "model": body["model"],
                             "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
                             "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})

        def log_message(self, *a):
            pass

    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
