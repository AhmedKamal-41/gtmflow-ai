"""AI client interface, real-OpenAI implementation, and factory."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.ai.json_parser import parse_json_strict
from app.ai.prompts import build_outreach_prompt, build_summary_prompt
from app.core.config import settings


class AIConfigError(RuntimeError):
    """Raised when an AI client cannot be constructed or invoked."""


class AIClient(ABC):
    """Both summary and outreach generators implement this interface."""

    name: str = "abstract"

    @abstractmethod
    def generate_company_summary(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """Return the company-summary JSON object."""

    @abstractmethod
    def generate_outreach(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """Return the outreach JSON object."""


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

    def _call(self, prompt: str) -> str:
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError as e:
            raise AIConfigError(
                "Real OpenAI mode requires the `openai` package. "
                "Install it (`pip install openai`) or set USE_MOCK_AI=true."
            ) from e
        client = OpenAI(api_key=self._api_key)
        response = client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Reply with strict JSON only -- no commentary, no "
                        "markdown fences."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
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
