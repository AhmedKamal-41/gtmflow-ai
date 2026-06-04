from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.models import Lead, LeadBatch, WorkflowEvent
from app.schemas.batch_upload import BatchUploadError, BatchUploadResponse
from app.schemas.lead_batch import LeadBatchRead
from app.services.csv_ingestion import CSVValidationError, parse_csv

router = APIRouter(prefix="/api/batches", tags=["batches"])


@router.post(
    "/upload",
    response_model=BatchUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_batch(
    file: UploadFile = File(...),
    batch_name: str | None = Form(None),
    session: Session = Depends(get_session),
) -> BatchUploadResponse:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must have a .csv extension.")

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="CSV file is empty.")

    try:
        raw_text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="CSV file must be UTF-8 encoded.")

    try:
        result = parse_csv(raw_text)
    except CSVValidationError as e:
        raise HTTPException(status_code=400, detail=e.message) from e

    valid_count = len(result.valid_leads)
    batch = LeadBatch(
        name=batch_name,
        source="csv",
        total_leads=result.total_rows,
        processed_leads=valid_count,
        status="uploaded" if valid_count > 0 else "failed",
    )
    session.add(batch)
    session.flush()

    for cleaned in result.valid_leads:
        lead_kwargs: dict[str, object] = {
            "batch_id": batch.id,
            "company_name": cleaned.company_name,
            "website": cleaned.website,
            "industry": cleaned.industry,
            "contact_name": cleaned.contact_name,
            "contact_email": cleaned.contact_email,
            "contact_title": cleaned.contact_title,
            "company_size": cleaned.company_size,
            "location": cleaned.location,
            "source": cleaned.source,
            "cleaned_data": cleaned.cleaned_data,
        }
        if cleaned.status:
            lead_kwargs["status"] = cleaned.status
        session.add(Lead(**lead_kwargs))

    session.add(
        WorkflowEvent(
            batch_id=batch.id,
            event_type="batch_uploaded",
            event_data={
                "total_rows": result.total_rows,
                "valid_rows": valid_count,
                "invalid_rows": len(result.errors),
            },
        )
    )

    batch_id = batch.id
    session.commit()

    return BatchUploadResponse(
        batch_id=batch_id,
        batch_name=batch_name,
        total_rows=result.total_rows,
        valid_rows=valid_count,
        invalid_rows=len(result.errors),
        errors=[
            BatchUploadError(
                row_number=err.row_number,
                field=err.field,
                message=err.message,
            )
            for err in result.errors
        ],
    )


@router.get("", response_model=list[LeadBatchRead])
def list_batches(session: Session = Depends(get_session)) -> list[LeadBatch]:
    rows = session.execute(
        select(LeadBatch).order_by(LeadBatch.created_at.desc())
    ).scalars().all()
    return list(rows)


@router.get("/{batch_id}", response_model=LeadBatchRead)
def get_batch(batch_id: UUID, session: Session = Depends(get_session)) -> LeadBatch:
    batch = session.get(LeadBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch
