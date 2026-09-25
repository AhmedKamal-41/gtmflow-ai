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
    # Token usage the provider reported for the most recent call, or None
    # (the mock makes no call). Recorded on the generation's audit event so
    # paid usage is known exactly rather than estimated.
    last_usage: dict[str, Any] | None = None

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


def get_ai_client() -> AIClient:
    """Pick the right client based on USE_MOCK_AI. Called fresh per request."""
    if settings.use_mock_ai:
        from app.ai.mock_client import MockAIClient

        return MockAIClient()
    return OpenAIClient(api_key=settings.openai_api_key)
