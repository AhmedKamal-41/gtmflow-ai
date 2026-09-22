"""Phase 4 closeout (item 4 / originally Part A.4): resuming a CSV upload
that previously left a batch 'partial' requires an EXPLICIT
`resume_batch_id` -- content-hash matching alone is never enough to infer
"this is a retry," since uploading byte-identical content as a
deliberately separate operation is legitimate and must remain distinct.
Omitting `resume_batch_id` always creates a new batch, even for an
identical file. Resuming with mismatched content, a non-partial batch, or
a nonexistent batch id is rejected.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

CSV_TEXT = (
    "company_name,industry,contact_title,company_size,source\n"
    "Cascade Modular,Housing,VP Operations,1000+,referral\n"
    "Northbridge Clinics,Healthcare,Practice Manager,1000+,webinar\n"
    "Third Row Co,Housing,VP Operations,1000+,referral\n"
)


def _upload(
    client: TestClient,
    raw: str,
    filename: str = "leads.csv",
    resume_batch_id: str | None = None,
):
    files = {"file": (filename, raw.encode("utf-8"), "text/csv")}
    data = {"batch_name": "retry-test"}
    if resume_batch_id is not None:
        data["resume_batch_id"] = resume_batch_id
    return client.post("/api/batches/upload", files=files, data=data)


def _make_partial_batch(client: TestClient, monkeypatch, max_rows: int = 2) -> str:
    from app.api import batches as batches_module

    monkeypatch.setattr(batches_module, "CSV_MAX_ROWS", max_rows)
    response = _upload(client, CSV_TEXT)
    assert response.status_code == 422
    return response.json()["detail"].split("batch ")[1].split(" ")[0]


def test_explicit_resume_batch_id_resumes_same_batch_without_duplicating_rows(
    client: TestClient, monkeypatch
) -> None:
    from app.api import batches as batches_module

    # First attempt: row limit trips after 2 of the 3 valid rows, leaving
    # the batch "partial" with 2 rows committed.
    first_batch_id = _make_partial_batch(client, monkeypatch, max_rows=2)

    batch_after_first = client.get(f"/api/batches/{first_batch_id}").json()
    assert batch_after_first["status"] == "partial"
    assert batch_after_first["processed_leads"] == 2

    leads_after_first = client.get(f"/api/leads?batch_id={first_batch_id}").json()
    assert leads_after_first["total"] == 2

    # Retry with the byte-identical file AND the explicit resume_batch_id,
    # row limit lifted -- must resume into the SAME batch id.
    monkeypatch.setattr(batches_module, "CSV_MAX_ROWS", 50_000)
    second = _upload(client, CSV_TEXT, resume_batch_id=first_batch_id)
    assert second.status_code == 201
    body = second.json()
    assert body["batch_id"] == first_batch_id
    assert body["valid_rows"] == 3  # 2 previously committed + 1 new

    batch_after_retry = client.get(f"/api/batches/{first_batch_id}").json()
    assert batch_after_retry["status"] == "uploaded"
    assert batch_after_retry["processed_leads"] == 3
    assert batch_after_retry["total_leads"] == 3

    leads_after_retry = client.get(f"/api/leads?batch_id={first_batch_id}").json()
    assert leads_after_retry["total"] == 3  # no duplicates
    companies = {lead["company_name"] for lead in leads_after_retry["items"]}
    assert companies == {"Cascade Modular", "Northbridge Clinics", "Third Row Co"}

    # Only one batch exists -- the resume did not create a second row in
    # lead_batches.
    all_batches = client.get("/api/batches?limit=50").json()["items"]
    matching = [b for b in all_batches if b["id"] == first_batch_id]
    assert len(matching) == 1


def test_identical_content_without_resume_batch_id_is_a_new_batch_not_a_resume(
    client: TestClient, monkeypatch
) -> None:
    """This is the core distinction the checkpoint asked to fix: byte-
    identical content is NOT, by itself, evidence of a retry. Without the
    explicit resume_batch_id, even the exact same file that just left a
    batch 'partial' must create a brand-new batch, not silently merge into
    the old one."""
    first_batch_id = _make_partial_batch(client, monkeypatch, max_rows=2)

    from app.api import batches as batches_module

    monkeypatch.setattr(batches_module, "CSV_MAX_ROWS", 50_000)
    second = _upload(client, CSV_TEXT)  # identical bytes, no resume_batch_id
    assert second.status_code == 201
    second_batch_id = second.json()["batch_id"]

    assert second_batch_id != first_batch_id
    assert second.json()["valid_rows"] == 3  # fresh batch, all 3 rows new

    # The original partial batch is completely untouched.
    original = client.get(f"/api/batches/{first_batch_id}").json()
    assert original["status"] == "partial"
    assert original["processed_leads"] == 2

    # Both batches independently exist and hold their own leads.
    original_leads = client.get(f"/api/leads?batch_id={first_batch_id}").json()
    new_leads = client.get(f"/api/leads?batch_id={second_batch_id}").json()
    assert original_leads["total"] == 2
    assert new_leads["total"] == 3


def test_retrying_after_full_success_would_be_a_new_batch_not_a_resume(
    client: TestClient,
) -> None:
    """A batch that already reached 'uploaded' is not a resume target --
    re-uploading the same content after a COMPLETE upload is a deliberate
    new upload (unchanged, out-of-scope behavior) and gets its own batch."""
    first = _upload(client, CSV_TEXT)
    assert first.status_code == 201
    first_batch_id = first.json()["batch_id"]

    second = _upload(client, CSV_TEXT)
    assert second.status_code == 201
    second_batch_id = second.json()["batch_id"]

    assert second_batch_id != first_batch_id


def test_resume_batch_id_against_non_partial_batch_is_rejected(
    client: TestClient,
) -> None:
    first = _upload(client, CSV_TEXT)
    assert first.status_code == 201
    batch_id = first.json()["batch_id"]  # status is "uploaded", not "partial"

    retry = _upload(client, CSV_TEXT, resume_batch_id=batch_id)
    assert retry.status_code == 409
    assert "not 'partial'" in retry.json()["detail"]


def test_resume_batch_id_against_nonexistent_batch_is_rejected(
    client: TestClient,
) -> None:
    fake_id = "00000000-0000-0000-0000-000000000000"
    response = _upload(client, CSV_TEXT, resume_batch_id=fake_id)
    assert response.status_code == 404


def test_resume_batch_id_rejects_changed_content_presented_as_same_retry(
    client: TestClient, monkeypatch
) -> None:
    """A different file must never be accepted as a resume of a partial
    batch just because the caller claims a resume_batch_id -- the server
    independently verifies the content hash still matches."""
    first_batch_id = _make_partial_batch(client, monkeypatch, max_rows=2)

    from app.api import batches as batches_module

    monkeypatch.setattr(batches_module, "CSV_MAX_ROWS", 50_000)
    changed_csv = CSV_TEXT + "Sneaky Extra Co,Retail,,,\n"
    response = _upload(client, changed_csv, resume_batch_id=first_batch_id)
    assert response.status_code == 409
    assert "does not match" in response.json()["detail"]

    # The original partial batch is untouched by the rejected attempt.
    original = client.get(f"/api/batches/{first_batch_id}").json()
    assert original["status"] == "partial"
    assert original["processed_leads"] == 2


def test_genuinely_different_retry_upload_is_a_separate_batch(
    client: TestClient, monkeypatch
) -> None:
    first_batch_id = _make_partial_batch(client, monkeypatch, max_rows=2)

    from app.api import batches as batches_module

    monkeypatch.setattr(batches_module, "CSV_MAX_ROWS", 50_000)
    different_csv = (
        "company_name,industry\n"
        "Totally Different Co,Retail\n"
    )
    second = _upload(client, different_csv, filename="other.csv")
    assert second.status_code == 201
    assert second.json()["batch_id"] != first_batch_id

    # The original partial batch is untouched.
    original = client.get(f"/api/batches/{first_batch_id}").json()
    assert original["status"] == "partial"
    assert original["processed_leads"] == 2


def test_resume_preserves_incomplete_batch_push_routing_restriction(
    client: TestClient, monkeypatch
) -> None:
    """A batch mid-resume (still partial after a second interrupted
    attempt) must stay excluded from Slack push, same as any other partial
    batch (Phase 3 closeout guard, unaffected by the resume path)."""
    from app.api import batches as batches_module

    batch_id = _make_partial_batch(client, monkeypatch, max_rows=1)

    monkeypatch.setattr(batches_module, "CSV_MAX_ROWS", 2)
    second = _upload(client, CSV_TEXT, resume_batch_id=batch_id)
    assert second.status_code == 422  # still short of all 3 rows

    batch = client.get(f"/api/batches/{batch_id}").json()
    assert batch["status"] == "partial"
    assert batch["processed_leads"] == 2

    client.post(f"/api/batches/{batch_id}/score")
    push_response = client.post(
        f"/api/batches/{batch_id}/push-hot",
        json={"integration_type": "slack", "force": True},
    )
    assert push_response.status_code == 400
    assert "partial" in push_response.json()["detail"]
