"""Orchestrates lead -> AI client -> AIOutput persistence."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.client import get_ai_client
from app.core.config import settings
from app.models import AIOutput, Lead, WorkflowEvent

PROMPT_VERSION = "v1"


def _model_label() -> str:
    return "mock" if settings.use_mock_ai else "openai"


def _build_lead_context(lead: Lead) -> dict[str, Any]:
    """Compose the dict handed to the AI client.

    Strictly facts only -- the AI client (real or mock) sees no fields we
    don't already have stored.
    """
    ctx: dict[str, Any] = {
        "company_name": lead.company_name,
        "website": lead.website,
        "industry": lead.industry,
        "contact_name": lead.contact_name,
        "contact_title": lead.contact_title,
        "contact_email": lead.contact_email,
        "company_size": lead.company_size,
        "location": lead.location,
        "source": lead.source,
    }
    if isinstance(lead.cleaned_data, dict):
        ctx["cleaned_data"] = lead.cleaned_data
    if lead.score is not None:
        ctx["score"] = {
            "total_score": lead.score.total_score,
            "priority": lead.score.priority,
        }
    return ctx


def _latest_summary_content(
    session: Session, lead_id: UUID
) -> dict[str, Any] | None:
    row = session.execute(
        select(AIOutput)
        .where(
            AIOutput.lead_id == lead_id,
            AIOutput.output_type == "company_summary",
        )
        .order_by(AIOutput.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    return dict(row.content) if isinstance(row.content, dict) else None


def generate_summary_for_lead(session: Session, lead: Lead) -> AIOutput:
    client = get_ai_client()
    ctx = _build_lead_context(lead)
    content = client.generate_company_summary(ctx)

    output = AIOutput(
        lead_id=lead.id,
        output_type="company_summary",
        content=content,
        model_used=_model_label(),
        prompt_version=PROMPT_VERSION,
    )
    session.add(output)
    session.add(
        WorkflowEvent(
            lead_id=lead.id,
            event_type="ai_summary_generated",
            event_data={
                "output_type": "company_summary",
                "model_used": _model_label(),
                "confidence": content.get("confidence"),
            },
        )
    )
    return output


def generate_outreach_for_lead(session: Session, lead: Lead) -> AIOutput:
    client = get_ai_client()
    ctx = _build_lead_context(lead)

    latest = _latest_summary_content(session, lead.id)
    if latest is not None:
        ctx["latest_summary"] = latest

    content = client.generate_outreach(ctx)

    output = AIOutput(
        lead_id=lead.id,
        output_type="outreach_email",
        content=content,
        model_used=_model_label(),
        prompt_version=PROMPT_VERSION,
    )
    session.add(output)
    session.add(
        WorkflowEvent(
            lead_id=lead.id,
            event_type="outreach_generated",
            event_data={
                "output_type": "outreach_email",
                "model_used": _model_label(),
                "confidence": content.get("confidence"),
            },
        )
    )
    return output
