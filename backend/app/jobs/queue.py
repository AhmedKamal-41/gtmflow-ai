"""Enqueue, claim and cancel background jobs.

Claiming is a conditional UPDATE, portable across Postgres and SQLite: only a
queued job, or a running job whose lease has expired, matches the WHERE
clause, so two workers can never both claim the same job (on Postgres the
second UPDATE waits for the first and then re-checks the condition).
"""
from __future__ import annotations

import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.actor import actor_label, current_actor
from app.models import LeadBatch, WorkflowEvent
from app.models.background_job import (
    ACTIVE_JOB_STATUSES,
    JOB_CANCELLED,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
    BackgroundJob,
)

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class JobError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _event(session: Session, job: BackgroundJob, event_type: str, **data: Any) -> None:
    session.add(WorkflowEvent(
        batch_id=job.batch_id, event_type=event_type,
        event_data={"job_id": str(job.id), "job_type": job.job_type, **data},
    ))


def enqueue(session: Session, job_type: str, batch_id: UUID, params: dict[str, Any] | None = None,
            *, max_attempts: int = 3, max_item_attempts: int = 3) -> tuple[BackgroundJob, bool]:
    """Queue one job for a batch. Returns (job, created). An identical job
    type already queued or running for the same batch is returned instead
    of queueing a duplicate. The caller commits."""
    from app.jobs.handlers import HANDLERS

    handler = HANDLERS.get(job_type)
    if handler is None:
        raise JobError(400, f"Unknown job type '{job_type}'. Known: {', '.join(sorted(HANDLERS))}.")
    if session.get(LeadBatch, batch_id) is None:
        raise JobError(404, "Batch not found")
    clean = handler.validate_params(params or {})
    key = f"{job_type}:{batch_id}"
    existing = session.scalar(select(BackgroundJob).where(BackgroundJob.dedupe_key == key))
    if existing is not None:
        return existing, False
    job = BackgroundJob(job_type=job_type, batch_id=batch_id, params=clean, status=JOB_QUEUED,
                        dedupe_key=key, attempts=0, max_attempts=max_attempts,
                        max_item_attempts=max_item_attempts, counts={}, cancel_requested=False,
                        created_by=actor_label(),
                        created_by_user_id=UUID(current_actor().user_id) if current_actor().user_id else None)
    session.add(job)
    try:
        session.flush()
    except IntegrityError:  # a concurrent request queued the same job first
        session.rollback()
        existing = session.scalar(select(BackgroundJob).where(BackgroundJob.dedupe_key == key))
        if existing is None:
            raise
        return existing, False
    _event(session, job, "job_enqueued", params=clean)
    return job, True


def _claimable(now: datetime):
    return or_(
        BackgroundJob.status == JOB_QUEUED,
        and_(BackgroundJob.status == JOB_RUNNING, BackgroundJob.lease_expires_at < now),
    )


def claim_next(session: Session, worker_id: str, lease_seconds: float, *, clock: Clock = utcnow,
               job_id: UUID | None = None) -> BackgroundJob | None:
    """Claim the oldest claimable job (or `job_id`), committing the claim.
    A job reclaimed after an expired lease is recorded as recovered; one
    claimed more than `max_attempts` times fails as a crash loop."""
    while True:
        now = clock()
        query = select(BackgroundJob.id, BackgroundJob.status, BackgroundJob.lease_owner).where(_claimable(now))
        if job_id is not None:
            query = query.where(BackgroundJob.id == job_id)
        candidates = session.execute(query.order_by(BackgroundJob.created_at, BackgroundJob.id).limit(10)).all()
        session.rollback()
        if not candidates:
            return None
        for cid, previous_status, previous_owner in candidates:
            result = session.execute(
                update(BackgroundJob)
                .where(BackgroundJob.id == cid, _claimable(now))
                .values(status=JOB_RUNNING, lease_owner=worker_id,
                        lease_expires_at=now + timedelta(seconds=lease_seconds), heartbeat_at=now,
                        attempts=BackgroundJob.attempts + 1)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                session.rollback()
                continue
            job = session.get(BackgroundJob, cid, populate_existing=True)
            if job.started_at is None:
                job.started_at = now
            if job.attempts > job.max_attempts:
                job.status, job.finished_at, job.dedupe_key = JOB_FAILED, now, None
                job.lease_owner = job.lease_expires_at = None
                job.last_error = f"crash_loop: claimed {job.attempts} times (max {job.max_attempts})"
                _event(session, job, "job_failed", error=job.last_error, counts=job.counts)
                session.commit()
                break  # look for another job
            if previous_status == JOB_RUNNING:
                _event(session, job, "job_recovered", previous_owner=previous_owner, attempt=job.attempts)
            session.commit()
            return job
        else:
            return None


def request_cancel(session: Session, job: BackgroundJob, *, clock: Clock = utcnow) -> BackgroundJob:
    """A queued job is cancelled at once; a running one stops before its
    next item (items already done stay done). The caller commits."""
    if job.status not in ACTIVE_JOB_STATUSES:
        raise JobError(409, f"Job is already {job.status}.")
    job.cancel_requested = True
    if job.status == JOB_QUEUED:
        job.status, job.finished_at, job.dedupe_key = JOB_CANCELLED, clock(), None
        _event(session, job, "job_cancelled", counts=job.counts)
    return job
