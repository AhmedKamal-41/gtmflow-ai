"""Execute one claimed job, item by item.

Every item is its own transaction containing (1) the item's effect, (2) its
status and the job's progress counts and (3) a *fenced* lease renewal:
an UPDATE that only matches while this worker still owns the running job.
If another worker took the job over (our lease expired), the fence matches
nothing, the transaction is rolled back and this worker stops -- so an
item's database effect is committed at most once. Items committed before a
crash stay done; the next worker continues with the pending ones.

External effects cannot be rolled back with a database transaction. Phase 11
therefore persists a unique delivery claim before Slack dispatch. A takeover
cannot repeat that claim; an uncertain outcome requires operator resolution.
Push items are never retried automatically. This is at-most-once dispatch per
claim, not exactly-once delivery by the external webhook.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.actor import Actor, reset_actor, set_actor
from app.jobs.handlers import HANDLERS, JobFatalError, TransientItemError
from app.jobs.queue import Clock, _event, utcnow
from app.models import Lead
from app.models.background_job import (
    ITEM_FAILED,
    ITEM_PENDING,
    JOB_CANCELLED,
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
    BackgroundJob,
    BackgroundJobItem,
)

LOST = "lost"          # another worker owns the job now
RELEASED = "released"  # graceful stop: back in the queue
PAUSED = "paused"      # item_limit reached (tests: a worker that dies holding the lease)


class LeaseLost(Exception):
    pass


def _fence(session: Session, job_id: UUID, worker_id: str, lease_seconds: float, clock: Clock) -> None:
    now = clock()
    result = session.execute(
        update(BackgroundJob)
        .where(BackgroundJob.id == job_id, BackgroundJob.lease_owner == worker_id,
               BackgroundJob.status == JOB_RUNNING)
        .values(lease_expires_at=now + timedelta(seconds=lease_seconds), heartbeat_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise LeaseLost()


def _bump(job: BackgroundJob, status: str, pending_delta: int = -1) -> None:
    counts = dict(job.counts or {})
    counts[status] = counts.get(status, 0) + 1
    counts[ITEM_PENDING] = counts.get(ITEM_PENDING, 0) + pending_delta
    job.counts = counts


def _close(session: Session, job: BackgroundJob, status: str, clock: Clock, *,
           error: str | None = None, result: dict[str, Any] | None = None) -> None:
    job.status, job.finished_at, job.dedupe_key = status, clock(), None
    job.lease_owner = None
    job.lease_expires_at = None
    if error is not None:
        job.last_error = error[:300]
    if result is not None:
        job.result = result
    event = {JOB_COMPLETED: "job_completed", JOB_FAILED: "job_failed", JOB_CANCELLED: "job_cancelled"}[status]
    _event(session, job, event, counts=job.counts, **({"error": job.last_error} if error else {}))


def _materialize(session: Session, job: BackgroundJob) -> None:
    handler = HANDLERS[job.job_type]
    lead_ids = handler.select_leads(session, job)
    session.add_all(BackgroundJobItem(job_id=job.id, lead_id=lead_id, position=i, status=ITEM_PENDING, attempts=0)
                    for i, lead_id in enumerate(lead_ids))
    job.total_items = len(lead_ids)
    job.counts = {ITEM_PENDING: len(lead_ids)}


def run_job(factory: sessionmaker[Session], job_id: UUID, worker_id: str, *, lease_seconds: float = 60,
            clock: Clock = utcnow, should_stop: Callable[[], bool] = lambda: False,
            item_limit: int | None = None) -> str:
    """Run a job this worker has claimed. Returns its final job status, or
    LOST / RELEASED / PAUSED. Everything the job does is recorded with the
    actor "job:<who queued it>" (Phase 12)."""
    with factory() as session:
        job = session.get(BackgroundJob, job_id)
        queued_by = job.created_by or "unknown"
        user_id = str(job.created_by_user_id) if job.created_by_user_id else None
    token = set_actor(Actor(label=f"job:{queued_by}"[:64], user_id=user_id))
    try:
        return _run_job(factory, job_id, worker_id, lease_seconds=lease_seconds, clock=clock,
                        should_stop=should_stop, item_limit=item_limit)
    finally:
        reset_actor(token)


def _run_job(factory: sessionmaker[Session], job_id: UUID, worker_id: str, *, lease_seconds: float,
             clock: Clock, should_stop: Callable[[], bool], item_limit: int | None) -> str:
    handler = HANDLERS[_job_type(factory, job_id)]
    with factory() as session:
        job = session.get(BackgroundJob, job_id)
        if job.total_items is None:
            try:
                _materialize(session, job)
            except JobFatalError as error:
                session.rollback()
                return _fail(factory, job_id, worker_id, lease_seconds, clock, str(error))
            try:
                _fence(session, job_id, worker_id, lease_seconds, clock)
            except LeaseLost:
                session.rollback()
                return LOST
            session.commit()

    processed = 0
    while True:
        with factory() as session:
            job = session.get(BackgroundJob, job_id)
            if job.status != JOB_RUNNING or job.lease_owner != worker_id:
                return LOST
            if job.cancel_requested:
                try:
                    _fence(session, job_id, worker_id, lease_seconds, clock)
                except LeaseLost:
                    session.rollback()
                    return LOST
                _close(session, job, JOB_CANCELLED, clock)
                session.commit()
                return JOB_CANCELLED
            if should_stop():
                return _release(session, job, worker_id, clock)
            if item_limit is not None and processed >= item_limit:
                return PAUSED
            item = session.scalar(
                select(BackgroundJobItem)
                .where(BackgroundJobItem.job_id == job_id, BackgroundJobItem.status == ITEM_PENDING)
                .order_by(BackgroundJobItem.position).limit(1)
            )
            if item is None:
                try:
                    _fence(session, job_id, worker_id, lease_seconds, clock)
                    result = handler.finalize(session, job) if handler.finalize else None
                except LeaseLost:
                    session.rollback()
                    return LOST
                _close(session, job, JOB_COMPLETED, clock, result=result)
                session.commit()
                return JOB_COMPLETED
            item_id = item.id
            lead = session.get(Lead, item.lead_id)
            try:
                status, outcome = handler.process(session, job, lead)
            except JobFatalError as error:
                session.rollback()
                return _fail(factory, job_id, worker_id, lease_seconds, clock, str(error))
            except Exception as error:  # noqa: BLE001 -- bounded retry; only a safe message is kept
                session.rollback()
                message = str(error) if isinstance(error, TransientItemError) else f"unexpected:{type(error).__name__}"
                if _retry(factory, job_id, item_id, worker_id, lease_seconds, clock, message) == LOST:
                    return LOST
                processed += 1
                continue
            item = session.get(BackgroundJobItem, item_id)
            job = session.get(BackgroundJob, job_id)
            item.attempts += 1
            item.status, item.outcome, item.error, item.updated_at = status, outcome, None, clock()
            _bump(job, status)
            try:
                _fence(session, job_id, worker_id, lease_seconds, clock)
            except LeaseLost:
                session.rollback()
                return LOST
            session.commit()
            processed += 1


def _job_type(factory: sessionmaker[Session], job_id: UUID) -> str:
    with factory() as session:
        return session.get(BackgroundJob, job_id).job_type


def _retry(factory, job_id, item_id, worker_id, lease_seconds, clock, message) -> str | None:
    with factory() as session:
        item = session.get(BackgroundJobItem, item_id)
        job = session.get(BackgroundJob, job_id)
        item.attempts += 1
        item.error, item.updated_at = message[:300], clock()
        if item.attempts >= job.max_item_attempts:
            item.status = ITEM_FAILED
            _bump(job, ITEM_FAILED)
        try:
            _fence(session, job_id, worker_id, lease_seconds, clock)
        except LeaseLost:
            session.rollback()
            return LOST
        session.commit()
    return None


def _fail(factory, job_id, worker_id, lease_seconds, clock, message) -> str:
    with factory() as session:
        job = session.get(BackgroundJob, job_id)
        try:
            _fence(session, job_id, worker_id, lease_seconds, clock)
        except LeaseLost:
            session.rollback()
            return LOST
        _close(session, job, JOB_FAILED, clock, error=message)
        session.commit()
    return JOB_FAILED


def _release(session: Session, job: BackgroundJob, worker_id: str, clock: Clock) -> str:
    """Graceful shutdown: hand the job back to the queue at once. A graceful
    release does not count toward the crash-loop limit."""
    result = session.execute(
        update(BackgroundJob)
        .where(BackgroundJob.id == job.id, BackgroundJob.lease_owner == worker_id,
               BackgroundJob.status == JOB_RUNNING)
        .values(status=JOB_QUEUED, lease_owner=None, lease_expires_at=None,
                attempts=BackgroundJob.attempts - 1)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.rollback()
        return LOST
    _event(session, job, "job_released", worker=worker_id, counts=job.counts)
    session.commit()
    return RELEASED
