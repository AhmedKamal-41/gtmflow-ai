"""Lead -> Slack push orchestration.

Caller is responsible for the request-level validation gates (lead exists,
integration_type=slack, lead is scored, lead is Hot OR force=true -- the
score threshold is the ONLY thing `force` can bypass). This module is the
single dispatch point for every route and enforces, before any payload is
built or the transport is touched:

  * the lead's status is not a blocked disposition (BlockedLeadError);
  * its batch import is complete (IncompleteImportError);
  * its current outreach draft carries an approval that still applies to
    that exact content and its current inputs (DeliveryNotApprovedError;
    see app/services/draft_review.py).

It then focuses on:

  * gathering the latest AI summary + outreach as context
  * building the Slack payload
  * dispatching via the slack integration helper
  * persisting the IntegrationPush row, WorkflowEvent, and Lead.status update
"""

from __future__ import annotations

from app.core.actor import actor_label

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.integrations.slack import (
    build_lead_url,
    build_slack_payload,
    send_slack_payload,
)
from app.models import AIOutput, IntegrationPush, Lead, WorkflowEvent
from app.models.integration_push import (
    PUSH_FAILED,
    PUSH_MOCK_SUCCESS,
    PUSH_PENDING,
    PUSH_SUCCESS,
    PUSH_UNKNOWN,
)
from app.models.ai_output import PURPOSE_OPERATIONAL
from app.models.lead_batch import INCOMPLETE_BATCH_STATUSES
from app.scoring.lead_scoring import DISQUALIFIED_STATUSES
from app.services.draft_review import review_state

SLACK = "slack"
SUCCESS_STATUSES = ("success", "mock_success")
BLOCKED_STATUSES = DISQUALIFIED_STATUSES
# A claim older than this without an outcome belongs to a holder that died
# mid-send: it becomes "unknown", never silently re-sent. Far longer than the
# 10 s Slack timeout.
CLAIM_STALE_SECONDS = 120


class BlockedLeadError(RuntimeError):
    """Raised when a lead's persisted status forbids outreach delivery.

    Applies regardless of score, the ``force`` flag, or mock/real mode --
    this is a hard stop, not something a caller can override. Raised from
    the single dispatch point (``push_lead_to_slack``) shared by both the
    single-lead and batch push routes, so neither can bypass it.
    """

    def __init__(self, lead_id: UUID, lead_status: str) -> None:
        self.lead_id = lead_id
        self.lead_status = lead_status
        super().__init__(
            f"Lead {lead_id} has status '{lead_status}' and cannot be pushed "
            "to any integration."
        )


class IncompleteImportError(RuntimeError):
    """The lead's batch is only partially imported. Not overridable."""

    def __init__(self, lead_id: UUID, batch_status: str) -> None:
        self.lead_id = lead_id
        self.batch_status = batch_status
        super().__init__(
            f"Lead {lead_id} belongs to a batch that is only partially imported "
            f"(status='{batch_status}')."
        )


class DeliveryNotApprovedError(RuntimeError):
    """The current draft has no approval that applies to its exact content
    and current inputs. Not overridable by force."""

    def __init__(self, lead_id: UUID, blockers: list[str]) -> None:
        self.lead_id = lead_id
        self.blockers = blockers
        super().__init__(
            "Delivery needs a current approval of the exact outreach draft: "
            + ", ".join(blockers)
        )


def _block(session: Session, lead: Lead, reason: str, **details: Any) -> None:
    session.add(
        WorkflowEvent(
            lead_id=lead.id,
            event_type="lead_push_blocked",
            event_data={
                "integration_type": SLACK,
                "reason": reason,
                "lead_status": lead.status,
                "priority": lead.score.priority if lead.score else None,
                "score": lead.score.total_score if lead.score else None,
                **details,
            },
        )
    )


