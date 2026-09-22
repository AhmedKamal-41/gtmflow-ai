from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.models import Lead, LeadBatch, WorkflowEvent
from app.models.lead_batch import (
    BATCH_STATUS_PARTIAL,
    BATCH_STATUS_UPLOADING,
    INCOMPLETE_BATCH_STATUSES,
)
from app.pdl.config import CSV_MAX_BYTES, CSV_MAX_ROWS
from app.schemas.batch_upload import BatchUploadError, BatchUploadResponse
from app.schemas.lead_batch import LeadBatchRead
from app.schemas.pagination import Page
from app.services.csv_ingestion import (
    CSVValidationError,
    StreamParseStats,
    iter_cleaned_leads,
)
from app.services.pagination import pagination_params, paginate

router = APIRouter(prefix="/api/batches", tags=["batches"])

_READ_CHUNK_SIZE = 1024 * 1024  # 1 MiB
_WRITE_CHUNK_ROWS = 500  # leads inserted + committed per DB transaction


async def _stream_upload_to_tempfile(file: UploadFile, max_bytes: int) -> tuple[Path, str]:
    """Stream the upload straight to a temp file on disk, enforcing
    max_bytes while WRITING -- never buffers the whole file in Python
    memory, and never trusts a client-supplied Content-Length header (Part
    E.2/E.3 of the Phase 3 closeout: the previous version read in 1 MiB
    chunks but still joined them into one in-memory `bytes` object before
    parsing anything -- bounded by size, but not actually streaming parse +
    bounded database writes, which is what was asked for).

    Also hashes the bytes (sha256) as they're written, streaming -- this is
    the retry-identity used by Part A.4's resume mechanism (see
    `upload_batch`): the same file bytes hash the same way regardless of
    how the previous attempt failed, without needing to re-read the file.
    Returns (tmp_path, hex_digest).
    """
    fd, tmp_name = tempfile.mkstemp(suffix=".csv")
    tmp_path = Path(tmp_name)
    total = 0
    hasher = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as f:
            while True:
                chunk = await file.read(_READ_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"CSV file exceeds the {max_bytes // (1024 * 1024)} MiB "
                            "upload limit. For bulk company data, use the PDL "
                            "curation CLI (backend/app/pdl/cli.py), not this endpoint."
                        ),
                    )
                hasher.update(chunk)
                f.write(chunk)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    if total == 0:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="CSV file is empty.")
    return tmp_path, hasher.hexdigest()


def _lead_kwargs(batch_id: UUID, cleaned) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "batch_id": batch_id,
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
        kwargs["status"] = cleaned.status
    return kwargs


def _committed_lead_count(session: Session, batch_id: UUID) -> int:
    return session.scalar(
        select(func.count()).select_from(Lead).where(Lead.batch_id == batch_id)
    ) or 0


