"""Phase 4 closeout: the real HTTP upload path after an interruption that
lands AFTER at least one chunk has already committed -- not just the
row-limit case (tests/test_csv_upload_retry.py), which is caught cleanly by
the handler before any bookkeeping is lost.

Two interruption shapes, both with `_WRITE_CHUNK_ROWS = 1` so every row is
its own committed chunk:

  * a handled failure (an ordinary exception while building row 3): the
    handler's own recovery path records "partial";
  * a crash that escapes the handler entirely (a BaseException, standing in
    for a killed worker): nothing after the second chunk's commit runs, so
    the batch's bookkeeping (`status`, `processed_leads`) is never updated.

In both, the committed rows must survive, the batch must stay excluded from
routing, an explicit `resume_batch_id` retry must finish the batch without
duplicating a single committed row, and routing must only open once the
batch has actually completed.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import batches as batches_module
from app.models import Lead, LeadBatch

CSV_TEXT = (
    "company_name,industry,contact_title,company_size,source\n"
    "Cascade Modular,Housing,VP Operations,1000+,referral\n"
    "Northbridge Clinics,Healthcare,Practice Manager,1000+,webinar\n"
    "Third Row Co,Housing,VP Operations,1000+,referral\n"
    "Fourth Row Co,Healthcare,Practice Manager,1000+,webinar\n"
)
ALL_NAMES = ["Cascade Modular", "Northbridge Clinics", "Third Row Co", "Fourth Row Co"]


class SimulatedWorkerCrash(BaseException):
    """Not an Exception subclass -- escapes the handler's `except Exception`
    recovery exactly like a killed process would skip it."""


def _upload(client: TestClient, resume_batch_id: str | None = None):
    files = {"file": ("leads.csv", CSV_TEXT.encode("utf-8"), "text/csv")}
    data: dict[str, str] = {"batch_name": "interrupted"}
    if resume_batch_id is not None:
        data["resume_batch_id"] = resume_batch_id
    return client.post("/api/batches/upload", files=files, data=data)


def _fail_on_row(monkeypatch, row_index: int, exc: BaseException) -> None:
    """Make building the Nth (1-based) lead of THIS upload raise `exc`."""
    real = batches_module._lead_kwargs
    calls = {"n": 0}

    def flaky(batch_id, cleaned):
        calls["n"] += 1
        if calls["n"] == row_index:
            raise exc
        return real(batch_id, cleaned)

    monkeypatch.setattr(batches_module, "_lead_kwargs", flaky)


def _lead_names(db_session: Session, batch_id: str) -> list[str]:
    db_session.expire_all()
    return sorted(
        db_session.execute(
            select(Lead.company_name).where(Lead.batch_id == uuid.UUID(batch_id))
        ).scalars()
    )


def _only_batch(db_session: Session) -> LeadBatch:
    db_session.expire_all()
    batches = db_session.execute(select(LeadBatch)).scalars().all()
    assert len(batches) == 1
    return batches[0]


def _assert_routing_blocked(client: TestClient, db_session: Session, batch_id: str) -> None:
    lead_id = db_session.execute(
        select(Lead.id).where(Lead.batch_id == uuid.UUID(batch_id)).limit(1)
    ).scalar_one()
    single = client.post(f"/api/leads/{lead_id}/push", json={"force": True})
    assert single.status_code == 400, single.text
    assert "partially imported" in single.json()["detail"]
    bulk = client.post(f"/api/batches/{batch_id}/push-hot", json={})
    assert bulk.status_code == 400, bulk.text
    fit = client.post(f"/api/leads/{lead_id}/fit-score")
    assert fit.status_code == 201
    assert fit.json()["eligibility"]["excluded"] is True


def _assert_resume_completes_without_duplicates(
    client: TestClient, db_session: Session, monkeypatch, batch_id: str
) -> None:
    monkeypatch.setattr(batches_module, "_lead_kwargs", _REAL_LEAD_KWARGS)
    resumed = _upload(client, resume_batch_id=batch_id)
    assert resumed.status_code == 201, resumed.text
    assert resumed.json()["batch_id"] == batch_id
    assert resumed.json()["valid_rows"] == 4

    assert _lead_names(db_session, batch_id) == sorted(ALL_NAMES)  # each exactly once
    batch = _only_batch(db_session)  # the retry created no second batch
    assert batch.status == "uploaded"
    assert batch.processed_leads == 4

    # Completion is what opens routing -- the scoring/push guards no longer
    # report this batch as incomplete.
    lead_id = db_session.execute(
        select(Lead.id).where(Lead.batch_id == uuid.UUID(batch_id)).limit(1)
    ).scalar_one()
    fit = client.post(f"/api/leads/{lead_id}/fit-score").json()
    assert fit["eligibility"]["excluded"] is False


_REAL_LEAD_KWARGS = batches_module._lead_kwargs


def test_handled_failure_after_committed_chunks_is_partial_and_resumes_exactly(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    monkeypatch.setattr(batches_module, "_WRITE_CHUNK_ROWS", 1)
    _fail_on_row(monkeypatch, 3, RuntimeError("database connection reset"))

    first = _upload(client)
    assert first.status_code == 422, first.text
    batch = _only_batch(db_session)
    batch_id = str(batch.id)
    assert batch.status == "partial"
    assert batch.processed_leads == 2
    assert _lead_names(db_session, batch_id) == sorted(ALL_NAMES[:2])

    _assert_routing_blocked(client, db_session, batch_id)
    _assert_resume_completes_without_duplicates(client, db_session, monkeypatch, batch_id)


def test_crash_escaping_the_handler_leaves_batch_uploading_and_resumable(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    monkeypatch.setattr(batches_module, "_WRITE_CHUNK_ROWS", 1)
    _fail_on_row(monkeypatch, 3, SimulatedWorkerCrash())

    with pytest.raises(SimulatedWorkerCrash):
        _upload(client)

    batch = _only_batch(db_session)
    batch_id = str(batch.id)
    # Handler never reached its bookkeeping: the batch must still read as
    # incomplete, not as a finished "uploaded" batch.
    assert batch.status == "uploading"
    assert _lead_names(db_session, batch_id) == sorted(ALL_NAMES[:2])
    # The stored counter lags the committed rows here -- the resume must
    # not trust it.
    assert batch.processed_leads == 0

    _assert_routing_blocked(client, db_session, batch_id)

    # Legacy v1 batch scoring must not overwrite the incomplete status.
    assert client.post(f"/api/batches/{batch_id}/score").status_code == 200
    assert _only_batch(db_session).status == "uploading"

    _assert_resume_completes_without_duplicates(client, db_session, monkeypatch, batch_id)


def test_resume_twice_after_completion_is_rejected_not_duplicated(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    monkeypatch.setattr(batches_module, "_WRITE_CHUNK_ROWS", 1)
    _fail_on_row(monkeypatch, 3, RuntimeError("boom"))
    _upload(client)
    batch_id = str(_only_batch(db_session).id)
    _assert_resume_completes_without_duplicates(client, db_session, monkeypatch, batch_id)

    # A client that retries its retry (e.g. it never saw the 201) gets a
    # clear 409 instead of a second copy of the rows.
    again = _upload(client, resume_batch_id=batch_id)
    assert again.status_code == 409
    db_session.expire_all()
    count = db_session.scalar(
        select(func.count()).select_from(Lead).where(Lead.batch_id == uuid.UUID(batch_id))
    )
    assert count == 4


def test_new_upload_of_identical_file_while_another_is_incomplete_is_separate(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    """Deliberately starting a new upload (no resume_batch_id) with
    byte-identical content creates a second batch and leaves the incomplete
    one untouched -- identical content is never treated as a retry."""
    monkeypatch.setattr(batches_module, "_WRITE_CHUNK_ROWS", 1)
    _fail_on_row(monkeypatch, 3, RuntimeError("boom"))
    _upload(client)
    first_id = str(_only_batch(db_session).id)

    monkeypatch.setattr(batches_module, "_lead_kwargs", _REAL_LEAD_KWARGS)
    second = _upload(client)
    assert second.status_code == 201
    assert second.json()["batch_id"] != first_id
    assert _lead_names(db_session, second.json()["batch_id"]) == sorted(ALL_NAMES)
    assert _lead_names(db_session, first_id) == sorted(ALL_NAMES[:2])
    db_session.expire_all()
    assert db_session.get(LeadBatch, uuid.UUID(first_id)).status == "partial"
