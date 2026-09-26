"""Phase 10: queue background jobs for a batch and follow their progress.

Queueing never runs the work in the request: a worker process
(`python -m app.jobs.worker`) executes it. The synchronous batch endpoints
remain for small batches and existing clients.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.jobs.queue import JobError, enqueue, request_cancel
from app.models.background_job import BackgroundJob, BackgroundJobItem
from app.schemas.jobs import JobCreate, JobItemRead, JobRead
from app.schemas.pagination import Page
from app.services.pagination import paginate, pagination_params

router = APIRouter(tags=["jobs"])


def _job(session: Session, job_id: UUID) -> BackgroundJob:
    job = session.get(BackgroundJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/api/batches/{batch_id}/jobs", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
def create_job(batch_id: UUID, payload: JobCreate, response: Response,
               session: Session = Depends(get_session)) -> JobRead:
    try:
        job, created = enqueue(session, payload.job_type, batch_id, payload.params)
    except JobError as error:
        session.rollback()
        raise HTTPException(status_code=error.status_code, detail=error.detail) from None
    session.commit()
    session.refresh(job)
    item = JobRead.model_validate(job)
    if not created:
        response.status_code = status.HTTP_200_OK
        item.deduplicated = True
    return item


@router.get("/api/jobs", response_model=Page[JobRead])
def list_jobs(batch_id: UUID | None = Query(None), status_filter: str | None = Query(None, alias="status"),
              pagination: tuple[int, int] = Depends(pagination_params),
              session: Session = Depends(get_session)) -> Page[JobRead]:
    limit, offset = pagination
    stmt = select(BackgroundJob)
    if batch_id is not None:
        stmt = stmt.where(BackgroundJob.batch_id == batch_id)
    if status_filter is not None:
        stmt = stmt.where(BackgroundJob.status == status_filter)
    stmt = stmt.order_by(BackgroundJob.created_at.desc(), BackgroundJob.id.desc())
    return paginate(session, stmt, limit=limit, offset=offset, schema=JobRead)


@router.get("/api/jobs/{job_id}", response_model=JobRead)
def get_job(job_id: UUID, session: Session = Depends(get_session)) -> JobRead:
    return JobRead.model_validate(_job(session, job_id))


@router.get("/api/jobs/{job_id}/items", response_model=Page[JobItemRead])
def list_job_items(job_id: UUID, status_filter: str | None = Query(None, alias="status"),
                   pagination: tuple[int, int] = Depends(pagination_params),
                   session: Session = Depends(get_session)) -> Page[JobItemRead]:
    _job(session, job_id)
    limit, offset = pagination
    stmt = select(BackgroundJobItem).where(BackgroundJobItem.job_id == job_id)
    if status_filter is not None:
        stmt = stmt.where(BackgroundJobItem.status == status_filter)
    stmt = stmt.order_by(BackgroundJobItem.position, BackgroundJobItem.id)
    return paginate(session, stmt, limit=limit, offset=offset, schema=JobItemRead)


@router.post("/api/jobs/{job_id}/cancel", response_model=JobRead)
def cancel_job(job_id: UUID, session: Session = Depends(get_session)) -> JobRead:
    job = _job(session, job_id)
    try:
        request_cancel(session, job)
    except JobError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from None
    session.commit()
    session.refresh(job)
    return JobRead.model_validate(job)
