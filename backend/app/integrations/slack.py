"""Slack incoming-webhook integration.

Two pure-ish concerns live here:

  * `build_slack_payload`: produces the JSON dict to POST. No I/O, easily
    unit-testable.
  * `send_slack_payload`: dispatches the dict to the webhook URL via httpx
    with a hard timeout, or returns mock_success when no URL is configured.

The DB row + WorkflowEvent + lead-status update live in the service layer
(`app.services.integration_push`), so this module stays composable.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx

LEAD_URL_TEMPLATE = "http://localhost:3000/leads/{lead_id}"
SLACK_TIMEOUT_SECONDS = 10.0


def build_lead_url(lead_id: UUID) -> str:
    return LEAD_URL_TEMPLATE.format(lead_id=lead_id)


def build_slack_payload(
    *,
    lead_id: UUID,
    company_name: str,
    score: int,
    priority: str,
    industry: str | None = None,
    contact_name: str | None = None,
    contact_title: str | None = None,
    contact_email: str | None = None,
    fit_reasoning: str | None = None,
    detected_pain_points: list[str] | None = None,
    outreach_subject: str | None = None,
    call_note: str | None = None,
    lead_url: str | None = None,
) -> dict[str, Any]:
    """Return the JSON dict to POST to Slack. Missing optional fields are skipped."""
    url = lead_url or build_lead_url(lead_id)

    lines: list[str] = [f"🔥 Hot GTM Lead: {company_name}"]
    lines.append(f"Score: {score}/100 ({priority})")

    if industry:
        lines.append(f"Industry: {industry}")

    contact_parts: list[str] = []
    if contact_name:
        contact_parts.append(contact_name)
    if contact_title:
        contact_parts.append(contact_title)
    if contact_parts:
        line = "Contact: " + ", ".join(contact_parts)
        if contact_email:
            line += f" ({contact_email})"
        lines.append(line)

    if fit_reasoning:
        lines.append(f"Why now: {fit_reasoning}")
    if detected_pain_points:
        lines.append("Pain points: " + ", ".join(detected_pain_points))
    if outreach_subject:
        lines.append(f"Suggested subject: {outreach_subject}")
    if call_note:
        lines.append(f"Call note: {call_note}")

    lines.append(f"Next step: Review outreach draft in GTMFlow: {url}")

    return {"text": "\n".join(lines)}


def send_slack_payload(
    webhook_url: str | None,
    payload: dict[str, Any],
) -> tuple[str, str]:
    """POST the payload; return ``(status, response_text)``.

    status is one of: ``"success"``, ``"mock_success"``, ``"failed"``.

    Mock mode (no webhook URL configured) returns immediately without any
    network call so demos and tests work out of the box.
    """
    if not webhook_url:
        return "mock_success", "Mocked: Slack webhook URL is not configured."

    try:
        response = httpx.post(
            webhook_url,
            json=payload,
            timeout=SLACK_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        # Do not forward exception text: it may embed the webhook URL.
        return "failed", "HTTP error: could not deliver message to Slack webhook."

    if response.status_code >= 400:
        body = (response.text or "").strip()[:200]
        if body:
            return "failed", f"HTTP {response.status_code}: {body}"
        return "failed", f"HTTP {response.status_code}: Slack webhook rejected the request."

    return "success", (response.text or "ok")[:500]
