"""Phase 3 closeout Part B: source-identity provenance is mandatory (never
just an optional flag), and same-source/same-selection converges to the
same SourceSnapshot and LeadBatch, matched by a persisted logical_key, not
by name.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.models import LeadBatch, SourceSnapshot
from app.pdl.cli import ProvenanceError, validate_snapshot_info
from app.pdl.importer import find_or_create_batch, find_or_create_source_snapshot

VALID_SNAPSHOT_INFO = {
    "provider": "people_data_labs",
    "mirror_url": "https://huggingface.co/datasets/andreaaltomani/company-dataset/resolve/main/free_company_dataset.json.gz",
    "checksum": "a" * 64,
    "checksum_algorithm": "sha256",
    "source_revision": "abc123",
    "reported_acquisition_date": "2025-07-28",
    "retrieved_license": "CC-BY-4.0",
    "retrieved_attribution": "PDL via HF",
    "parser_version": "pdl-alias-v1",
}


# --------------------------------------------------------------------------
# validate_snapshot_info: fail clearly, before any write
# --------------------------------------------------------------------------


def test_validate_snapshot_info_accepts_complete_provenance() -> None:
    validate_snapshot_info(dict(VALID_SNAPSHOT_INFO))  # must not raise


@pytest.mark.parametrize("missing_field", ["provider", "mirror_url", "checksum", "checksum_algorithm"])
def test_validate_snapshot_info_rejects_missing_field(missing_field: str) -> None:
    info = dict(VALID_SNAPSHOT_INFO)
    info[missing_field] = None
    with pytest.raises(ProvenanceError, match=missing_field):
        validate_snapshot_info(info)


def test_validate_snapshot_info_rejects_empty_checksum() -> None:
    info = dict(VALID_SNAPSHOT_INFO)
    info["checksum"] = ""
    with pytest.raises(ProvenanceError):
        validate_snapshot_info(info)


def test_validate_snapshot_info_rejects_wrong_length_checksum() -> None:
    info = dict(VALID_SNAPSHOT_INFO)
    info["checksum"] = "not-a-real-sha256"
    with pytest.raises(ProvenanceError, match="hex digest"):
        validate_snapshot_info(info)


def test_validate_snapshot_info_rejects_mismatched_algorithm() -> None:
    info = dict(VALID_SNAPSHOT_INFO)
    info["checksum_algorithm"] = "md5"
    with pytest.raises(ProvenanceError, match="checksum_algorithm"):
        validate_snapshot_info(info)


# --------------------------------------------------------------------------
# find_or_create_source_snapshot: convergence by (provider, checksum)
# --------------------------------------------------------------------------


def test_find_or_create_source_snapshot_converges_on_identical_checksum(
    db_session: Session,
) -> None:
    kwargs = dict(
        provider="people_data_labs",
        mirror_url="https://example.com/x.json.gz",
        source_revision="rev1",
        reported_acquisition_date=date(2025, 7, 28),
        retrieved_license="CC-BY-4.0",
        retrieved_attribution="attr",
        checksum="b" * 64,
        checksum_algorithm="sha256",
        parser_version="v1",
    )
    first, created1 = find_or_create_source_snapshot(db_session, **kwargs)
    db_session.commit()
    second, created2 = find_or_create_source_snapshot(db_session, **kwargs)
    db_session.commit()

    assert created1 is True
    assert created2 is False
    assert first.id == second.id
    count = db_session.query(SourceSnapshot).count()
    assert count == 1


def test_find_or_create_source_snapshot_requires_nonempty_checksum(
    db_session: Session,
) -> None:
    with pytest.raises(ValueError):
        find_or_create_source_snapshot(
            db_session,
            provider="people_data_labs",
            mirror_url="https://example.com/x.json.gz",
            source_revision=None,
            reported_acquisition_date=None,
            retrieved_license=None,
            retrieved_attribution=None,
            checksum="",
            checksum_algorithm="sha256",
            parser_version=None,
        )


def test_different_checksum_creates_a_distinct_snapshot(db_session: Session) -> None:
    kwargs = dict(
        provider="people_data_labs",
        mirror_url="https://example.com/x.json.gz",
        source_revision=None,
        reported_acquisition_date=None,
        retrieved_license=None,
        retrieved_attribution=None,
        checksum_algorithm="sha256",
        parser_version=None,
    )
    a, _ = find_or_create_source_snapshot(db_session, checksum="c" * 64, **kwargs)
    db_session.commit()
    b, _ = find_or_create_source_snapshot(db_session, checksum="d" * 64, **kwargs)
    db_session.commit()
    assert a.id != b.id
    assert db_session.query(SourceSnapshot).count() == 2


# --------------------------------------------------------------------------
# find_or_create_batch: convergence by logical_key, name is irrelevant
# --------------------------------------------------------------------------


def test_find_or_create_batch_converges_on_identical_logical_key(
    db_session: Session,
) -> None:
    first, created1 = find_or_create_batch(db_session, logical_key="samekey123")
    db_session.commit()
    second, created2 = find_or_create_batch(db_session, logical_key="samekey123")
    db_session.commit()

    assert created1 is True
    assert created2 is False
    assert first.id == second.id
    assert db_session.query(LeadBatch).count() == 1


def test_renaming_batch_does_not_break_logical_key_matching(db_session: Session) -> None:
    batch, _ = find_or_create_batch(db_session, logical_key="rename-test-key")
    db_session.commit()

    batch.name = "A completely different human-chosen name"
    db_session.commit()

    again, created = find_or_create_batch(db_session, logical_key="rename-test-key")
    db_session.commit()

    assert created is False
    assert again.id == batch.id
    assert again.name == "A completely different human-chosen name"


def test_find_or_create_batch_requires_nonempty_logical_key(db_session: Session) -> None:
    with pytest.raises(ValueError):
        find_or_create_batch(db_session, logical_key="")


def test_different_logical_key_creates_a_distinct_batch(db_session: Session) -> None:
    a, _ = find_or_create_batch(db_session, logical_key="key-a")
    db_session.commit()
    b, _ = find_or_create_batch(db_session, logical_key="key-b")
    db_session.commit()
    assert a.id != b.id
    assert db_session.query(LeadBatch).count() == 2
