"""Which model drafts right now, for the UI and for the fallback decision.

`fine_tuned_reachable` asks the configured inference server for its model
list with a short timeout and caches the answer briefly, so a page load or a
generation never waits long on a server that is down. The probe sends only
the optional API key; the key is never logged or returned.
"""
from __future__ import annotations

import threading
import time

import httpx

from app.ai.client import AIConfigError
from app.ai.lora_client import ADAPTER_REVISION, BASE_MODEL, PROVIDER, validate_base_url
from app.core.config import settings

PROBE_TIMEOUT_SECONDS = 3.0
PROBE_CACHE_SECONDS = 30.0
FINE_TUNED_LABEL = "GTMFlow fine-tuned model (Qwen3-4B + LoRA)"
MOCK_LABEL = "Demo generator (no AI model)"

_cache: dict[tuple[str, str], tuple[float, bool]] = {}
_lock = threading.Lock()


def fine_tuned_reachable() -> bool:
    try:
        base = validate_base_url(settings.lora_inference_base_url)
    except AIConfigError:
        return False
    key = (base, settings.lora_served_model)
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
    if cached and now - cached[0] < PROBE_CACHE_SECONDS:
        return cached[1]
    headers = {"Authorization": f"Bearer {settings.lora_inference_api_key}"} if settings.lora_inference_api_key else {}
    try:
        response = httpx.get(f"{base}/models", headers=headers, timeout=PROBE_TIMEOUT_SECONDS, trust_env=False)
        served = {item.get("id") for item in response.json().get("data", [])} if response.status_code == 200 else set()
        reachable = settings.lora_served_model in served
    except (httpx.HTTPError, ValueError, AttributeError):
        reachable = False
    with _lock:
        _cache[key] = (now, reachable)
    return reachable


def clear_probe_cache() -> None:
    with _lock:
        _cache.clear()


def ai_status() -> dict:
    """Plain-language description of the drafting model."""
    fine_tuned_selected = not settings.use_mock_ai and settings.ai_provider == PROVIDER
    reachable = fine_tuned_reachable() if fine_tuned_selected else False
    if settings.use_mock_ai:
        mode, label = "mock", MOCK_LABEL
        detail = "Drafts come from a built-in demo generator. Connect the fine-tuned model to get real drafts."
    elif fine_tuned_selected and reachable:
        mode, label = "fine_tuned", FINE_TUNED_LABEL
        detail = "Drafts are written by the fine-tuned model."
    elif fine_tuned_selected and settings.lora_fallback_to_mock:
        mode, label = "mock", MOCK_LABEL
        detail = "The fine-tuned model is offline, so drafts come from the demo generator until it is back."
    elif fine_tuned_selected:
        mode, label = "unavailable", FINE_TUNED_LABEL
        detail = "The fine-tuned model is offline. Drafting is paused until its server is back."
    else:
        mode, label = "openai", "OpenAI (gpt-4o-mini)"
        detail = "Drafts are written by OpenAI's hosted model."
    return {
        "mode": mode,
        "label": label,
        "detail": detail,
        "fine_tuned_selected": fine_tuned_selected,
        "fine_tuned_connected": reachable,
        "fallback_enabled": fine_tuned_selected and settings.lora_fallback_to_mock,
        "base_model": BASE_MODEL,
        "adapter": ADAPTER_REVISION,
    }
