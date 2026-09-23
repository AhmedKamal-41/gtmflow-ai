"""Phase 6 training-annotation workbench API. Separate from outreach review:
nothing here approves a draft for delivery, and outreach approval never
creates a training example."""
from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.client import AIConfigError, AIProviderError
from app.core.database import get_session
from app.models import AIOutput, AnnotationCandidate, Lead, TrainingAnnotation
from app.schemas.ai_output import AIOutputRead
from app.schemas.annotation import (
    AnnotationSubmit,
    AnnotationSummary,
    CandidateDetail,
    CandidateSummary,
    GenerateCandidateRequest,
    ProviderInfo,
    TrainingAnnotationRead,
)
from app.schemas.pagination import Page
from app.services.ai_generation import GenerationOutputInvalid, record_generation_rejected
from app.services.annotation import (
    AnnotationError,
    candidate_status,
    configured_provider,
    export_rows,
    generate_candidate,
    is_mock_output,
    latest_annotations,
    queue_summary,
    submit_annotation,
)
from app.services.pagination import pagination_params
from app.api.outreach_review import _source_info

router = APIRouter(prefix="/api/annotation", tags=["annotation"])


def _candidate(session: Session, candidate_id: UUID) -> AnnotationCandidate:
    candidate = session.get(AnnotationCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Annotation candidate not found")
    return candidate


def _summary(candidate: AnnotationCandidate, latest, output: AIOutput | None) -> dict:
    return {
        "id": candidate.id, "queue": candidate.queue, "position": candidate.position,
        "task": candidate.task, "split": candidate.split, "group_key": candidate.group_key,
        "lead_id": candidate.lead_id, "company_name": candidate.lead.company_name,
        "status": candidate_status(candidate, latest),
        "source_output_id": candidate.source_output_id, "is_mock": is_mock_output(output),
    }


@router.get("/provider", response_model=ProviderInfo)
def get_provider() -> ProviderInfo:
    return ProviderInfo(**configured_provider())


@router.get("/queues/{queue}/summary", response_model=AnnotationSummary)
def get_queue_summary(queue: str, session: Session = Depends(get_session)) -> AnnotationSummary:
    return AnnotationSummary(**queue_summary(session, queue))


@router.get("/queues/{queue}/candidates", response_model=Page[CandidateSummary])
def list_candidates(
    queue: str,
    pagination: tuple[int, int] = Depends(pagination_params),
    session: Session = Depends(get_session),
) -> Page[CandidateSummary]:
    limit, offset = pagination
    total = session.scalar(
        select(func.count()).select_from(AnnotationCandidate).where(AnnotationCandidate.queue == queue)
    ) or 0
    rows = list(session.scalars(
        select(AnnotationCandidate).where(AnnotationCandidate.queue == queue)
        .order_by(AnnotationCandidate.position).limit(limit).offset(offset)
    ))
    latest = latest_annotations(session, [c.id for c in rows])
    outputs = {o.id: o for o in session.scalars(
        select(AIOutput).where(AIOutput.id.in_([c.source_output_id for c in rows if c.source_output_id]))
    )}
    items = [CandidateSummary(**_summary(c, latest.get(c.id), outputs.get(c.source_output_id))) for c in rows]
    return Page[CandidateSummary](items=items, total=total, limit=limit, offset=offset,
                                  has_more=offset + len(items) < total)


def _detail(session: Session, candidate: AnnotationCandidate) -> CandidateDetail:
    latest = latest_annotations(session, [candidate.id]).get(candidate.id)
    source = session.get(AIOutput, candidate.source_output_id) if candidate.source_output_id else None
    target = None
    if latest is not None and latest.target_output_id and latest.source_output_id == candidate.source_output_id:
        target = session.get(AIOutput, latest.target_output_id)
    lead: Lead = candidate.lead
    count = session.scalar(
        select(func.count()).select_from(TrainingAnnotation).where(TrainingAnnotation.candidate_id == candidate.id)
    ) or 0
    return CandidateDetail(
        **_summary(candidate, latest, source),
        manifest_version=candidate.manifest_version,
        lead_facts={
            "company_name": lead.company_name, "website": lead.website, "industry": lead.industry,
            "company_size": lead.company_size, "location": lead.location,
            "contact_name": lead.contact_name, "contact_title": lead.contact_title,
            "contact_email": lead.contact_email, "source_record_id": lead.source_record_id,
        },
        source=_source_info(lead).model_dump(mode="json"),
        source_output=AIOutputRead.model_validate(source) if source else None,
        target_output=AIOutputRead.model_validate(target) if target else None,
        latest_annotation=TrainingAnnotationRead.model_validate(latest) if latest else None,
        annotation_count=count,
    )


@router.get("/candidates/{candidate_id}", response_model=CandidateDetail)
def get_candidate(candidate_id: UUID, session: Session = Depends(get_session)) -> CandidateDetail:
    return _detail(session, _candidate(session, candidate_id))


@router.post("/candidates/{candidate_id}/generate", response_model=CandidateDetail)
def post_generate_candidate(
    candidate_id: UUID,
    payload: GenerateCandidateRequest,
    session: Session = Depends(get_session),
) -> CandidateDetail:
    candidate = _candidate(session, candidate_id)
    try:
        generate_candidate(session, candidate, payload.provider)
    except AnnotationError as error:
        session.rollback()
        raise HTTPException(status_code=error.status_code, detail=error.detail) from None
    except AIConfigError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail=str(error)) from None
    except AIProviderError as error:
        session.rollback()
        raise HTTPException(status_code=502, detail=f"{error} No output was saved.") from None
    except GenerationOutputInvalid as error:
        session.rollback()
        record_generation_rejected(session, candidate.lead_id, candidate.task, error.reason_codes)
        session.commit()
        raise HTTPException(status_code=502, detail=str(error)) from None
    session.commit()
    session.refresh(candidate)
    return _detail(session, candidate)


@router.post("/candidates/{candidate_id}/annotations", response_model=TrainingAnnotationRead, status_code=201)
def post_annotation(
    candidate_id: UUID,
    payload: AnnotationSubmit,
    response: Response,
    session: Session = Depends(get_session),
) -> TrainingAnnotation:
    candidate = _candidate(session, candidate_id)
    try:
        row, created = submit_annotation(session, candidate, payload)
    except AnnotationError as error:
        session.rollback()
        raise HTTPException(status_code=error.status_code, detail=error.detail) from None
    session.commit()
    session.refresh(row)
    if not created:
        response.status_code = status.HTTP_200_OK
    return row


@router.get("/export", response_class=PlainTextResponse)
def get_export(queue: str | None = None, session: Session = Depends(get_session)) -> PlainTextResponse:
    """Human-reviewed examples as JSON Lines. Unreviewed and skipped
    candidates are never included."""
    lines = [json.dumps(row, sort_keys=True, default=str) for row in export_rows(session, queue)]
    return PlainTextResponse("\n".join(lines) + ("\n" if lines else ""), media_type="application/x-ndjson")
