"""Idempotent DB import: SourceSnapshot / ImportRun / CompanyIdentity /
Lead, in chunked, individually-committed batches so a partial failure is
honestly labeled and resumable rather than silently reported complete.

Idempotency is enforced at two levels:
  - Application level: find-or-create CompanyIdentity/Lead by
    (source_snapshot_id, source_record_id) before inserting.
  - Database level: uq_company_identities_snapshot_source_record and
    uq_leads_snapshot_source_record (migration 0003) make it impossible to
    double-insert even under a race, independent of the find-or-create logic
    above -- see docs/engineering-log's Phase 3 handoff for a live verification of
    both layers, including what happens if the application-level check is
    bypassed.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CompanyIdentity, ImportRun, Lead, LeadBatch, SourceSnapshot, WorkflowEvent
from app.pdl.identity import compute_identity
from app.pdl.normalize import NormalizedCompany

CHUNK_SIZE = 500
LEAD_SOURCE_LABEL = "pdl_import"


def compute_logical_key(
    *,
    source_checksum: str,
    mapping_version: str,
    target_per_segment: dict[str, int],
    seed: int,
    country_filter: str,
) -> str:
    """Deterministic identity for "this exact curated selection" (Part D.6).
    Two runs with the same source file, mapping version, targets, seed, and
    country filter produce the same key -- and, given a full (EOF-reaching)
    scan, the same actual selection, since reservoir sampling is seeded."""
    payload = "|".join(
        [
            source_checksum,
            mapping_version,
            ",".join(f"{k}={v}" for k, v in sorted(target_per_segment.items())),
            str(seed),
            country_filter,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def find_or_create_source_snapshot(
    session: Session,
    *,
    provider: str,
    mirror_url: str,
    source_revision: str | None,
    reported_acquisition_date,
    retrieved_license: str | None,
    retrieved_attribution: str | None,
    checksum: str,
    checksum_algorithm: str,
    parser_version: str | None,
) -> tuple[SourceSnapshot, bool]:
    """Reuse an existing snapshot row for the same (provider, checksum) if
    one exists, rather than creating a duplicate on every CLI invocation.
    Returns (snapshot, created).

    `checksum` is required (not optional) -- callers must validate
    provenance (see cli.py's `validate_snapshot_info`) before calling this.
    Race-safe: `uq_source_snapshots_provider_checksum` (migration 0004)
    means a concurrent duplicate INSERT fails at the database, not just the
    application's SELECT-then-INSERT check; on that failure this retries
    the SELECT once and returns the row the *other* transaction committed,
    rather than raising or silently creating a second row.
    """
    if not checksum:
        raise ValueError("find_or_create_source_snapshot requires a non-empty checksum")

    existing = session.execute(
        select(SourceSnapshot).where(
            SourceSnapshot.provider == provider,
            SourceSnapshot.checksum == checksum,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    snapshot = SourceSnapshot(
        provider=provider,
        mirror_url=mirror_url,
        source_revision=source_revision,
        reported_acquisition_date=reported_acquisition_date,
        retrieved_license=retrieved_license,
        retrieved_attribution=retrieved_attribution,
        checksum=checksum,
        checksum_algorithm=checksum_algorithm,
        parser_version=parser_version,
    )
    session.add(snapshot)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        winner = session.execute(
            select(SourceSnapshot).where(
                SourceSnapshot.provider == provider,
                SourceSnapshot.checksum == checksum,
            )
        ).scalar_one_or_none()
        if winner is None:
            raise  # genuinely not a uniqueness race -- re-raise the original failure
        return winner, False
    return snapshot, True


def find_or_create_batch(session: Session, *, logical_key: str) -> tuple[LeadBatch, bool]:
    """Look up by the persisted, full `logical_key` column -- never by
    `name`. Batch names stay freely editable (a rename never breaks
    matching) and are no longer part of the identity mechanism at all.
    Race-safe via `uq_lead_batches_logical_key` (migration 0004), same
    catch-and-retry pattern as find_or_create_source_snapshot.
    """
    if not logical_key:
        raise ValueError("find_or_create_batch requires a non-empty logical_key")

    existing = session.execute(
        select(LeadBatch).where(LeadBatch.logical_key == logical_key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    batch = LeadBatch(
        name=f"PDL import ({logical_key[:12]})",
        source=LEAD_SOURCE_LABEL,
        status="uploaded",
        logical_key=logical_key,
    )
    session.add(batch)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        winner = session.execute(
            select(LeadBatch).where(LeadBatch.logical_key == logical_key)
        ).scalar_one_or_none()
        if winner is None:
            raise
        return winner, False
    return batch, True


@dataclass
class ChunkResult:
    chunk_index: int
    attempted: int
    inserted: int
    reused: int
    failed: int
    error: str | None = None
    committed: bool = False


@dataclass
class ImportSummary:
    import_run_id: Any
    source_snapshot_id: Any
    batch_id: Any
    inserted: int = 0
    reused: int = 0
    failed: int = 0
    chunks: list[ChunkResult] = field(default_factory=list)

    @property
    def all_chunks_committed(self) -> bool:
        return all(c.committed for c in self.chunks)


def _lead_cleaned_data(company: NormalizedCompany, segment: str) -> dict[str, Any]:
    return {
        "candidate_segment": segment,
        "locality": company.locality,
        "region": company.region,
        "country": company.country_raw,
        "founded": company.founded,
        "linkedin_url": company.linkedin_url,
        # Explicit, honest labels -- Part C of the brief: broad segment
        # membership is not a qualification claim.
        "segment_is_candidate_classification_only": True,
    }


def import_companies(
    session_factory,
    *,
    companies_by_segment: dict[str, list[NormalizedCompany]],
    source_snapshot_id: Any,
    batch_id: Any,
    import_run_id: Any,
    chunk_size: int = CHUNK_SIZE,
) -> ImportSummary:
    """Import in chunks, each its own transaction. A chunk that fails is
    rolled back and recorded as failed -- already-committed chunks are not
    undone, and the ImportRun's status reflects "partial" honestly rather
    than "completed" if any chunk didn't commit (see the caller in cli.py).

    Takes plain ids, not ORM objects, deliberately: each chunk opens its own
    session (so one chunk's failure/rollback can't poison another's
    transaction), and an object loaded in a different, now-closed session
    would raise DetachedInstanceError the moment a chunk touched it.
    """
    flat: list[tuple[str, NormalizedCompany]] = [
        (segment, company)
        for segment, companies in companies_by_segment.items()
        for company in companies
    ]

    summary = ImportSummary(
        import_run_id=import_run_id,
        source_snapshot_id=source_snapshot_id,
        batch_id=batch_id,
    )

    for chunk_index, start in enumerate(range(0, len(flat), chunk_size)):
        chunk = flat[start : start + chunk_size]
        session = session_factory()
        result = ChunkResult(
            chunk_index=chunk_index, attempted=len(chunk), inserted=0, reused=0, failed=0
        )
        try:
            for segment, company in chunk:
                identity = compute_identity(company)

                existing_identity = session.execute(
                    select(CompanyIdentity).where(
                        CompanyIdentity.source_snapshot_id == source_snapshot_id,
                        CompanyIdentity.source_record_id == identity.key,
                    )
                ).scalar_one_or_none()
                if existing_identity is None:
                    company_identity = CompanyIdentity(
                        canonical_name=company.company_name,
                        website_domain=company.domain,
                        source_snapshot_id=source_snapshot_id,
                        source_record_id=identity.key,
                        identity_confidence=identity.confidence,
                        source_raw_identity=company.source_raw_data,
                    )
                    session.add(company_identity)
                    session.flush()
                else:
                    company_identity = existing_identity

                existing_lead = session.execute(
                    select(Lead).where(
                        Lead.source_snapshot_id == source_snapshot_id,
                        Lead.source_record_id == identity.key,
                    )
                ).scalar_one_or_none()
                if existing_lead is not None:
                    result.reused += 1
                    continue

                lead = Lead(
                    batch_id=batch_id,
                    company_name=company.company_name,
                    website=company.website,
                    industry=company.raw_industry,
                    company_size=company.company_size,
                    location=company.location,
                    source=LEAD_SOURCE_LABEL,
                    cleaned_data=_lead_cleaned_data(company, segment),
                    company_identity_id=company_identity.id,
                    source_snapshot_id=source_snapshot_id,
                    import_run_id=import_run_id,
                    source_record_id=identity.key,
                    source_raw_data=company.source_raw_data,
                )
                session.add(lead)
                result.inserted += 1

            session.add(
                WorkflowEvent(
                    batch_id=batch_id,
                    event_type="pdl_import_chunk_committed",
                    event_data={
                        "import_run_id": str(import_run_id),
                        "chunk_index": chunk_index,
                        "attempted": result.attempted,
                        "inserted": result.inserted,
                        "reused": result.reused,
                    },
                )
            )
            session.commit()
            result.committed = True
        except IntegrityError as e:
            session.rollback()
            result.failed = len(chunk) - result.inserted - result.reused
            result.error = f"IntegrityError: {e.orig if hasattr(e, 'orig') else e}"
        except Exception as e:  # noqa: BLE001 -- any other failure still must not look silent
            session.rollback()
            result.failed = len(chunk) - result.inserted - result.reused
            result.error = f"{type(e).__name__}: {e}"
        finally:
            session.close()

        summary.chunks.append(result)
        if result.committed:
            summary.inserted += result.inserted
            summary.reused += result.reused
        else:
            summary.failed += len(chunk)

    return summary