@router.post(
    "/upload",
    response_model=BatchUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_batch(
    file: UploadFile = File(...),
    batch_name: str | None = Form(None),
    resume_batch_id: UUID | None = Form(
        None,
        description=(
            "Explicit retry identifier (Phase 4 closeout, item 4): pass the "
            "batch_id from a prior 422 'partial' response to resume THAT "
            "specific upload attempt. Omitting this always creates a brand "
            "new batch, even if the file's bytes happen to be identical to "
            "an existing partial batch -- content matching alone is never "
            "enough to infer 'this is a retry,' since a second, deliberately "
            "separate upload of the same file is a legitimate, distinct "
            "operation. Content hash still must match the batch being "
            "resumed; a changed file presented as the same retry is rejected."
        ),
    ),
    session: Session = Depends(get_session),
) -> BatchUploadResponse:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must have a .csv extension.")

    tmp_path, content_hash = await _stream_upload_to_tempfile(file, CSV_MAX_BYTES)
    try:
        # Validate the header BEFORE creating/resuming any batch/lead row --
        # matches the previous behavior where a bad header meant no DB
        # writes at all. Read+validate, then rewind for the real
        # (streaming) pass.
        try:
            with tmp_path.open("r", encoding="utf-8-sig", newline="") as probe:
                # Draining one row from the generator forces header
                # validation (it happens before the first yield); a
                # StopIteration here just means "zero data rows," not an
                # error -- the generator already raised for an actually
                # invalid/missing header.
                next(iter_cleaned_leads(probe, max_rows=CSV_MAX_ROWS), None)
        except CSVValidationError as e:
            raise HTTPException(status_code=400, detail=e.message) from e
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="CSV file must be UTF-8 encoded.")

        # Phase 4 closeout (item 4): resuming is opt-in via an EXPLICIT
        # `resume_batch_id`, never inferred from content hash alone. Hash
        # matching by itself can't distinguish "this is a retry of that
        # specific failed attempt" from "this happens to be the same file,
        # uploaded again as a deliberately separate operation" -- both are
        # legitimate and must be distinguishable by the caller, not guessed
        # by the server. `upload_content_hash` is still stored on every
        # batch and still checked here, but only to REJECT a resume_batch_id
        # whose content no longer matches (a changed file presented as the
        # same retry), never to auto-discover a resume target.
        if resume_batch_id is not None:
            batch = session.get(LeadBatch, resume_batch_id)
            if batch is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"resume_batch_id {resume_batch_id} does not exist.",
                )
            # "uploading" is resumable too: it's what a batch is left in when
            # the process died after committing some chunks but before the
            # handler could record "partial" (see INCOMPLETE_BATCH_STATUSES).
            if batch.status not in INCOMPLETE_BATCH_STATUSES:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Batch {resume_batch_id} has status '{batch.status}', not "
                        "'partial'/'uploading' -- there is nothing to resume. Omit "
                        "resume_batch_id to upload this file as a new batch."
                    ),
                )
            if batch.upload_content_hash != content_hash:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"This file's content does not match batch "
                        f"{resume_batch_id}'s original upload -- refusing to "
                        "resume a different file under the same retry. Omit "
                        "resume_batch_id to upload this as a new, separate batch."
                    ),
                )
            batch_id = batch.id
            # The rows actually committed to this batch, not the stored
            # `processed_leads` counter: after a crash between a chunk commit
            # and the final bookkeeping commit, the counter lags the table.
            # Rows are inserted in file order, so skipping this many valid
            # rows of the (hash-verified identical) file resumes exactly
            # where the committed data ends.
            already_committed = _committed_lead_count(session, batch_id)
            resumed = True
        else:
            batch = LeadBatch(
                name=batch_name,
                source="csv",
                status=BATCH_STATUS_UPLOADING,
                upload_content_hash=content_hash,
            )
            session.add(batch)
            # Committed on its own, before any lead chunk, so the batch is
            # durably marked incomplete from the start -- if the process
            # dies mid-stream it stays "uploading" (excluded from routing,
            # resumable), never a finished-looking "uploaded".
            session.commit()
            batch_id = batch.id
            already_committed = 0
            resumed = False

        stats = StreamParseStats()
        inserted = 0
        skipped_for_resume = 0
        chunk: list = []
        mid_stream_error: str | None = None

        def _flush_chunk() -> None:
            nonlocal chunk, inserted
            if not chunk:
                return
            for cleaned in chunk:
                session.add(Lead(**_lead_kwargs(batch_id, cleaned)))
            session.commit()
            inserted += len(chunk)
            chunk = []

        try:
            with tmp_path.open("r", encoding="utf-8-sig", newline="") as f:
                for cleaned in iter_cleaned_leads(
                    f, max_rows=CSV_MAX_ROWS, stats=stats
                ):
                    # On resume, the first `already_committed` valid rows
                    # were already inserted+committed by the earlier
                    # attempt -- re-parsed here (for accurate total_rows /
                    # error counts against the full file) but never
                    # re-inserted.
                    if skipped_for_resume < already_committed:
                        skipped_for_resume += 1
                        continue
                    chunk.append(cleaned)
                    if len(chunk) >= _WRITE_CHUNK_ROWS:
                        _flush_chunk()
            _flush_chunk()
        except CSVValidationError as e:
            # Exceeded max_rows mid-stream. Flush whatever was already
            # buffered in the current (not-yet-full) chunk before recording
            # the error -- otherwise up to _WRITE_CHUNK_ROWS-1 correctly
            # parsed leads would be silently discarded (parsed, buffered,
            # never committed, never counted). Every row `iter_cleaned_leads`
            # actually yielded before raising is preserved; only the row
            # that pushed the count over the limit, and everything after it
            # (which was never read), is excluded. The batch is marked
            # "partial" and excluded from push (see app/api/push.py's guard).
            _flush_chunk()
            mid_stream_error = e.message
        except Exception as e:
            # Phase 4 closeout Part A.4: any OTHER failure (DB error,
            # timeout, etc.) mid-stream must also honestly leave the batch
            # "partial" with an accurate committed count -- not just the
            # max-rows case. The failure may have happened inside
            # `_flush_chunk`'s `session.commit()` itself, so the session
            # can be left needing a rollback before it's touched again; the
            # in-flight chunk is deliberately NOT re-flushed (it may be
            # exactly what caused the failure).
            session.rollback()
            mid_stream_error = f"Unexpected error while processing the file: {e}"

        batch = session.get(LeadBatch, batch_id)
        batch.total_leads = stats.total_rows
        batch.processed_leads = _committed_lead_count(session, batch_id)
        if mid_stream_error is not None:
            batch.status = BATCH_STATUS_PARTIAL
        elif batch.processed_leads == 0:
            batch.status = "failed"
        else:
            batch.status = "uploaded"

        session.add(
            WorkflowEvent(
                batch_id=batch_id,
                event_type="batch_uploaded" if mid_stream_error is None else "batch_upload_partial",
                event_data={
                    "total_rows": stats.total_rows,
                    "valid_rows": batch.processed_leads,
                    "invalid_rows": stats.total_error_count,
                    "mid_stream_error": mid_stream_error,
                    "resumed": resumed,
                    "resumed_from_processed_leads": already_committed if resumed else None,
                },
            )
        )
        session.commit()

        if mid_stream_error is not None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{mid_stream_error} {batch.processed_leads} rows were already "
                    f"committed to batch {batch_id} before this limit was hit; the "
                    "batch is marked 'partial' and will not be included in Slack "
                    f"routing. To retry without duplicating rows, resubmit the "
                    f"identical file with resume_batch_id={batch_id}. Omitting "
                    "resume_batch_id (even with the same file) creates a separate "
                    "new batch instead."
                ),
            )

        return BatchUploadResponse(
            batch_id=batch_id,
            batch_name=batch.name,
            total_rows=stats.total_rows,
            valid_rows=batch.processed_leads,
            invalid_rows=stats.total_error_count,
            errors=[
                BatchUploadError(
                    row_number=err.row_number, field=err.field, message=err.message
                )
                for err in stats.error_examples
            ],
        )
    finally:
        tmp_path.unlink(missing_ok=True)


