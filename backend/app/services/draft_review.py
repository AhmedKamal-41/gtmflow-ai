"""Phase 6: exact-draft review state, review decisions and human revisions.

One definition, used by the review endpoints, readiness and Slack delivery,
of whether a lead's current outreach draft is approved *in a way that
still applies*:

* The draft is the lead's current operational outreach draft (newest by
  `created_at DESC, id DESC`; annotation outputs never count).
* Its latest operational review is an approval that names this draft's
  exact content hash (reviews from before Phase 6 name no content and never
  authorize anything).
* It was produced by grounded generation from a seller that is still in
  force (the active revision, or the unchanged built-in demo profile), and
  rebuilding its input context today gives the same `input_hash` -- i.e.
  company facts, fit, restrictions and seller content are unchanged.

Anything else yields ordered blocker codes (the readiness gap codes in
app/scoring/fit.py). A human edit is a new immutable output row, so it
supersedes the draft it revises and needs its own review; an older
approval stays in history but can't authorize different content.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.hashing import content_hash  # noqa: F401 -- re-exported
from app.ai import quality_checks
from app.ai.grounding import (
    AIOutputValidationError,
    describe_details,
    validate_outreach,
    validate_summary,
)
from app.models import AIOutput, AIOutputReview, Lead, WorkflowEvent
from app.models.ai_output import ORIGIN_HUMAN_EDITED, PURPOSE_OPERATIONAL
from app.models.ai_output_review import REVIEW_KIND_OPERATIONAL_OUTREACH
from app.scoring import fit
from app.scoring.lead_scoring import DISQUALIFIED_STATUSES

OUTREACH = "outreach_email"
SUMMARY = "company_summary"
GROUNDED_SCHEMA = "v2"

DECISION_APPROVED = "approved"
DECISION_REJECTED = "rejected"

STATUS_NO_DRAFT = "no_draft"
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_SUPERSEDED = "superseded"

# No authentication exists (docs/upgrade/audit.md F.1): every review and
# human revision is stamped with this honest label. Request bodies that try
# to supply a name are rejected by the schemas (extra="forbid").
REVIEWER_LABEL = "local-demo-unauthenticated"
HUMAN_EDIT_MODEL = "human_edit"


class ReviewError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


# ------------------------------------------------------------ queries

def operational_outreach_clause():
    return and_(
        AIOutput.output_type == OUTREACH,
        AIOutput.purpose == PURPOSE_OPERATIONAL,
    )


def newer_draft_clause(output: AIOutput):
    """Operational outreach rows for the same lead that sort after `output`
    under `created_at DESC, id DESC` -- drafts that supersede it."""
    return and_(
        AIOutput.lead_id == output.lead_id,
        operational_outreach_clause(),
        or_(
            AIOutput.created_at > output.created_at,
            and_(AIOutput.created_at == output.created_at, AIOutput.id > output.id),
        ),
    )


def current_drafts_by_lead(session: Session, lead_ids: list[UUID]) -> dict[UUID, AIOutput]:
    if not lead_ids:
        return {}
    rn = func.row_number().over(
        partition_by=AIOutput.lead_id,
        order_by=(AIOutput.created_at.desc(), AIOutput.id.desc()),
    ).label("rn")
    ranked = (
        select(AIOutput.id, rn)
        .where(AIOutput.lead_id.in_(lead_ids), operational_outreach_clause())
        .subquery()
    )
    rows = session.execute(
        select(AIOutput).join(ranked, AIOutput.id == ranked.c.id).where(ranked.c.rn == 1)
    ).scalars().all()
    return {row.lead_id: row for row in rows}


def latest_reviews_by_output(session: Session, output_ids: list[UUID]) -> dict[UUID, AIOutputReview]:
    if not output_ids:
        return {}
    rn = func.row_number().over(
        partition_by=AIOutputReview.ai_output_id,
        order_by=(AIOutputReview.created_at.desc(), AIOutputReview.id.desc()),
    ).label("rn")
    ranked = (
        select(AIOutputReview.id, rn)
        .where(
            AIOutputReview.ai_output_id.in_(output_ids),
            AIOutputReview.review_kind == REVIEW_KIND_OPERATIONAL_OUTREACH,
        )
        .subquery()
    )
    rows = session.execute(
        select(AIOutputReview).join(ranked, AIOutputReview.id == ranked.c.id).where(ranked.c.rn == 1)
    ).scalars().all()
    return {row.ai_output_id: row for row in rows}


# ------------------------------------------------------------ state

@dataclass
class ReviewState:
    lead_id: UUID
    draft: AIOutput | None
    draft_content_hash: str | None
    review: AIOutputReview | None
    status: str
    delivery_blockers: list[str] = field(default_factory=list)
    email_blockers: list[str] = field(default_factory=list)

    @property
    def approval_applicable(self) -> bool:
        return self.draft is not None and not self.delivery_blockers


def review_states(session: Session, leads: list[Lead]) -> dict[UUID, ReviewState]:
    """Bounded: active seller, current drafts, their latest reviews and the
    leads' current fit rows -- four queries for any page of leads."""
    from app.services import fit_queries
    from app.services.ai_generation import (
        current_input_hash,
        resolve_active_seller,
        seller_context_for_output,
    )

    lead_ids = [lead.id for lead in leads]
    active = resolve_active_seller(session)
    drafts = current_drafts_by_lead(session, lead_ids)
    reviews = latest_reviews_by_output(session, [d.id for d in drafts.values()])
    fits = fit_queries.latest_fit_scores_by_lead(session, list(drafts))

    states: dict[UUID, ReviewState] = {}
    for lead in leads:
        draft = drafts.get(lead.id)
        if draft is None:
            blockers = [fit.GAP_NO_OUTREACH_DRAFT]
            states[lead.id] = ReviewState(lead.id, None, None, None, STATUS_NO_DRAFT, blockers, list(blockers))
            continue
        draft_hash = content_hash(draft.content)
        blockers: list[str] = []
        seller = (
            seller_context_for_output(draft, active)
            if draft.output_schema_version == GROUNDED_SCHEMA
            else None
        )
        if seller is None:
            blockers.append(fit.GAP_DRAFT_NOT_FROM_ACTIVE_PROFILE)
        elif current_input_hash(lead, OUTREACH, seller, fits.get(lead.id)) != draft.input_hash:
            blockers.append(fit.GAP_DRAFT_INPUTS_CHANGED)

        review = reviews.get(draft.id)
        if review is None:
            status = STATUS_PENDING
            blockers.append(fit.GAP_DRAFT_NOT_REVIEWED)
        elif review.decision == DECISION_REJECTED:
            status = STATUS_REJECTED
            blockers.append(fit.GAP_DRAFT_REJECTED)
        else:
            status = STATUS_APPROVED
            if review.content_hash != draft_hash:
                blockers.append(fit.GAP_APPROVAL_WITHOUT_CONTENT_IDENTITY)

        email_blockers = list(blockers)
        drafted_from_active = active is not None and draft.seller_profile_id == active.profile_id
        if (
            active is not None
            and not drafted_from_active
            and fit.GAP_DRAFT_NOT_FROM_ACTIVE_PROFILE not in email_blockers
        ):
            email_blockers.insert(0, fit.GAP_DRAFT_NOT_FROM_ACTIVE_PROFILE)
        states[lead.id] = ReviewState(
            lead.id, draft, draft_hash, review, status, blockers, email_blockers
        )
    return states