def _latest_output_content(
    session: Session, lead_id: UUID, output_type: str
) -> dict[str, Any] | None:
    row = session.execute(
        select(AIOutput)
        .where(
            AIOutput.lead_id == lead_id,
            AIOutput.output_type == output_type,
            AIOutput.purpose == PURPOSE_OPERATIONAL,
        )
        .order_by(AIOutput.created_at.desc(), AIOutput.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    return dict(row.content) if isinstance(row.content, dict) else None


def lead_has_successful_slack_push(session: Session, lead_id: UUID) -> bool:
    row = session.execute(
        select(IntegrationPush.id)
        .where(
            IntegrationPush.lead_id == lead_id,
            IntegrationPush.integration_type == SLACK,
            IntegrationPush.status.in_(SUCCESS_STATUSES),
        )
        .limit(1)
    ).first()
    return row is not None


class DeliveryInProgressError(RuntimeError):
    """Another request, worker or restart holds the claim for this exact
    approved draft attempt and has not recorded an outcome yet."""

    def __init__(self, push: IntegrationPush) -> None:
        self.push = push
        super().__init__(f"Delivery {push.id} of this approved draft is in progress.")


class DeliveryOutcomeUnknownError(RuntimeError):
    """The last attempt may have reached Slack. Nothing is resent until an
    operator resolves it (resolve_unknown_delivery)."""

    def __init__(self, push: IntegrationPush) -> None:
        self.push = push
        super().__init__(
            f"Delivery {push.id} has an unknown outcome: it may have reached Slack. "
            "Check the channel, then resolve it as delivered or not delivered."
        )


@dataclass
class DispatchResult:
    push: IntegrationPush
    sent: bool    # this call attempted a send (real or mock)
    replay: bool  # an earlier successful delivery of this exact draft was returned


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _latest_attempt(session: Session, output_id: UUID, content_hash: str) -> IntegrationPush | None:
    return session.execute(
        select(IntegrationPush)
        .where(IntegrationPush.approved_output_id == output_id,
               IntegrationPush.approved_content_hash == content_hash,
               IntegrationPush.delivery_key.is_not(None))
        .order_by(IntegrationPush.attempt.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _gate(session: Session, lead: Lead):
    """The unchanged delivery rules, checked before anything is claimed."""
    assert lead.score is not None, "push_lead_to_slack requires a scored lead"
    if lead.status in BLOCKED_STATUSES:
        _block(session, lead, "blocked_lead_status")
        raise BlockedLeadError(lead.id, lead.status)
    batch = lead.batch
    if batch is not None and batch.status in INCOMPLETE_BATCH_STATUSES:
        _block(session, lead, "incomplete_import", batch_status=batch.status)
        raise IncompleteImportError(lead.id, batch.status)
    state = review_state(session, lead)
    if not state.approval_applicable:
        _block(session, lead, "no_applicable_approval", blockers=state.delivery_blockers)
        raise DeliveryNotApprovedError(lead.id, state.delivery_blockers)
    assert state.draft is not None
    return state


def _payload(session: Session, lead: Lead, approved_draft: AIOutput) -> dict[str, Any]:
    summary = _latest_output_content(session, lead.id, "company_summary")
    outreach = dict(approved_draft.content)
    detected_pains: list[str] = []
    fit_reasoning: str | None = lead.score.reasoning
    if summary:
        raw_pains = summary.get("detected_pain_points")
        if isinstance(raw_pains, list):
            detected_pains = [str(p) for p in raw_pains if p]
        if summary.get("fit_reasoning"):
            fit_reasoning = str(summary["fit_reasoning"])
    outreach_subject: str | None = None
    call_note: str | None = None
    if outreach:
        if outreach.get("subject"):
            outreach_subject = str(outreach["subject"])
            if approved_draft.seller_profile_kind == "demo":
                outreach_subject = f"[Demonstration] {outreach_subject}"
        if outreach.get("call_note"):
            call_note = str(outreach["call_note"])
    return build_slack_payload(
        lead_id=lead.id,
        company_name=lead.company_name,
        score=lead.score.total_score,
        priority=lead.score.priority,
        industry=lead.industry,
        contact_name=lead.contact_name,
        contact_title=lead.contact_title,
        contact_email=lead.contact_email,
        fit_reasoning=fit_reasoning,
        detected_pain_points=detected_pains or None,
        outreach_subject=outreach_subject,
        call_note=call_note,
        lead_url=build_lead_url(lead.id),
    )


def deliver_lead_to_slack(session: Session, lead: Lead, *, redeliver: bool = False,
                          actor: str = "api") -> DispatchResult:
    """The single Slack dispatch point (Phase 11 delivery ledger).

    1. The unchanged gates: blocked status, incomplete import, a current
       approval of the exact draft. Nothing is claimed or sent otherwise.
    2. The ledger for this exact approved draft and content:
       * a delivered attempt is returned as a replay (no send) unless
         `redeliver` is explicitly requested;
       * a pending claim younger than CLAIM_STALE_SECONDS -> in progress;
       * an older pending claim (its holder died mid-send) -> unknown;
       * an unknown outcome blocks every send until an operator resolves it;
       * a failed (definitely not delivered) attempt may be retried by an
         explicit new request -- never automatically.
    3. Claim the next attempt: insert its unique `delivery_key` inside a
       savepoint and COMMIT before sending. A concurrent claimer gets the
       unique-key conflict and sends nothing.
    4. Send, then record the outcome with a conditional update.

    Commits (the claim and the outcome); callers may commit again harmlessly.
    Guarantee: at most one send per claimed attempt. Slack incoming webhooks
    take no idempotency key, so an attempt whose outcome is unknown cannot be
    deduplicated on Slack's side; that is why it is never resent blindly.
    """
    state = _gate(session, lead)
    draft, content_hash = state.draft, state.draft_content_hash
    latest = _latest_attempt(session, draft.id, content_hash)
    if latest is not None:
        if latest.status == PUSH_PENDING:
            if _now() - _aware(latest.claimed_at) < timedelta(seconds=CLAIM_STALE_SECONDS):
                raise DeliveryInProgressError(latest)
            expired = session.execute(
                update(IntegrationPush)
                .where(IntegrationPush.id == latest.id, IntegrationPush.status == PUSH_PENDING)
                .values(status=PUSH_UNKNOWN, outcome_code="claim_expired", completed_at=_now(),
                        response_text="The claim holder stopped before recording an outcome; "
                                      "the message may or may not have been sent.")
                .execution_options(synchronize_session=False)
            )
            if expired.rowcount == 1:
                session.add(WorkflowEvent(lead_id=lead.id, event_type="push_outcome_unknown", event_data={
                    "push_id": str(latest.id), "reason": "claim_expired", "claimed_by": latest.claimed_by}))
            session.commit()
            session.refresh(latest)
            if latest.status == PUSH_UNKNOWN:
                raise DeliveryOutcomeUnknownError(latest)
        if latest.status == PUSH_UNKNOWN:
            raise DeliveryOutcomeUnknownError(latest)
        if latest.status in SUCCESS_STATUSES and not redeliver:
            return DispatchResult(latest, sent=False, replay=True)

    attempt = (latest.attempt if latest is not None else 0) + 1
    mode = "real" if settings.slack_webhook_url else "mock"
    push = IntegrationPush(
        lead_id=lead.id, integration_type=SLACK, payload=_payload(session, lead, draft),
        status=PUSH_PENDING, delivery_key=f"slack:{draft.id}:{content_hash}:{attempt}",
        approved_output_id=draft.id, approved_content_hash=content_hash, attempt=attempt,
        delivery_mode=mode, claimed_by=actor_label(), claimed_at=_now(),
    )
    try:
        with session.begin_nested():
            session.add(push)
            session.flush()
    except IntegrityError:
        # Someone else claimed this exact attempt first: never send twice.
        other = _latest_attempt(session, draft.id, content_hash)
        session.commit()
        if other is not None and other.status in SUCCESS_STATUSES and not redeliver:
            return DispatchResult(other, sent=False, replay=True)
        if other is not None and other.status == PUSH_UNKNOWN:
            raise DeliveryOutcomeUnknownError(other) from None
        raise DeliveryInProgressError(other) from None
    session.commit()  # the claim is durable before any byte is sent

    status, response_text = send_slack_payload(settings.slack_webhook_url, push.payload)
    outcome = {PUSH_SUCCESS: "delivered", PUSH_MOCK_SUCCESS: "mock_delivered",
               PUSH_FAILED: "not_delivered", PUSH_UNKNOWN: "outcome_unknown"}.get(status, "unexpected_status")
    recorded = session.execute(
        update(IntegrationPush)
        .where(IntegrationPush.id == push.id, IntegrationPush.status == PUSH_PENDING)
        .values(status=status, response_text=response_text, completed_at=_now(), outcome_code=outcome)
        .execution_options(synchronize_session=False)
    )
    session.refresh(push)
    event_data = {
        "integration_type": SLACK,
        "status": status,
        "priority": lead.score.priority,
        "score": lead.score.total_score,
        "approved_output_id": str(draft.id),
        "approved_content_hash": content_hash,
        "approval_review_id": str(state.review.id) if state.review else None,
        "seller_profile_kind": draft.seller_profile_kind,
        "push_id": str(push.id),
        "attempt": attempt,
        "delivery_mode": mode,
        "redeliver": redeliver,
        "action_source": actor,
    }
    if recorded.rowcount != 1:
        # Our claim was declared unknown while we were sending (we exceeded
        # CLAIM_STALE_SECONDS). Keep the row as it is; record what we learned.
        session.add(WorkflowEvent(lead_id=lead.id, event_type="push_late_outcome", event_data=event_data))
    else:
        session.add(WorkflowEvent(lead_id=lead.id, event_type="lead_pushed", event_data=event_data))
        if status in SUCCESS_STATUSES and lead.status not in BLOCKED_STATUSES:
            lead.status = "pushed"
    session.commit()
    session.refresh(push)
    return DispatchResult(push, sent=True, replay=False)


def push_lead_to_slack(session: Session, lead: Lead) -> IntegrationPush:
    """Backwards-compatible wrapper: deliver (or replay) the current
    approved draft and return its push row. Raises BlockedLeadError,
    IncompleteImportError, DeliveryNotApprovedError, DeliveryInProgressError
    or DeliveryOutcomeUnknownError; see deliver_lead_to_slack."""
    return deliver_lead_to_slack(session, lead).push


RESOLVED_DELIVERED = "confirmed_delivered"
RESOLVED_NOT_DELIVERED = "confirmed_not_delivered"


class ResolutionError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code, self.detail = status_code, detail
        super().__init__(detail)


def resolve_unknown_delivery(session: Session, push: IntegrationPush, resolution: str,
                             note: str | None, operator_label: str) -> IntegrationPush:
    """An operator records what they found in the Slack channel. Only an
    `unknown` attempt can be resolved; the original outcome code is kept.
    Delivered -> success (or mock_success for a mock-mode attempt);
    not delivered -> failed, which allows an explicit new attempt."""
    if resolution not in (RESOLVED_DELIVERED, RESOLVED_NOT_DELIVERED):
        raise ResolutionError(400, "resolution must be confirmed_delivered or confirmed_not_delivered")
    if push.status != PUSH_UNKNOWN:
        raise ResolutionError(409, f"Only a delivery with an unknown outcome can be resolved (this one is '{push.status}').")
    delivered = PUSH_MOCK_SUCCESS if push.delivery_mode == "mock" else PUSH_SUCCESS
    new_status = delivered if resolution == RESOLVED_DELIVERED else PUSH_FAILED
    result = session.execute(
        update(IntegrationPush)
        .where(IntegrationPush.id == push.id, IntegrationPush.status == PUSH_UNKNOWN)
        .values(status=new_status, resolution=resolution, resolution_note=(note or None) and note[:500],
                resolved_at=_now())
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise ResolutionError(409, "This delivery was resolved by someone else first. Reload.")
    session.add(WorkflowEvent(lead_id=push.lead_id, event_type="push_outcome_resolved", event_data={
        "push_id": str(push.id), "resolution": resolution, "status": new_status,
        "operator_label": operator_label, "note": note}))
    lead = session.get(Lead, push.lead_id)
    if new_status in SUCCESS_STATUSES and lead is not None and lead.status not in BLOCKED_STATUSES:
        lead.status = "pushed"
    session.commit()
    session.refresh(push)
    return push
