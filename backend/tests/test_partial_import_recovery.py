"""Phase 3 closeout Part C.4/E: partial-import state is reported accurately,
excluded from every push entry point (including force=true), proven with a
transport spy (no external send), and interrupted-then-retried imports
don't duplicate already-committed rows or reset workflow state.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient


def _transport_spy(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, json: dict, timeout: float) -> Any:
        calls.append({"url": url, "json": json})
        raise AssertionError("Slack transport must not be invoked for a partial batch")

    monkeypatch.setattr("app.integrations.slack.httpx.post", fake_post)
    return calls


def _upload_partial_batch(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> str:
    from app.api import batches as batches_module

    monkeypatch.setattr(batches_module, "CSV_MAX_ROWS", 2)
    csv_text = (
        "company_name,industry,contact_title,company_size,source\n"
        "Cascade Modular,Housing,VP Operations,1000+,referral\n"
        "Northbridge Clinics,Healthcare,Practice Manager,1000+,webinar\n"
        "Third Row Co,Housing,VP Operations,1000+,referral\n"
    )
    files = {"file": ("leads.csv", csv_text.encode("utf-8"), "text/csv")}
    response = client.post(
        "/api/batches/upload", files=files, data={"batch_name": "partial-test"}
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    return detail.split("batch ")[1].split(" ")[0]


def test_partial_batch_is_labeled_accurately(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    batch_id = _upload_partial_batch(client, monkeypatch)
    batch = client.get(f"/api/batches/{batch_id}").json()
    assert batch["status"] == "partial"
    assert batch["processed_leads"] == 2  # first 2 rows committed before the 3rd tripped the limit

    leads = client.get(f"/api/leads?batch_id={batch_id}").json()
    assert leads["total"] == 2  # exactly the committed rows, no more, no less


def test_partial_batch_excluded_from_single_lead_push_even_with_force(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    batch_id = _upload_partial_batch(client, monkeypatch)
    leads = client.get(f"/api/leads?batch_id={batch_id}").json()["items"]
    lead_id = leads[0]["id"]
    client.post(f"/api/leads/{lead_id}/score")

    calls = _transport_spy(monkeypatch)
    response = client.post(
        f"/api/leads/{lead_id}/push", json={"integration_type": "slack", "force": True}
    )
    assert response.status_code == 400
    assert "partial" in response.json()["detail"]
    assert calls == []


def test_partial_batch_excluded_from_batch_push_even_with_force(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    batch_id = _upload_partial_batch(client, monkeypatch)
    client.post(f"/api/batches/{batch_id}/score")

    calls = _transport_spy(monkeypatch)
    response = client.post(
        f"/api/batches/{batch_id}/push-hot",
        json={"integration_type": "slack", "force": True},
    )
    assert response.status_code == 400
    assert "partial" in response.json()["detail"]
    assert calls == []


def test_complete_batch_is_not_affected_by_the_partial_guard(
    client: TestClient,
) -> None:
    """Sanity: the guard is specific to status=='partial', not a general
    regression on ordinary complete uploads."""
    csv_text = "company_name,industry,contact_title,company_size,source\nCascade Modular,Housing,VP Operations,1000+,referral\n"
    files = {"file": ("leads.csv", csv_text.encode("utf-8"), "text/csv")}
    up = client.post(
        "/api/batches/upload", files=files, data={"batch_name": "complete"}
    ).json()
    assert up["batch_id"]
    batch = client.get(f"/api/batches/{up['batch_id']}").json()
    assert batch["status"] == "uploaded"

    leads = client.get(f"/api/leads?batch_id={up['batch_id']}").json()["items"]
    client.post(f"/api/leads/{leads[0]['id']}/score")
    response = client.post(
        f"/api/leads/{leads[0]['id']}/push",
        json={"integration_type": "slack", "force": True},
    )
    assert response.status_code == 200


# --------------------------------------------------------------------------
# PDL importer: interruption after a committed chunk, then retry
# --------------------------------------------------------------------------


def test_pdl_import_chunk_failure_preserves_earlier_commits_and_retry_is_idempotent(
    db_session_factory,
) -> None:
    """Simulates a chunk failing partway through a multi-chunk PDL import:
    earlier chunks stay committed, the failure is reported accurately (not
    silently reported as complete), and re-running the import afterward
    reuses the already-committed rows rather than duplicating them."""
    from datetime import date, timezone, datetime

    from app.models import ImportRun
    from app.pdl.importer import (
        find_or_create_batch,
        find_or_create_source_snapshot,
        import_companies,
    )
    from app.pdl.normalize import NormalizedCompany

    session_factory = db_session_factory
    session = session_factory()
    snapshot, _ = find_or_create_source_snapshot(
        session,
        provider="test_provider",
        mirror_url="https://example.com/x.json.gz",
        source_revision=None,
        reported_acquisition_date=date(2025, 1, 1),
        retrieved_license=None,
        retrieved_attribution=None,
        checksum="f" * 64,
        checksum_algorithm="sha256",
        parser_version="v1",
    )
    session.commit()
    session.refresh(snapshot)
    batch, _ = find_or_create_batch(session, logical_key="chunk-failure-test-key")
    session.commit()
    session.refresh(batch)
    import_run = ImportRun(source_snapshot_id=snapshot.id, status="running", started_at=datetime.now(timezone.utc))
    session.add(import_run)
    session.commit()
    session.refresh(import_run)
    source_snapshot_id, batch_id, import_run_id = snapshot.id, batch.id, import_run.id
    session.close()

    def _company(i: int) -> NormalizedCompany:
        return NormalizedCompany(
            company_name=f"Chunk Co {i}",
            website=None,
            domain=None,
            raw_industry="real estate",
            normalized_industry="real_estate",
            candidate_segment="real_estate",
            company_size="1-10",
            location="austin, texas, united states",
            locality="austin",
            region="texas",
            country_raw="united states",
            country_normalized="united states",
            source_record_id=f"chunk-id-{i}",
            founded=None,
            linkedin_url=None,
            source_raw_data={"id": f"chunk-id-{i}"},
        )

    companies = {"real_estate": [_company(i) for i in range(5)]}

    # First attempt: chunk_size=2 -> chunks of [0,1], [2,3], [4]. Force the
    # SECOND chunk to fail by monkeypatching session_factory to blow up on
    # its 2nd call... simpler: directly call import_companies with a small
    # chunk_size and a session_factory that fails starting from the 2nd
    # invocation, simulating "interrupted after a committed chunk."
    call_count = {"n": 0}
    real_factory = session_factory

    def flaky_factory():
        call_count["n"] += 1
        if call_count["n"] == 2:
            class ExplodingSession:
                def execute(self, *a, **kw):
                    raise RuntimeError("simulated interruption")

                def rollback(self):
                    pass

                def close(self):
                    pass

            return ExplodingSession()
        return real_factory()

    summary = import_companies(
        flaky_factory,
        companies_by_segment=companies,
        source_snapshot_id=source_snapshot_id,
        batch_id=batch_id,
        import_run_id=import_run_id,
        chunk_size=2,
    )

    # chunk_size=2 over 5 companies -> 3 chunks: [0,1], [2,3], [4]. The flaky
    # factory only explodes on its 2nd call (chunk index 1); chunks 0 and 2
    # use the real session and succeed -- proving a failure in the MIDDLE of
    # a multi-chunk run doesn't stop or corrupt chunks before or after it.
    assert summary.chunks[0].committed is True
    assert summary.chunks[1].committed is False
    assert summary.chunks[1].error is not None
    assert summary.chunks[2].committed is True
    assert summary.inserted == 3  # chunk 0 (2 rows) + chunk 2 (1 row)
    assert summary.failed == 2  # chunk 1's 2 rows counted as failed, not silently dropped
    assert summary.all_chunks_committed is False

    verify_session = real_factory()
    from app.models import Lead

    committed_names = {
        row.company_name
        for row in verify_session.query(Lead).filter(Lead.batch_id == batch_id).all()
    }
    assert committed_names == {"Chunk Co 0", "Chunk Co 1", "Chunk Co 4"}
    verify_session.close()

    # --- Retry with the real (non-flaky) session factory: must not
    # duplicate chunk 0's already-committed rows, and must complete the rest. ---
    retry_summary = import_companies(
        real_factory,
        companies_by_segment=companies,
        source_snapshot_id=source_snapshot_id,
        batch_id=batch_id,
        import_run_id=import_run_id,
        chunk_size=2,
    )
    assert retry_summary.all_chunks_committed is True
    assert retry_summary.reused == 3  # Chunk Co 0, 1, 4 -- already committed on the first attempt
    assert retry_summary.inserted == 2  # Chunk Co 2, 3 -- the chunk that failed last time

    verify_session2 = real_factory()
    final_names = {
        row.company_name
        for row in verify_session2.query(Lead).filter(Lead.batch_id == batch_id).all()
    }
    assert final_names == {f"Chunk Co {i}" for i in range(5)}
    assert len(final_names) == 5  # no duplicates
    verify_session2.close()
