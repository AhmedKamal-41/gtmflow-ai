"""Phase 6: training-annotation workbench service.

Separate from operational review in both directions:
* candidate outputs are `purpose="annotation"` rows -- never a lead's
  current draft, never approvable for delivery;
* an annotation writes `training_annotations` only; it creates no
  `AIOutputReview` and changes no lead status.

A candidate becomes a human-reviewed training example only through an
explicit submission naming the exact output and content hash displayed.
Nothing here approves candidates in bulk.
"""
from __future__ import annotations

from collections import Counter
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.client import AIClient, AIConfigError, get_ai_client
from app.core.config import settings
from app.core.hashing import content_hash
from app.models import (
    AIOutput,
    AnnotationCandidate,
    Lead,
    TrainingAnnotation,
    WorkflowEvent,
)
from app.models.ai_output import PURPOSE_ANNOTATION
from app.models.annotation import (
    DECISION_ACCEPTED,
    DECISION_CORRECTED,
    DECISION_SKIPPED,
    TASK_OUTREACH,
    TASK_SUMMARY,
)
from app.schemas.annotation import AnnotationSubmit
from app.services.ai_generation import _generate, resolve_active_seller
from app.services.draft_review import REVIEWER_LABEL, ReviewError, create_revision
from app.services.splits import EXPERIMENT_TARGETS

STATUS_AWAITING_GENERATION = "awaiting_generation"
STATUS_PENDING_REVIEW = "pending_review"
EXPORT_FORMAT_VERSION = "annotation-export-v1"


class AnnotationError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


# ------------------------------------------------------------ provider

def configured_provider() -> dict[str, Any]:
    """Which provider generation would use, without contacting it."""
    name = "mock" if settings.use_mock_ai else "openai"
    try:
        client = get_ai_client()
    except AIConfigError as error:
        return {"configured_provider": name, "model_revision": None, "is_mock": False,
                "available": False, "detail": str(error)}
    detail = (
        "Deterministic mock generator. Candidates are labeled mock through review and export."
        if client.name == "mock"
        else "Real provider. Each generation is a paid API call."
    )
    return {"configured_provider": client.name, "model_revision": client.model_revision,
            "is_mock": client.name == "mock", "available": True, "detail": detail}


def generate_candidate(
    session: Session, candidate: AnnotationCandidate, provider: str,
    client: AIClient | None = None,
) -> AIOutput:
    """Generate the candidate's output with the provider the caller names,
    which must be the configured one. Attached once; never replaced."""
    if candidate.source_output_id is not None:
        raise AnnotationError(409, "This candidate already has a generated output; it is never replaced.")
    client = client if client is not None else get_ai_client()
    if client.name != provider:
        raise AnnotationError(
            409, f"Requested provider '{provider}' but the configured provider is '{client.name}'. Nothing was generated.",
        )
    seller = resolve_active_seller(session)
    if candidate.task == TASK_OUTREACH and seller is None:
        raise AnnotationError(
            409, "Outreach candidates need an active seller profile revision. Activate one on the Seller page first.",
        )
    lead = session.get(Lead, candidate.lead_id)
    event = "ai_summary_generated" if candidate.task == TASK_SUMMARY else "outreach_generated"
    output = _generate(session, lead, candidate.task, event, seller, purpose=PURPOSE_ANNOTATION, client=client)
    candidate.source_output_id = output.id
    session.add(WorkflowEvent(lead_id=lead.id, event_type="annotation_candidate_generated", event_data={
        "candidate_id": str(candidate.id), "queue": candidate.queue, "ai_output_id": str(output.id),
        "provider": client.name, "model_revision": client.model_revision,
    }))
    return output


# ------------------------------------------------------------ status

def latest_annotations(session: Session, candidate_ids: list[UUID]) -> dict[UUID, TrainingAnnotation]:
    if not candidate_ids:
        return {}
    rn = func.row_number().over(
        partition_by=TrainingAnnotation.candidate_id,
        order_by=(TrainingAnnotation.created_at.desc(), TrainingAnnotation.id.desc()),
    ).label("rn")
    ranked = (
        select(TrainingAnnotation.id, rn)
        .where(TrainingAnnotation.candidate_id.in_(candidate_ids))
        .subquery()
    )
    rows = session.scalars(
        select(TrainingAnnotation).join(ranked, TrainingAnnotation.id == ranked.c.id).where(ranked.c.rn == 1)
    ).all()
    return {row.candidate_id: row for row in rows}