def _scored_counts_by_batch(session: Session, batch_ids: list[UUID]) -> dict[UUID, int]:
    """One grouped query for however many batches are on the current page
    -- avoids an N+1 COUNT per batch row."""
    if not batch_ids:
        return {}
    from app.models import LeadScore

    rows = session.execute(
        select(Lead.batch_id, func.count())
        .select_from(Lead)
        .join(LeadScore, LeadScore.lead_id == Lead.id)
        .where(Lead.batch_id.in_(batch_ids))
        .group_by(Lead.batch_id)
    ).all()
    return {batch_id: count for batch_id, count in rows}


@router.get("", response_model=Page[LeadBatchRead])
def list_batches(
    pagination: tuple[int, int] = Depends(pagination_params),
    session: Session = Depends(get_session),
) -> Page[LeadBatchRead]:
    limit, offset = pagination
    stmt = select(LeadBatch).order_by(LeadBatch.created_at.desc(), LeadBatch.id.desc())
    page = paginate(session, stmt, limit=limit, offset=offset, schema=LeadBatchRead)
    scored_counts = _scored_counts_by_batch(session, [item.id for item in page.items])
    for item in page.items:
        item.scored_leads = scored_counts.get(item.id, 0)
    return page


@router.get("/{batch_id}", response_model=LeadBatchRead)
def get_batch(batch_id: UUID, session: Session = Depends(get_session)) -> LeadBatchRead:
    batch = session.get(LeadBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    result = LeadBatchRead.model_validate(batch)
    result.scored_leads = _scored_counts_by_batch(session, [batch.id]).get(batch.id, 0)
    return result
