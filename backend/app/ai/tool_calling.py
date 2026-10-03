"""Native function calling through the existing AI clients; no agent framework.

The deterministic mock exercises the same tools, but is not an AI model.
Only the service layer may execute a returned tool request.
"""
from __future__ import annotations

import json
import re

from app.ai.client import AIConfigError, AIProviderError, OpenAIClient
from app.core.config import settings


def get_agent_client():
    if settings.use_mock_ai or settings.agent_provider == "mock":
        from app.ai.mock_client import MockAIClient

        client = MockAIClient()
        client.model_revision = "mock-lead-assistant-v1"
        return client
    if settings.agent_provider == "openai":
        return OpenAIClient(settings.openai_api_key, model=settings.agent_model)
    raise AIConfigError("AGENT_PROVIDER must be mock or openai.")


def openai_tool_turn(api_key: str, model: str, messages: list[dict], tools: list[dict]):
    try:
        from openai import OpenAI

        # One attempt per step. A bounded tool loop must not hide SDK retries.
        with OpenAI(api_key=api_key, base_url="https://api.openai.com/v1",
                    timeout=15.0, max_retries=0) as client:
            response = client.chat.completions.create(
                model=model, messages=messages, tools=tools, tool_choice="auto",
                parallel_tool_calls=False, max_completion_tokens=800,
            )
        choice = response.choices[0]
        if choice.finish_reason not in ("stop", "tool_calls"):
            raise ValueError("Incomplete assistant response")
        calls = choice.message.tool_calls or []
        turn = {"role": "assistant", "content": None,
                "tool_calls": [{"id": c.id, "type": "function",
                                "function": {"name": c.function.name, "arguments": c.function.arguments}}
                               for c in calls]}
        usage = response.usage
        counts = ({"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
                   "total_tokens": usage.total_tokens} if usage else None)
        return turn, counts
    except Exception:
        # Never echo prompts, imported fields, tokens, URLs, or SDK errors.
        raise AIProviderError("The assistant provider could not complete this step.") from None


def _call(name: str, arguments: dict, step: int) -> dict:
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": f"demo-{step}", "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments)}}]}


def mock_tool_turn(messages: list[dict]) -> dict:
    """A labeled demo: recognizes priority and healthcare/real-estate terms.

    It never claims to understand arbitrary natural-language constraints.
    """
    task = json.loads(next(m["content"] for m in messages if m["role"] == "user"))
    results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
    if not results:
        prompt = task["request"].lower()
        priority = next((p for p in ("Hot", "Warm", "Cold") if re.search(rf"\b{p.lower()}\b", prompt)), None)
        industry = "health" if "health" in prompt else "real estate" if "real estate" in prompt else None
        return _call("search_leads", {"query": None, "industry": industry, "priority": priority}, 1)
    last = results[-1]
    if last["tool"] == "search_leads":
        ids = [r["id"] for r in last.get("leads", [])][:task["max_leads"]]
        return _call("inspect_leads", {"lead_ids": ids}, 2) if ids else _call("propose_leads", {"lead_ids": []}, 2)
    ids = [r["id"] for r in last.get("leads", [])][:task["max_leads"]]
    return _call("propose_leads", {"lead_ids": ids}, 3)
