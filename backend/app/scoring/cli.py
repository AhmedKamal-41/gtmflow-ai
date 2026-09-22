"""Phase 4 Part F: score the real, already-imported cohort with the v2
deterministic company-fit scorer (app/scoring/fit.py), in bounded batches.

Reuses the EXACT same compute+persist path as `POST /api/leads/{id}/fit-score`
(app.api.fit_scoring._score_and_optionally_persist) -- a lead scored via this
CLI and one scored via the API are indistinguishable, same versions, same
storage. No new download, no re-scan of the original source corpus (only
already-imported `Lead` rows are read), no LLM calls, no Slack sends. A
lead's `Lead.status` / its batch's `LeadBatch.status` are never touched (see
fit.py / fit_scoring.py's Part D.3 invariant) -- this command inserts
`LeadFitScore` rows only.

Usage:
    python -m app.scoring.cli [--dry-run] [--chunk-size N] [--limit N]

--dry-run computes and reports the same summary WITHOUT writing any
LeadFitScore rows (each chunk's session is rolled back instead of committed).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from typing import Any

from sqlalchemy import select

from app.api.fit_scoring import _score_and_optionally_persist
from app.core.database import get_sessionmaker
from app.models import Lead

DEFAULT_CHUNK_SIZE = 500


def _segment_of(lead: Lead) -> str:
    """PDL import already classified every imported lead into exactly one
    of two candidate segments (app/pdl/config.py's TARGET_PER_SEGMENT) --
    stored, verbatim, in `cleaned_data["candidate_segment"]`. A lead with no
    such key (e.g. a CSV/demo lead, out of scope for this real-cohort run
    but handled honestly rather than crashing) is reported as
    'unclassified' rather than silently grouped into a PDL segment it was
    never part of.
    """
    cleaned = lead.cleaned_data
    if isinstance(cleaned, dict):
        segment = cleaned.get("candidate_segment")
        if isinstance(segment, str) and segment:
            return segment
    return "unclassified"


def score_cohort(
    session_factory,
    *,
    dry_run: bool,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    limit: int | None = None,
) -> dict[str, Any]:
    """`session_factory` is an injectable `sessionmaker` (defaults to the
    app's real one in `main()` below) so tests can point this at a
    disposable SQLite session factory instead of the real Postgres DB."""
    probe = session_factory()
    try:
        stmt = select(Lead.id).order_by(Lead.id)
        if limit is not None:
            stmt = stmt.limit(limit)
        lead_ids = [row[0] for row in probe.execute(stmt).all()]
    finally:
        probe.close()

    attempted_by_segment: Counter[str] = Counter()
    succeeded_by_segment: Counter[str] = Counter()
    failed_by_segment: Counter[str] = Counter()
    band_counts: Counter[str] = Counter()
    coverage_values: list[float] = []
    fit_values: list[int] = []
    readiness_gap_counts: Counter[str] = Counter()
    eligibility_reason_counts: Counter[str] = Counter()
    errors: list[dict[str, str]] = []

    started = time.perf_counter()

    for start in range(0, len(lead_ids), chunk_size):
        chunk_ids = lead_ids[start : start + chunk_size]
        session = session_factory()
        try:
            leads = (
                session.execute(select(Lead).where(Lead.id.in_(chunk_ids)))
                .scalars()
                .all()
            )
            for lead in leads:
                segment = _segment_of(lead)
                attempted_by_segment[segment] += 1
                try:
                    response = _score_and_optionally_persist(
                        session, lead, persist=not dry_run
                    )
                    succeeded_by_segment[segment] += 1
                    band_counts[response.band] += 1
                    coverage_values.append(response.evidence_coverage_pct)
                    fit_values.append(response.fit_score)
                    for action_name in ("outbound_email", "internal_slack_handoff"):
                        action = getattr(response.readiness, action_name)
                        for gap in action.gaps:
                            readiness_gap_counts[f"{action_name}:{gap}"] += 1
                    for reason in response.eligibility.reasons:
                        eligibility_reason_counts[reason] += 1
                except Exception as e:  # noqa: BLE001 -- bounded-batch resilience
                    failed_by_segment[segment] += 1
                    errors.append({"lead_id": str(lead.id), "error": str(e)})

            if dry_run:
                session.rollback()
            else:
                session.commit()
        finally:
            session.close()

    elapsed_s = round(time.perf_counter() - started, 3)

    def _pct(values: list[float]) -> dict[str, float]:
        if not values:
            return {"min": 0.0, "max": 0.0, "mean": 0.0}
        return {
            "min": round(min(values), 1),
            "max": round(max(values), 1),
            "mean": round(sum(values) / len(values), 1),
        }

    return {
        "dry_run": dry_run,
        "chunk_size": chunk_size,
        "total_leads_considered": len(lead_ids),
        "attempted_by_segment": dict(attempted_by_segment),
        "succeeded_by_segment": dict(succeeded_by_segment),
        "failed_by_segment": dict(failed_by_segment),
        "total_attempted": sum(attempted_by_segment.values()),
        "total_succeeded": sum(succeeded_by_segment.values()),
        "total_failed": sum(failed_by_segment.values()),
        "band_counts": dict(band_counts),
        "fit_score_distribution": _pct([float(v) for v in fit_values]),
        "evidence_coverage_pct_distribution": _pct(coverage_values),
        "readiness_gap_counts": dict(readiness_gap_counts),
        "eligibility_reason_counts": dict(eligibility_reason_counts),
        "errors": errors[:20],  # bounded example list, not unbounded
        "elapsed_seconds": elapsed_s,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and report but write no LeadFitScore rows.",
    )
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument(
        "--limit", type=int, default=None, help="Score at most this many leads."
    )
    args = parser.parse_args(argv)

    summary = score_cohort(
        get_sessionmaker(),
        dry_run=args.dry_run,
        chunk_size=args.chunk_size,
        limit=args.limit,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["total_failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