def candidate_status(candidate: AnnotationCandidate, latest: TrainingAnnotation | None) -> str:
    if candidate.source_output_id is None:
        return STATUS_AWAITING_GENERATION
    if latest is None or latest.source_output_id != candidate.source_output_id:
        return STATUS_PENDING_REVIEW
    return latest.decision


def is_mock_output(output: AIOutput | None) -> bool | None:
    return None if output is None else output.model_used == "mock"


# ------------------------------------------------------------ submit

def submit_annotation(
    session: Session, candidate: AnnotationCandidate, request: AnnotationSubmit
) -> tuple[TrainingAnnotation, bool]:
    existing = session.scalar(
        select(TrainingAnnotation).where(TrainingAnnotation.submission_id == request.submission_id)
    )
    if existing is not None:
        if existing.candidate_id != candidate.id:
            raise AnnotationError(409, "This submission id was already used for a different candidate.")
        return existing, False
    if candidate.source_output_id is None:
        raise AnnotationError(409, "This candidate has no generated output to annotate yet.")
    if request.source_output_id != candidate.source_output_id:
        raise AnnotationError(409, "The output you reviewed is not this candidate's output. Reload the candidate.")
    source = session.get(AIOutput, candidate.source_output_id)
    if content_hash(source.content) != request.source_content_hash:
        raise AnnotationError(409, "The content you reviewed does not match the candidate's output. Reload the candidate.")

    target_id = None
    target_hash = None
    if request.decision == DECISION_ACCEPTED:
        target_id, target_hash = source.id, request.source_content_hash
    elif request.decision == DECISION_CORRECTED:
        try:
            revision, _ = create_revision(
                session, source,
                expected_content_hash=request.source_content_hash,
                content=request.corrected_content or {},
                require_current=False,
            )
        except ReviewError as error:
            raise AnnotationError(error.status_code, error.detail) from None
        target_id, target_hash = revision.id, content_hash(revision.content)

    timing = None
    if request.timing is not None:
        timing = request.timing.model_dump()
        timing["source"] = "ui"
        # Incomplete: no user interaction was observed, or no active time.
        timing["incomplete"] = request.timing.interaction_count == 0 or request.timing.active_ms == 0

    row = TrainingAnnotation(
        submission_id=request.submission_id,
        candidate_id=candidate.id,
        source_output_id=source.id,
        source_content_hash=request.source_content_hash,
        decision=request.decision,
        target_output_id=target_id,
        target_content_hash=target_hash,
        factual_support=request.factual_support,
        writing_quality=request.writing_quality,
        missing_info_handling=request.missing_info_handling,
        notes=request.notes,
        skip_reason=request.skip_reason,
        reviewer_label=REVIEWER_LABEL,
        review_mode={"accepted": "accept", "corrected": "correct", "skipped": "skip"}[request.decision],
        timing=timing,
    )
    session.add(row)
    session.flush()
    session.add(WorkflowEvent(lead_id=candidate.lead_id, event_type="training_annotation_recorded", event_data={
        "annotation_id": str(row.id), "candidate_id": str(candidate.id), "queue": candidate.queue,
        "decision": row.decision, "source_output_id": str(source.id),
        "target_output_id": str(target_id) if target_id else None,
        "reviewer_label": REVIEWER_LABEL,
    }))
    return row, True


# ------------------------------------------------------------ summary + export

