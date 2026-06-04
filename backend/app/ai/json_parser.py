"""Strict JSON-object parser for AI responses."""

from __future__ import annotations

import json
from typing import Any


class AIJSONParseError(ValueError):
    """Raised when an AI response cannot be parsed as a JSON object."""


def parse_json_strict(text: str) -> dict[str, Any]:
    """Parse the given text as a JSON object.

    Tolerant of leading/trailing whitespace and ```json code fences (some
    models emit them despite instructions). Anything else raises
    AIJSONParseError -- the caller should surface a clean 5xx/4xx error.
    """
    text = (text or "").strip()
    if not text:
        raise AIJSONParseError("AI returned empty text.")

    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise AIJSONParseError(f"Invalid JSON from AI: {e}") from e

    if not isinstance(data, dict):
        raise AIJSONParseError(
            f"Expected JSON object, got {type(data).__name__}."
        )
    return data
