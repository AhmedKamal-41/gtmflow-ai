"""Phase 3 Part E.5: pagination is consistent, stably ordered (with a
unique tie-breaker so rows are never omitted or duplicated across pages),
and caps at the configured max page size."""
from __future__ import annotations

from fastapi.testclient import TestClient

CSV_TEMPLATE = "company_name,industry\n" + "".join(
    f"Company {i},Housing\n" for i in range(25)
)


def _upload(client: TestClient) -> str:
    files = {"file": ("leads.csv", CSV_TEMPLATE.encode("utf-8"), "text/csv")}
    return client.post(
        "/api/batches/upload", files=files, data={"batch_name": "pagination-test"}
    ).json()["batch_id"]


def test_leads_pagination_walks_all_rows_without_gaps_or_dupes(
    client: TestClient,
) -> None:
    batch_id = _upload(client)
    seen_ids: list[str] = []
    offset = 0
    limit = 7
    while True:
        page = client.get(
            f"/api/leads?batch_id={batch_id}&limit={limit}&offset={offset}"
        ).json()
        seen_ids.extend(item["id"] for item in page["items"])
        if not page["has_more"]:
            break
        offset += limit

    assert page["total"] == 25
    assert len(seen_ids) == 25
    assert len(set(seen_ids)) == 25  # no duplicates across pages


def test_leads_pagination_total_reflects_full_dataset_not_page_size(
    client: TestClient,
) -> None:
    batch_id = _upload(client)
    page = client.get(f"/api/leads?batch_id={batch_id}&limit=5&offset=0").json()
    assert len(page["items"]) == 5
    assert page["total"] == 25  # server-computed across the whole dataset
    assert page["has_more"] is True


def test_leads_pagination_rejects_over_max_page_size(client: TestClient) -> None:
    response = client.get("/api/leads?limit=99999")
    assert response.status_code == 422


def test_batches_list_is_paginated(client: TestClient) -> None:
    for i in range(3):
        files = {
            "file": ("leads.csv", "company_name\nX\n".encode("utf-8"), "text/csv")
        }
        client.post(
            "/api/batches/upload", files=files, data={"batch_name": f"batch-{i}"}
        )
    page = client.get("/api/batches?limit=2").json()
    assert page["total"] == 3
    assert len(page["items"]) == 2
    assert page["has_more"] is True