def review_state(session: Session, lead: Lead) -> ReviewState:
    return review_states(session, [lead])[lead.id]


def output_statuses(session: Session, outputs: list[AIOutput]) -> dict[UUID, str | None]:
    """Per-row operational review status for history lists: `superseded`
    for any outreach draft that is no longer current, otherwise the latest
    decision or `pending`. None for summaries and annotation outputs."""
    drafts = [o for o in outputs if o.output_type == OUTREACH and o.purpose == PURPOSE_OPERATIONAL]
    current = current_drafts_by_lead(session, list({o.lead_id for o in drafts}))
    reviews = latest_reviews_by_output(session, [o.id for o in drafts])
    statuses: dict[UUID, str | None] = {o.id: None for o in outputs}
    for output in drafts:
        if current.get(output.lead_id) is None or current[output.lead_id].id != output.id:
            statuses[output.id] = STATUS_SUPERSEDED
        elif output.id in reviews:
            statuses[output.id] = (
                STATUS_REJECTED if reviews[output.id].decision == DECISION_REJECTED else STATUS_APPROVED
            )
        else:
            statuses[output.id] = STATUS_PENDING
    return statuses


# ------------------------------------------------------------ review

def resolve_reviewable_draft(
    session: Session, lead: Lead, ai_output_id: UUID, displayed_hash: str
) -> AIOutput:
    """The exact draft a review targets, or a ReviewError explaining why it
    can't be reviewed: missing (404), another lead's or not an operational
    outreach draft (400), content different from what was displayed (409),
    or superseded by a newer draft (409)."""
    output = session.get(AIOutput, ai_output_id)
    if output is None:
        raise ReviewError(404, f"AI output {ai_output_id} not found.")
    if output.lead_id != lead.id:
        raise ReviewError(400, "This output belongs to a different lead. Refusing to review it as this lead's outreach.")
    if output.output_type != OUTREACH:
        raise ReviewError(400, f"Output type '{output.output_type}' is not outreach and cannot be approved or rejected as outreach.")
    if output.purpose != PURPOSE_OPERATIONAL:
        raise ReviewError(400, "This output is a training-annotation candidate, not an operational draft. Annotation and delivery approval are separate.")
    if content_hash(output.content) != displayed_hash:
        raise ReviewError(409, "The content you reviewed does not match this draft. Reload and review the draft as it is now.")
    if session.execute(select(AIOutput.id).where(newer_draft_clause(output)).limit(1)).first():
        raise ReviewError(409, "This draft has been superseded by a newer outreach draft for this lead. Refresh and review the latest version.")
    return output


