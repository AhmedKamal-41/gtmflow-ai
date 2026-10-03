"""AI client interface, real-OpenAI implementation, and factory."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.ai.json_parser import parse_json_strict
from app.ai.prompts import JSON_SYSTEM_MESSAGE, build_outreach_prompt, build_summary_prompt
from app.core.config import settings


class AIConfigError(RuntimeError):
    """Raised when an AI client cannot be constructed or invoked."""


class AIProviderError(RuntimeError):
    """The provider call failed. The message names only the exception class:
    provider errors can embed request details, so their text is never
    forwarded to API responses."""


class AIClient(ABC):
    """Both summary and outreach generators implement this interface."""

    name: str = "abstract"
    # Concrete provider/model identity for AIOutput.model_revision provenance
    # (Part D). Distinct from `name` ("mock"/"openai", the provider family):
    # this is the specific revision within that provider, e.g. a real model
    # string or a versioned mock-generator tag.
    model_revision: str = "unknown"
    # Phase 10: the adapter applied on top of `model_revision`, or None
    # (recorded in AIOutput.adapter_revision).
    adapter_revision: str | None = None
    # Token usage the provider reported for the most recent call, or None
    # (the mock makes no call). Recorded on the generation's audit event so
    # paid usage is known exactly rather than estimated.
    last_usage: dict[str, Any] | None = None

    def choose_tools(self, messages: list[dict], tools: list[dict]) -> dict:
        raise AIConfigError("This provider does not support the lead assistant's tool calling.")

    @abstractmethod
    def generate_company_summary(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """Return the company-summary JSON object for a grounded context
        (app/ai/grounding.py). The caller validates it before saving."""

    @abstractmethod
    def generate_outreach(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """Return the outreach JSON object for a grounded context. The
        caller validates it before saving."""


class OpenAIClient(AIClient):
    """Thin wrapper around OpenAI's chat-completions API.

    Construction validates the key BEFORE any network call. Tests verify
    that an empty key raises AIConfigError without touching OpenAI.
    The actual network call is lazy-loaded so the openai package only
    needs to be installed when USE_MOCK_AI=false.
    """

    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        if not api_key:
            raise AIConfigError(
                "USE_MOCK_AI is false but OPENAI_API_KEY is not set. "
                "Either provide a key or flip USE_MOCK_AI=true to use the mock client."
            )
        self._api_key = api_key
        self._model = model
        self.model_revision = model

    def _call(self, prompt: str) -> str:
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError as e:
            raise AIConfigError(
                "Real OpenAI mode requires the `openai` package. "
                "Install it (`pip install openai`) or set USE_MOCK_AI=true."
            ) from e
        self.last_usage = None
        try:
            client = OpenAI(api_key=self._api_key)
            response = client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": JSON_SYSTEM_MESSAGE,
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
        except Exception as e:  # noqa: BLE001 -- sanitized, see AIProviderError
            raise AIProviderError(
                f"AI provider request failed ({type(e).__name__})."
            ) from None
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.last_usage = {
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
                "response_model": getattr(response, "model", None),
            }
        return response.choices[0].message.content or ""

    def generate_company_summary(self, ctx: dict[str, Any]) -> dict[str, Any]:
        return parse_json_strict(self._call(build_summary_prompt(ctx)))

    def generate_outreach(self, ctx: dict[str, Any]) -> dict[str, Any]:
        return parse_json_strict(self._call(build_outreach_prompt(ctx)))

    def choose_tools(self, messages: list[dict], tools: list[dict]) -> dict:
        from app.ai.tool_calling import openai_tool_turn

        turn, self.last_usage = openai_tool_turn(self._api_key, self._model, messages, tools)
        return turn


REAL_PROVIDERS = ("openai", "qwen3-4b-lora-v1")


def get_ai_client() -> AIClient:
    """Pick the client. Called fresh per request.

    USE_MOCK_AI (default true) always wins. With USE_MOCK_AI=false,
    AI_PROVIDER chooses the real provider: "openai" (the default, unchanged
    behaviour) or "qwen3-4b-lora-v1" (Phase 10). Any other value fails
    closed before a network call. With LORA_FALLBACK_TO_MOCK=true, an
    unreachable fine-tuned server selects the mock instead."""
    if settings.use_mock_ai:
        from app.ai.mock_client import MockAIClient

        return MockAIClient()
    if settings.ai_provider == "openai":
        return OpenAIClient(api_key=settings.openai_api_key)
    if settings.ai_provider == "qwen3-4b-lora-v1":
        from app.ai.lora_client import LocalLoRAClient

        if settings.lora_fallback_to_mock:
            from app.ai.status import fine_tuned_reachable

            if not fine_tuned_reachable():
                # Opt-in: the server is down, so draft with the mock generator.
                # The output is recorded as mock (model_used/model_revision).
                from app.ai.mock_client import MockAIClient

                return MockAIClient()

        return LocalLoRAClient(
            base_url=settings.lora_inference_base_url,
            served_model=settings.lora_served_model,
            api_key=settings.lora_inference_api_key,
            timeout_seconds=settings.lora_timeout_seconds,
        )
    raise AIConfigError(
        f"Unknown AI_PROVIDER '{settings.ai_provider}'. Use one of: {', '.join(REAL_PROVIDERS)} "
        "(with USE_MOCK_AI=false), or set USE_MOCK_AI=true."
    )
