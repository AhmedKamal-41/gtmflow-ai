"""Phase 3 Part E: CSV upload size/row bounds are enforced while reading,
not trusted from client-supplied headers, and error reporting stays bounded
(examples capped) while counts stay accurate (unbounded)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.csv_ingestion import parse_csv


def test_parse_csv_rejects_over_row_limit() -> None:
    import pytest

    from app.services.csv_ingestion import CSVValidationError

    header = "company_name\n"
    rows = "".join(f"Company {i}\n" for i in range(10))
    csv_text = header + rows

    unbounded = parse_csv(csv_text, max_rows=None)
    assert unbounded.total_rows == 10  # sanity: no limit parses everything

    # max_rows enforcement raises, doesn't silently truncate.
    with pytest.raises(CSVValidationError):
        parse_csv(csv_text, max_rows=5)


def test_parse_csv_bounds_error_examples_but_counts_all() -> None:
    # 20 rows with an empty company_name but a non-empty second column, so
    # the row isn't dropped entirely as blank -- it's a genuine per-row
    # validation error (missing required field), not an empty line.
    rows = "".join(f",extra-{i}\n" for i in range(20))
    csv_text = "company_name,extra\n" + rows
    result = parse_csv(csv_text, max_error_examples=5)
    assert result.total_error_count == 20
    assert len(result.errors) == 5


def test_upload_endpoint_rejects_oversized_file(client: TestClient, monkeypatch) -> None:
    from app.api import batches as batches_module

    monkeypatch.setattr(batches_module, "CSV_MAX_BYTES", 100)  # tiny limit for the test
    csv_text = "company_name\n" + "".join(f"Company Number {i}\n" for i in range(50))
    files = {"file": ("big.csv", csv_text.encode("utf-8"), "text/csv")}
    response = client.post(
        "/api/batches/upload", files=files, data={"batch_name": "oversized"}
    )
    assert response.status_code == 413
    assert "MiB" in response.json()["detail"]


def test_upload_endpoint_rejects_over_row_limit(client: TestClient, monkeypatch) -> None:
    """Phase 3 closeout: exceeding max_rows mid-stream no longer means
    nothing was written -- rows already parsed before the limit was hit are
    committed, and the batch is marked 'partial' (excluded from push, see
    test_partial_batch_excluded_from_push in test_partial_import_recovery.py)
    rather than silently discarded or reported as if it fully succeeded."""
    from app.api import batches as batches_module

    monkeypatch.setattr(batches_module, "CSV_MAX_ROWS", 3)
    csv_text = "company_name\n" + "".join(f"Company {i}\n" for i in range(10))
    files = {"file": ("many_rows.csv", csv_text.encode("utf-8"), "text/csv")}
    response = client.post(
        "/api/batches/upload", files=files, data={"batch_name": "too-many-rows"}
    )
    assert response.status_code == 422
    assert "more than 3 data rows" in response.json()["detail"]
    assert "already committed" in response.json()["detail"]

    batch_id = response.json()["detail"].split("batch ")[1].split(" ")[0]
    batch = client.get(f"/api/batches/{batch_id}").json()
    assert batch["status"] == "partial"
    assert batch["processed_leads"] == 3  # rows 1-3 committed before row 4 tripped the limit
