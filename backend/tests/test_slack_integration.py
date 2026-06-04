from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import httpx
import pytest

from app.integrations.slack import (
    SLACK_TIMEOUT_SECONDS,
    build_lead_url,
    build_slack_payload,
    send_slack_payload,
)


def _full_kwargs() -> dict[str, Any]:
    return {
        "lead_id": uuid4(),
        "company_name": "Cascade Modular Homes",
        "score": 94,
        "priority": "Hot",
        "industry": "Housing",
        "contact_name": "Sarah Chen",
        "contact_title": "VP Operations",
        "contact_email": "sarah@cascade.com",
        "fit_reasoning": "Strong fit because housing + ops persona + maintenance signals.",
        "detected_pain_points": ["leasing", "maintenance", "scheduling"],
        "outreach_subject": "Quick idea for Cascade Modular Homes's leasing workflow",
        "call_note": "Open with the leasing workflow angle.",
        "lead_url": "http://localhost:3000/leads/abc",
    }


def test_payload_includes_required_fields() -> None:
    payload = build_slack_payload(**_full_kwargs())
    text = payload["text"]

    assert "Cascade Modular Homes" in text
    assert "94/100" in text
    assert "Hot" in text
    assert "Housing" in text
    assert "Sarah Chen" in text
    assert "VP Operations" in text
    assert "sarah@cascade.com" in text
    assert "Strong fit" in text
    assert "leasing" in text and "maintenance" in text
    assert "Quick idea for Cascade Modular Homes's leasing workflow" in text
    assert "Open with the leasing workflow angle." in text
    assert "http://localhost:3000/leads/abc" in text


def test_payload_handles_missing_optional_fields() -> None:
    payload = build_slack_payload(
        lead_id=uuid4(),
        company_name="Minimal Co",
        score=80,
        priority="Hot",
    )
    text = payload["text"]
    assert "Minimal Co" in text
    assert "80/100" in text
    assert "Hot" in text
    # No contact / industry / outreach sections were appended.
    assert "Industry:" not in text
    assert "Contact:" not in text
    assert "Why now:" not in text
    assert "Suggested subject:" not in text
    assert "Next step:" in text  # the default URL line is always emitted


def test_send_slack_payload_no_url_returns_mock_success() -> None:
    status, text = send_slack_payload(None, {"text": "anything"})
    assert status == "mock_success"
    assert "Mocked" in text

    status, text = send_slack_payload("", {"text": "anything"})
    assert status == "mock_success"


def test_send_slack_payload_with_url_calls_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    class FakeResponse:
        status_code = 200
        text = "ok"

    def fake_post(url: str, json: dict, timeout: float) -> FakeResponse:
        calls.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr("app.integrations.slack.httpx.post", fake_post)

    status, text = send_slack_payload(
        "https://hooks.slack.com/services/X/Y/Z", {"text": "hi"}
    )
    assert status == "success"
    assert text == "ok"
    assert len(calls) == 1
    assert calls[0]["url"] == "https://hooks.slack.com/services/X/Y/Z"
    assert calls[0]["json"] == {"text": "hi"}
    assert calls[0]["timeout"] == SLACK_TIMEOUT_SECONDS


def test_send_slack_payload_http_error_returns_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        status_code = 500
        text = "Slack internal error"

    monkeypatch.setattr(
        "app.integrations.slack.httpx.post", lambda *a, **kw: FakeResponse()
    )

    status, text = send_slack_payload(
        "https://hooks.slack.com/services/X/Y/Z", {"text": "hi"}
    )
    assert status == "failed"
    assert "500" in text


def test_send_slack_payload_network_error_returns_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_conn_error(*a: Any, **kw: Any) -> None:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("app.integrations.slack.httpx.post", raise_conn_error)

    status, text = send_slack_payload(
        "https://hooks.slack.com/services/X/Y/Z", {"text": "hi"}
    )
    assert status == "failed"
    assert "could not deliver" in text.lower()
    assert "hooks.slack.com" not in text
    assert "SLACK_WEBHOOK_URL" not in text


def test_send_slack_payload_network_error_does_not_leak_webhook_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """httpx errors can embed the request URL; response_text must stay clean."""

    def raise_with_url(*a: Any, **kw: Any) -> None:
        raise httpx.ConnectError(
            "connection failed",
            request=httpx.Request(
                "POST", "https://hooks.slack.com/services/SECRET/PATH"
            ),
        )

    monkeypatch.setattr("app.integrations.slack.httpx.post", raise_with_url)

    status, text = send_slack_payload(
        "https://hooks.slack.com/services/X/Y/Z", {"text": "hi"}
    )
    assert status == "failed"
    assert "hooks.slack.com" not in text
    assert "SECRET" not in text


def test_payload_never_leaks_webhook_url_or_secret_markers() -> None:
    payload = build_slack_payload(**_full_kwargs())
    text_blob = json.dumps(payload)
    # The builder never has access to the webhook URL, but assert anyway.
    assert "SLACK_WEBHOOK_URL" not in text_blob
    assert "hooks.slack.com" not in text_blob


def test_build_lead_url_uses_template() -> None:
    lid = uuid4()
    assert build_lead_url(lid) == f"http://localhost:3000/leads/{lid}"