def apply_review(
    session: Session,
    *,
    lead: Lead,
    output: AIOutput,
    decision: str,
    reason: str | None = None,
    reviewer_label: str = REVIEWER_LABEL,
    acknowledged_quality_flags: list[str] | None = None,
) -> tuple[AIOutputReview, bool]:
    """Record a decision on the exact draft (and content) displayed.

    An identical repeat of the current decision is an idempotent no-op; a
    changed decision is a new row and its own event. A blocked lead's status
    is a hard disposition and is never overwritten (Phase 3)."""
    latest = latest_reviews_by_output(session, [output.id]).get(output.id)
    draft_hash = content_hash(output.content)
    if lead.status not in DISQUALIFIED_STATUSES:
        lead.status = "outreach_approved" if decision == DECISION_APPROVED else "outreach_rejected"
    if latest is not None and latest.decision == decision and latest.content_hash == draft_hash:
        return latest, True

    review = AIOutputReview(
        lead_id=lead.id,
        ai_output_id=output.id,
        decision=decision,
        reason=reason,
        review_kind=REVIEW_KIND_OPERATIONAL_OUTREACH,
        reviewer_label=reviewer_label,
        legacy_unlinked=False,
        content_hash=draft_hash,
    )
    session.add(review)
    session.add(WorkflowEvent(
        lead_id=lead.id,
        event_type="outreach_approved" if decision == DECISION_APPROVED else "outreach_rejected",
        event_data={
            "ai_output_id": str(output.id),
            "output_type": OUTREACH,
            "lead_id": str(lead.id),
            "content_hash": draft_hash,
            "origin": output.origin,
            "reviewer_label": reviewer_label,
            **({"reason": reason} if reason is not None else {}),
            **({"acknowledged_quality_flags": acknowledged_quality_flags,
                "quality_checks_version": quality_checks.CHECKS_VERSION}
               if acknowledged_quality_flags is not None else {}),
        },
    ))
    # Visible to later checks in the same transaction (autoflush is off),
    # e.g. the demo approving and then pushing.
    session.flush()
    return review, False


# ------------------------------------------------------------ revisions

def create_revision(
    session: Session,
    output: AIOutput,
    *,
    expected_content_hash: str,
    content: dict[str, Any],
    require_current: bool = True,
) -> tuple[AIOutput, bool]:
    """A human correction as a NEW immutable row (origin `human_edited`,
    `parent_output_id` -> the revised row). The model response is never
    changed. The corrected content must pass the same versioned schema and
    grounding checks against the parent's recorded input. An identical
    retry returns the revision already made."""
    if content_hash(output.content) != expected_content_hash:
        raise ReviewError(409, "The output changed since you opened it. Reload before editing.")
    if output.output_schema_version != GROUNDED_SCHEMA or not output.input_snapshot:
        raise ReviewError(409, "Only outputs from grounded generation (schema v2) can be revised. Generate a new draft first.")
    validate = validate_summary if output.output_type == SUMMARY else validate_outreach
    try:
        validated = validate(content, output.input_snapshot)
    except AIOutputValidationError as error:
        raise ReviewError(
            422,
            "The edited content failed validation ("
            + ", ".join(error.reason_codes)
            + "): "
            + describe_details(error.details)
            + ". Nothing was saved.",
        ) from None
    new_hash = content_hash(validated)
    if new_hash == content_hash(output.content):
        raise ReviewError(400, "The edited content is identical to the current revision. Nothing was saved.")

    # An identical retry returns the revision it already made -- checked
    # before supersession, since that revision is what superseded `output`.
    for child in session.scalars(
        select(AIOutput).where(
            AIOutput.parent_output_id == output.id,
            AIOutput.origin == ORIGIN_HUMAN_EDITED,
        )
    ):
        if content_hash(child.content) == new_hash:
            return child, False
    if (
        require_current
        and output.output_type == OUTREACH
        and output.purpose == PURPOSE_OPERATIONAL
        and session.execute(select(AIOutput.id).where(newer_draft_clause(output)).limit(1)).first()
    ):
        raise ReviewError(409, "This draft has been superseded by a newer outreach draft. Edit the latest version.")

    revision = AIOutput(
        lead_id=output.lead_id,
        output_type=output.output_type,
        content=validated,
        model_used=HUMAN_EDIT_MODEL,
        prompt_version=output.prompt_version,
        origin=ORIGIN_HUMAN_EDITED,
        parent_output_id=output.id,
        input_snapshot=output.input_snapshot,
        input_hash=output.input_hash,
        output_schema_version=output.output_schema_version,
        model_revision=None,
        seller_profile_id=output.seller_profile_id,
        seller_profile_version=output.seller_profile_version,
        seller_profile_content_hash=output.seller_profile_content_hash,
        seller_profile_kind=output.seller_profile_kind,
        purpose=output.purpose,
        author_label=REVIEWER_LABEL,
    )
    session.add(revision)
    session.flush()
    session.add(WorkflowEvent(
        lead_id=output.lead_id,
        event_type="ai_output_revised",
        event_data={
            "ai_output_id": str(revision.id),
            "parent_output_id": str(output.id),
            "output_type": output.output_type,
            "purpose": output.purpose,
            "parent_content_hash": expected_content_hash,
            "content_hash": new_hash,
            "author_label": REVIEWER_LABEL,
        },
    ))
    return revision, True