def queue_summary(session: Session, queue: str) -> dict[str, Any]:
    candidates = list(session.scalars(select(AnnotationCandidate).where(AnnotationCandidate.queue == queue)))
    latest = latest_annotations(session, [c.id for c in candidates])
    outputs = {
        o.id: o for o in session.scalars(
            select(AIOutput).where(AIOutput.id.in_([c.source_output_id for c in candidates if c.source_output_id]))
        )
    }
    statuses = {c.id: candidate_status(c, latest.get(c.id)) for c in candidates}
    counts = Counter(statuses.values())
    reviewed = [c for c in candidates if statuses[c.id] in (DECISION_ACCEPTED, DECISION_CORRECTED)]
    return {
        "queue": queue,
        "manifest_version": candidates[0].manifest_version if candidates else None,
        "candidates": len(candidates),
        "unique_companies": len({c.lead_id for c in candidates}),
        "generated": sum(1 for c in candidates if c.source_output_id),
        "awaiting_generation": counts[STATUS_AWAITING_GENERATION],
        "pending_review": counts[STATUS_PENDING_REVIEW],
        "reviewed_examples": len(reviewed),
        "reviewed_unique_companies": len({c.lead_id for c in reviewed}),
        "accepted": counts[DECISION_ACCEPTED],
        "corrected": counts[DECISION_CORRECTED],
        "skipped": counts[DECISION_SKIPPED],
        "mock_candidates": sum(1 for o in outputs.values() if o.model_used == "mock"),
        "by_task": dict(Counter(c.task for c in candidates)),
        "by_split": dict(Counter(c.split for c in candidates)),
        "experiment_targets": EXPERIMENT_TARGETS,
    }


def export_rows(session: Session, queue: str | None = None) -> list[dict[str, Any]]:
    """Human-reviewed examples only: each candidate's latest annotation, if
    it accepted or corrected the candidate's own output. Unreviewed,
    skipped and stale annotations are excluded."""
    stmt = select(AnnotationCandidate).order_by(AnnotationCandidate.queue, AnnotationCandidate.position)
    if queue is not None:
        stmt = stmt.where(AnnotationCandidate.queue == queue)
    candidates = list(session.scalars(stmt))
    latest = latest_annotations(session, [c.id for c in candidates])
    rows = []
    for candidate in candidates:
        annotation = latest.get(candidate.id)
        if annotation is None or candidate_status(candidate, annotation) not in (DECISION_ACCEPTED, DECISION_CORRECTED):
            continue
        source = session.get(AIOutput, annotation.source_output_id)
        target = session.get(AIOutput, annotation.target_output_id)
        lead = session.get(Lead, candidate.lead_id)
        rows.append({
            "export_format": EXPORT_FORMAT_VERSION,
            "example_id": str(annotation.id),
            "queue": candidate.queue,
            "candidate_id": str(candidate.id),
            "task": candidate.task,
            "split": candidate.split,
            "manifest_version": candidate.manifest_version,
            "company_group_key": candidate.group_key,
            "lead_id": str(lead.id),
            "company_identity_id": str(lead.company_identity_id) if lead.company_identity_id else None,
            "source_record_id": lead.source_record_id,
            "input_snapshot": source.input_snapshot,
            "input_hash": source.input_hash,
            "target": target.content,
            "target_content_hash": annotation.target_content_hash,
            "target_output_id": str(target.id),
            "target_origin": target.origin,
            "source_output_id": str(source.id),
            "source_content_hash": annotation.source_content_hash,
            "source_output": source.content,
            "seller_profile_id": str(source.seller_profile_id) if source.seller_profile_id else None,
            "seller_profile_version": source.seller_profile_version,
            "seller_profile_content_hash": source.seller_profile_content_hash,
            "seller_profile_kind": source.seller_profile_kind,
            "prompt_version": source.prompt_version,
            "output_schema_version": source.output_schema_version,
            "model_used": source.model_used,
            "model_revision": source.model_revision,
            "is_mock": source.model_used == "mock",
            "is_demo_seller": source.seller_profile_kind == "demo",
            "reviewer_label": annotation.reviewer_label,
            "reviewer_authenticated": False,
            "review_mode": annotation.review_mode,
            "decision": annotation.decision,
            "reviewed_at": annotation.created_at.isoformat(),
            "assessment": {
                "factual_support": annotation.factual_support,
                "writing_quality": annotation.writing_quality,
                "missing_info_handling": annotation.missing_info_handling,
            },
            "notes": annotation.notes,
            "timing": annotation.timing,
        })
    return rows
