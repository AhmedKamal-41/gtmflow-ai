"""Phase 10: the Phase 8 LoRA adapter (`qwen3-4b-lora-v1`) as an AIClient.

The backend does not load model weights. It talks to an OpenAI-compatible
inference server that serves the pinned base model with the adapter loaded
under the name `LORA_SERVED_MODEL` (default `qwen3-4b-lora-v1`) -- for
example vLLM:

    vllm serve Qwen/Qwen3-4B-Instruct-2507 --revision cdbee75f17c01a7cc42f958dc650907174af0554 \\
        --enable-lora --max-lora-rank 16 \\
        --lora-modules qwen3-4b-lora-v1=training/runs/phase8-qwen3-4b-lora-v1/train/best_adapter

Requests match the Phase 9 evaluation exactly where the API allows: the same
system message and grounded-v2 prompt builders (the server applies the
model's own chat template, as training and evaluation did), greedy decoding
(temperature 0) and at most 1,024 new tokens. A reply cut off at that limit
is rejected, never repaired.

Selected only with USE_MOCK_AI=false AND AI_PROVIDER=qwen3-4b-lora-v1.
Configuration is validated before any network call; the first call checks
that the server actually lists the served model. The server cannot prove
which adapter file it loaded: the pinned adapter hash is recorded as the
*configured* adapter in provenance, not as a server-verified fact.
"""
from __future__ import annotations

import ipaddress
import threading
from typing import Any
from urllib.parse import urlsplit

from app.ai.client import AIClient, AIConfigError, AIProviderError
from app.ai.json_parser import AIOutputTruncated, parse_json_strict
from app.ai.prompts import JSON_SYSTEM_MESSAGE, build_outreach_prompt, build_summary_prompt

PROVIDER = "qwen3-4b-lora-v1"
BASE_MODEL = "Qwen/Qwen3-4B-Instruct-2507"
BASE_REVISION = "cdbee75f17c01a7cc42f958dc650907174af0554"
ADAPTER_SHA256 = "a68ff056511a59f1de9e85b36696609060bac802b7c7ba95cb21c245536caf09"
ADAPTER_REVISION = f"phase8-qwen3-4b-lora-v1/epoch3@sha256:{ADAPTER_SHA256}"
MAX_NEW_TOKENS = 1024  # training/configs/phase9-eval-v1.json

_verified: set[tuple[str, str]] = set()
_verified_lock = threading.Lock()


def _host_is_local(host: str) -> bool:
    if host in ("localhost",) or "." not in host:  # loopback name or a single-label service name
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def validate_base_url(url: str) -> str:
    """An explicit http(s) URL; plain http only to a local/private host, so
    an optional API key never crosses the internet unencrypted."""
    if not url:
        raise AIConfigError(
            "AI_PROVIDER=qwen3-4b-lora-v1 needs LORA_INFERENCE_BASE_URL (an OpenAI-compatible "
            "inference server, e.g. http://localhost:8000/v1). Or set USE_MOCK_AI=true."
        )
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise AIConfigError("LORA_INFERENCE_BASE_URL must be an http(s) URL with a host.")
    if parts.scheme == "http" and not _host_is_local(parts.hostname):
        raise AIConfigError("LORA_INFERENCE_BASE_URL must use https unless the server is local or on a private network.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise AIConfigError("LORA_INFERENCE_BASE_URL must not carry credentials, a query or a fragment.")
    return url.rstrip("/")


class LocalLoRAClient(AIClient):
    name = PROVIDER
    model_revision = f"{BASE_MODEL}@{BASE_REVISION}"
    adapter_revision = ADAPTER_REVISION

    def __init__(self, base_url: str, served_model: str = PROVIDER, api_key: str = "",
                 timeout_seconds: float = 120.0, http_client: Any = None) -> None:
        self._base_url = validate_base_url(base_url)
        if not served_model:
            raise AIConfigError("LORA_SERVED_MODEL must name the adapter as the server serves it.")
        self._served_model = served_model
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._http_client = http_client  # tests inject a mocked transport

    def _openai(self):
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError as e:
            raise AIConfigError("The LoRA provider uses the `openai` package as its HTTP client; install it.") from e
        return OpenAI(base_url=self._base_url, api_key=self._api_key or "unused", timeout=self._timeout,
                      max_retries=0, http_client=self._http_client)

    def _ensure_served(self, client) -> None:
        key = (self._base_url, self._served_model)
        if key in _verified:
            return
        try:
            served = {m.id for m in client.models.list().data}
        except Exception as e:  # noqa: BLE001 -- sanitized
            raise AIProviderError(f"Inference server model listing failed ({type(e).__name__}).") from None
        if self._served_model not in served:
            raise AIConfigError(
                f"The inference server does not serve '{self._served_model}'. Start it with the Phase 8 "
                "adapter registered under that name (see docs/upgrade/phase10-integration-handoff.md)."
            )
        with _verified_lock:
            _verified.add(key)

    def _call(self, prompt: str) -> dict[str, Any]:
        client = self._openai()
        self._ensure_served(client)
        self.last_usage = None
        try:
            response = client.chat.completions.create(
                model=self._served_model,
                messages=[{"role": "system", "content": JSON_SYSTEM_MESSAGE}, {"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=MAX_NEW_TOKENS,
                n=1,
            )
        except Exception as e:  # noqa: BLE001 -- sanitized, see AIProviderError
            raise AIProviderError(f"AI provider request failed ({type(e).__name__}).") from None
        choice = response.choices[0]
        usage = getattr(response, "usage", None)
        self.last_usage = {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
            "response_model": getattr(response, "model", None),
            "finish_reason": getattr(choice, "finish_reason", None),
        }
        if getattr(choice, "finish_reason", None) == "length":
            raise AIOutputTruncated(f"Output truncated at the {MAX_NEW_TOKENS}-token limit; not repaired.")
        return parse_json_strict(choice.message.content or "")

    def generate_company_summary(self, ctx: dict[str, Any]) -> dict[str, Any]:
        return self._call(build_summary_prompt(ctx))

    def generate_outreach(self, ctx: dict[str, Any]) -> dict[str, Any]:
        return self._call(build_outreach_prompt(ctx))
