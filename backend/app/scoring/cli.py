"""Phase 4 Part F: score the real, already-imported cohort with the v2
deterministic company-fit scorer (app/scoring/fit.py), in bounded batches.

Reuses the EXACT same compute+persist path as the fit-score API
(app.api.fit_scoring.score_leads) -- a lead scored via this CLI and one
scored via the API are indistinguishable, same versions, same storage. No
new download, no re-scan of the original source corpus (only
already-imported `Lead` rows are read), no LLM calls, no Slack sends. A
lead's `Lead.status` / its batch's `LeadBatch.status` are never touched (see
fit.py / fit_scoring.py's Part D.3 invariant) -- this command inserts
`LeadFitScore` rows only.

Usage:
    python -m app.scoring.cli [--dry-run] [--chunk-size N] [--limit N]
                              [--rescore-unchanged]

--dry-run computes and reports the same summary WITHOUT writing any
LeadFitScore rows (each chunk's session is rolled back instead of committed).

By default a lead whose latest applicable score already has the identical
input fingerprint is skipped (counted under `skipped_unchanged_by_segment`),
so rerunning after a successful run writes nothing new.
--rescore-unchanged writes a fresh row anyway.

A failure on one lead rolls back only that lead's savepoint; a failure
committing a whole chunk counts every lead in that chunk as failed. Either
way the run continues with the next chunk.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from typing import Any

from sqlalchemy import select

from app.api.fit_scoring import score_leads
from app.core.database import get_sessionmaker
from app.models import Lead
from app.schemas.lead_fit_score import LeadFitScoreResponse

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


def _stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"min": 0.0, "max": 0.0, "mean": 0.0}
    return {
        "min": round(min(values), 1),
        "max": round(max(values), 1),
        "mean": round(sum(values) / len(values), 1),
    }


def score_cohort(
    session_factory,
    *,
    dry_run: bool,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    limit: int | None = None,
    rescore_unchanged: bool = False,
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
    skipped_by_segment: Counter[str] = Counter()
    failed_by_segment: Counter[str] = Counter()
    band_counts: Counter[str] = Counter()
    band_counts_by_segment: dict[str, Counter[str]] = {}
    fit_histogram: Counter[int] = Counter()
    coverage_histogram: Counter[float] = Counter()
    fit_values: list[float] = []
    coverage_values: list[float] = []
    readiness_counts: Counter[str] = Counter()
    eligibility_counts: Counter[str] = Counter()
    errors: list[dict[str, str]] = []

    started = time.perf_counter()

    for start in range(0, len(lead_ids), chunk_size):
        chunk_ids = lead_ids[start : start + chunk_size]
        session = session_factory()
        try:
            leads = list(
                session.execute(select(Lead).where(Lead.id.in_(chunk_ids))).scalars()
            )
            chunk_ok: list[tuple[str, LeadFitScoreResponse]] = []
            for lead, response, error in score_leads(
                session,
                leads,
                persist=not dry_run,
                skip_unchanged=not rescore_unchanged,
            ):
                segment = _segment_of(lead)
                attempted_by_segment[segment] += 1
                if error is not None:
                    failed_by_segment[segment] += 1
                    errors.append({"lead_id": str(lead.id), "error": str(error)})
                elif response is None:
                    skipped_by_segment[segment] += 1
                else:
                    chunk_ok.append((segment, response))

            try:
                if dry_run:
                    session.rollback()
                else:
                    session.commit()
            except Exception as e:  # noqa: BLE001 -- chunk-level resilience
                session.rollback()
                for segment, response in chunk_ok:
                    failed_by_segment[segment] += 1
                    errors.append(
                        {"lead_id": str(response.lead_id), "error": f"chunk commit failed: {e}"}
                    )
                chunk_ok = []

            for segment, response in chunk_ok:
                succeeded_by_segment[segment] += 1
                band_counts[response.band] += 1
                band_counts_by_segment.setdefault(segment, Counter())[response.band] += 1
                fit_histogram[response.fit_score] += 1
                coverage_histogram[response.evidence_coverage_pct] += 1
                fit_values.append(float(response.fit_score))
                coverage_values.append(response.evidence_coverage_pct)
                for action_name in ("outbound_email", "internal_slack_handoff"):
                    action = getattr(response.readiness, action_name)
                    readiness_counts[f"{action_name}:status={action.status}"] += 1
                    for gap in action.gaps:
                        readiness_counts[f"{action_name}:gap={gap}"] += 1
                eligibility_counts[
                    "excluded" if response.eligibility.excluded else "not_excluded"
                ] += 1
                for reason in response.eligibility.reasons:
                    eligibility_counts[f"reason={reason}"] += 1
        finally:
            session.close()

    elapsed_s = round(time.perf_counter() - started, 3)

    return {
        "dry_run": dry_run,
        "chunk_size": chunk_size,
        "total_leads_considered": len(lead_ids),
        "attempted_by_segment": dict(attempted_by_segment),
        "succeeded_by_segment": dict(succeeded_by_segment),
        "skipped_unchanged_by_segment": dict(skipped_by_segment),
        "failed_by_segment": dict(failed_by_segment),
        "total_attempted": sum(attempted_by_segment.values()),
        "total_succeeded": sum(succeeded_by_segment.values()),
        "total_skipped_unchanged": sum(skipped_by_segment.values()),
        "total_failed": sum(failed_by_segment.values()),
        "band_counts": dict(band_counts),
        "band_counts_by_segment": {k: dict(v) for k, v in band_counts_by_segment.items()},
        "fit_score_histogram": {str(k): v for k, v in sorted(fit_histogram.items())},
        "fit_score_distribution": _stats(fit_values),
        "evidence_coverage_pct_histogram": {
            str(k): v for k, v in sorted(coverage_histogram.items())
        },
        "evidence_coverage_pct_distribution": _stats(coverage_values),
        "readiness_counts": dict(readiness_counts),
        "eligibility_counts": dict(eligibility_counts),
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
    parser.add_argument(
        "--rescore-unchanged",
        action="store_true",
        help="Write a new row even when the latest applicable row has the same input fingerprint.",
    )
    args = parser.parse_args(argv)

    summary = score_cohort(
        get_sessionmaker(),
        dry_run=args.dry_run,
        chunk_size=args.chunk_size,
        limit=args.limit,
        rescore_unchanged=args.rescore_unchanged,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["total_failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
