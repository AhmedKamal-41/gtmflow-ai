"""Legacy v1 lead scoring applied to one lead (moved from app/api/scoring.py
in Phase 10 so the synchronous endpoints and background jobs share it;
behaviour unchanged)."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import Lead, LeadScore
from app.scoring.lead_scoring import DISQUALIFIED_STATUSES, score_lead


def apply_legacy_score(session: Session, lead: Lead) -> dict[str, Any]:
    """Run the scorer, upsert LeadScore, mark the lead as scored.

    matched_signals is merged into score_breakdown JSON for persistence;
    the API response splits them back out at the top level.
    """
    result = score_lead(lead)
    persisted_breakdown = {
        **result["score_breakdown"],
        "matched_signals": result["matched_signals"],
    }
    if lead.score is None:
        lead.score = LeadScore(
            lead_id=lead.id,
            total_score=result["total_score"],
            priority=result["priority"],
            score_breakdown=persisted_breakdown,
            reasoning=result["reasoning"],
        )
    else:
        lead.score.total_score = result["total_score"]
        lead.score.priority = result["priority"]
        lead.score.score_breakdown = persisted_breakdown
        lead.score.reasoning = result["reasoning"]
    # A blocked disposition (do_not_contact / disqualified / unsubscribed) is
    # not a workflow stage -- scoring must not overwrite it. Otherwise a
    # lead's blocked status is silently lost the moment it's scored, and the
    # push-time status check in services/integration_push.py would never see
    # it (see docs/engineering-log/audit.md D.2).
    if lead.status not in DISQUALIFIED_STATUSES:
        lead.status = "scored"
    return result
