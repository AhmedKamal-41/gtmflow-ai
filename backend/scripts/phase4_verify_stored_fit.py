"""Phase 4: read-only check that every lead's CURRENT stored v2 fit row
(app/services/fit_queries.py's definition) equals what the scorer computes
right now from the lead's current inputs -- fingerprint, fit score, band,
evidence coverage, and every criterion's result/points. Writes nothing.

Prints a JSON summary; exit 0 only if every lead has a current row and
every one of them matches.

Usage:
    DATABASE_URL=... python scripts/phase4_verify_stored_fit.py [--chunk-size N]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from sqlalchemy import select

from app.core.database import get_sessionmaker
from app.models import Lead
from app.scoring import fit
from app.services.fit_queries import latest_fit_scores_by_lead


def _criteria_key(criteria: list[dict]) -> list[tuple]:
    return [(c["name"], c["result"], c["points"], c["weight"], c["active"]) for c in criteria]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunk-size", type=int, default=500)
    args = parser.parse_args(argv)

    session = get_sessionmaker()()
    try:
        lead_ids = list(session.execute(select(Lead.id).order_by(Lead.id)).scalars())
        missing = 0
        mismatches: Counter[str] = Counter()
        examples: list[dict] = []
        for start in range(0, len(lead_ids), args.chunk_size):
            chunk = lead_ids[start : start + args.chunk_size]
            leads = list(session.execute(select(Lead).where(Lead.id.in_(chunk))).scalars())
            stored = latest_fit_scores_by_lead(session, chunk)
            for lead in leads:
                row = stored.get(lead.id)
                if row is None:
                    missing += 1
                    continue
                result = fit.compute_fit(lead)
                checks = {
                    "input_fingerprint": row.input_fingerprint == result.input_fingerprint,
                    "fit_score": row.fit_score == result.fit_score,
                    "band": row.band == result.band,
                    "evidence_coverage_pct": row.evidence_coverage_pct == result.evidence_coverage_pct,
                    "criteria": _criteria_key(row.criteria) == _criteria_key(result.criteria_as_dicts()),
                }
                for name, ok in checks.items():
                    if not ok:
                        mismatches[name] += 1
                if not all(checks.values()) and len(examples) < 5:
                    examples.append({"lead_id": str(lead.id), "failed": [k for k, v in checks.items() if not v]})
            session.expire_all()
    finally:
        session.close()

    summary = {
        "leads_checked": len(lead_ids),
        "leads_without_current_row": missing,
        "field_mismatch_counts": dict(mismatches),
        "examples": examples,
        "all_match": missing == 0 and not mismatches,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["all_match"] else 1


if __name__ == "__main__":
    sys.exit(main())
