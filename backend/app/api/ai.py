from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.models import AIOutput, Lead
from app.models.ai_output import PURPOSE_ANNOTATION, PURPOSE_OPERATIONAL
from app.schemas.ai_output import AIOutputRead, AIOutputRevisionCreate
from app.schemas.pagination import Page
from app.ai.client import AIConfigError, AIProviderError
from app.services.ai_generation import (
    GenerationOutputInvalid,
    SellerProfileRequired,
    generate_outreach_for_lead,
    generate_summary_for_lead,
    record_generation_rejected,
)
from app.services.draft_review import ReviewError, create_revision, output_statuses
from app.services.pagination import pagination_params, paginate

router = APIRouter(prefix="/api/leads", tags=["ai"])


def _generate_or_raise(session: Session, lead: Lead, output_type: str, generate) -> AIOutput:
    """Map generation failures to clear responses. None of them saves an
    AIOutput; a validation failure records only its reason codes."""
    lead_id = lead.id
    try:
        return generate(session, lead)
    except SellerProfileRequired as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from None
    except AIConfigError as error:
        # Our own configuration message; it never contains a key.
        session.rollback()
        raise HTTPException(status_code=503, detail=str(error)) from None
    except AIProviderError as error:
        session.rollback()
        raise HTTPException(
            status_code=502, detail=f"{error} No output was saved."
        ) from None
    except GenerationOutputInvalid as error:
        session.rollback()
        record_generation_rejected(session, lead_id, output_type, error.reason_codes, error.usage, error.details)
        session.commit()
        raise HTTPException(status_code=502, detail=str(error)) from None


@router.post(
    "/{lead_id}/generate-summary",
    response_model=AIOutputRead,
    status_code=status.HTTP_200_OK,
)
def post_generate_summary(
    lead_id: UUID,
    session: Session = Depends(get_session),
) -> AIOutput:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    output = _generate_or_raise(session, lead, "company_summary", generate_summary_for_lead)
    session.commit()
    session.refresh(output)
    return output


@router.post(
    "/{lead_id}/generate-outreach",
    response_model=AIOutputRead,
    status_code=status.HTTP_200_OK,
)
def post_generate_outreach(
    lead_id: UUID,
    session: Session = Depends(get_session),
) -> AIOutput:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    output = _generate_or_raise(session, lead, "outreach_email", generate_outreach_for_lead)
    session.commit()
    session.refresh(output)
    return output


@router.get(
    "/{lead_id}/ai-outputs",
    response_model=Page[AIOutputRead],
)
def list_lead_ai_outputs(
    lead_id: UUID,
    pagination: tuple[int, int] = Depends(pagination_params),
    purpose: str = Query(
        PURPOSE_OPERATIONAL,
        pattern=f"^({PURPOSE_OPERATIONAL}|{PURPOSE_ANNOTATION})$",
        description="operational (the lead's drafts, default) or annotation (training candidates)",
    ),
    session: Session = Depends(get_session),
) -> Page[AIOutputRead]:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    limit, offset = pagination
    stmt = (
        select(AIOutput)
        .where(AIOutput.lead_id == lead_id, AIOutput.purpose == purpose)
        .order_by(AIOutput.created_at.desc(), AIOutput.id.desc())
    )
    page = paginate(session, stmt, limit=limit, offset=offset, schema=AIOutputRead)
    rows = session.scalars(select(AIOutput).where(AIOutput.id.in_([i.id for i in page.items]))).all()
    statuses = output_statuses(session, list(rows))
    for item in page.items:
        item.review_status = statuses.get(item.id)
    return page


@router.get(
    "/{lead_id}/latest-ai-output",
    response_model=AIOutputRead,
)
def get_latest_ai_output(
    lead_id: UUID,
    output_type: str = Query(
        ...,
        description="company_summary or outreach_email",
    ),
    session: Session = Depends(get_session),
) -> AIOutputRead:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    row = session.execute(
        select(AIOutput)
        .where(
            AIOutput.lead_id == lead_id,
            AIOutput.output_type == output_type,
            AIOutput.purpose == PURPOSE_OPERATIONAL,
        )
        # `id` breaks created_at ties -- the same rule as the approve/reject
        # supersession check and v2 readiness, so all three agree on which
        # draft is current.
        .order_by(AIOutput.created_at.desc(), AIOutput.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No AI output of type '{output_type}' for this lead",
        )
    item = AIOutputRead.model_validate(row)
    item.review_status = output_statuses(session, [row]).get(row.id)
    return item


@router.post(
    "/{lead_id}/ai-outputs/{output_id}/revisions",
    response_model=AIOutputRead,
    status_code=status.HTTP_201_CREATED,
)
def post_output_revision(
    lead_id: UUID,
    output_id: UUID,
    payload: AIOutputRevisionCreate,
    response: Response,
    session: Session = Depends(get_session),
) -> AIOutputRead:
    """Save a human correction of an operational output as a new immutable
    revision. For outreach it becomes the current draft and needs its own
    review; the original stays in history, unchanged."""
    output = session.get(AIOutput, output_id)
    if output is None or output.lead_id != lead_id:
        raise HTTPException(status_code=404, detail="AI output not found for this lead")
    if output.purpose != PURPOSE_OPERATIONAL:
        raise HTTPException(
            status_code=400,
            detail="Training-annotation candidates are corrected in the annotation workbench.",
        )
    try:
        revision, created = create_revision(
            session, output,
            expected_content_hash=payload.expected_content_hash,
            content=payload.content,
        )
    except ReviewError as error:
        session.rollback()
        raise HTTPException(status_code=error.status_code, detail=error.detail) from None
    session.commit()
    session.refresh(revision)
    if not created:
        response.status_code = status.HTTP_200_OK
    item = AIOutputRead.model_validate(revision)
    item.review_status = output_statuses(session, [revision]).get(revision.id)
    return item
