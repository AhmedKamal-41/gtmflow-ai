"""Verify an existing database's schema matches the 0001_baseline Alembic
revision BEFORE stamping it, so Alembic can then manage further migrations
without ever having run any DDL against this database itself.

This exists for exactly one situation: a database that was created by the
old, pre-Alembic path (``python -m app.core.init_db``, i.e.
``Base.metadata.create_all``) and now needs to be brought under Alembic
management without losing its data. The correct sequence is:

    1. python scripts/verify_baseline_schema.py         # check only
    2. python scripts/verify_baseline_schema.py --stamp  # check, then stamp
    3. alembic upgrade head                              # apply 0002+

Never run ``alembic stamp head`` directly on an existing database -- that
tells Alembic the database already has every migration applied, including
ones that were never actually run, silently skipping real schema changes
(the new provenance/review tables and columns) while leaving the database
believing it's up to date. This script refuses to stamp anything but
0001_baseline, and only after checking the schema actually matches it.

Exit codes: 0 = compatible (and stamped, if --stamp was passed); 1 =
incompatible schema (details printed, nothing stamped); 2 = already
Alembic-managed (nothing to do).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import inspect

# Run as `python scripts/verify_baseline_schema.py` from backend/: Python puts
# scripts/ (not backend/) on the import path, so add backend/ for `app`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import get_engine  # noqa: E402

# The exact table -> column set created by alembic/versions/0001_baseline.py.
# Deliberately hardcoded and independent of the current app/models/*.py --
# those now contain Phase 2 additions, and this script's whole job is to
# describe a fixed historical target, not "whatever the models say today."
EXPECTED_BASELINE_SCHEMA: dict[str, set[str]] = {
    "lead_batches": {
        "id", "name", "source", "total_leads", "processed_leads", "status",
        "created_at", "updated_at",
    },
    "leads": {
        "id", "batch_id", "company_name", "website", "industry",
        "contact_name", "contact_email", "contact_title", "company_size",
        "location", "source", "status", "cleaned_data", "created_at",
        "updated_at",
    },
    "lead_scores": {
        "id", "lead_id", "total_score", "priority", "score_breakdown",
        "reasoning", "created_at", "updated_at",
    },
    "ai_outputs": {
        "id", "lead_id", "output_type", "content", "model_used",
        "prompt_version", "created_at",
    },
    "integration_pushes": {
        "id", "lead_id", "integration_type", "payload", "status",
        "response_text", "created_at",
    },
    "workflow_events": {
        "id", "lead_id", "batch_id", "event_type", "event_data", "created_at",
    },
}


def _check(engine) -> tuple[bool, list[str]]:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    problems: list[str] = []

    if "alembic_version" in existing_tables:
        return False, ["already-managed"]

    expected_tables = set(EXPECTED_BASELINE_SCHEMA)
    missing_tables = expected_tables - existing_tables
    unexpected_tables = existing_tables - expected_tables
    if missing_tables:
        problems.append(f"missing table(s): {sorted(missing_tables)}")
    if unexpected_tables:
        problems.append(
            f"unexpected table(s) not in the baseline schema: {sorted(unexpected_tables)}"
        )

    for table in sorted(expected_tables & existing_tables):
        actual_columns = {c["name"] for c in inspector.get_columns(table)}
        expected_columns = EXPECTED_BASELINE_SCHEMA[table]
        missing_cols = expected_columns - actual_columns
        extra_cols = actual_columns - expected_columns
        if missing_cols:
            problems.append(f"table '{table}' missing column(s): {sorted(missing_cols)}")
        if extra_cols:
            problems.append(
                f"table '{table}' has unexpected column(s): {sorted(extra_cols)}"
            )

    return (len(problems) == 0), problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stamp",
        action="store_true",
        help="If the schema matches, run `alembic stamp 0001_baseline`.",
    )
    args = parser.parse_args()

    engine = get_engine()
    ok, problems = _check(engine)

    if not ok and problems == ["already-managed"]:
        print(
            "This database already has an alembic_version table -- it's "
            "already Alembic-managed. Nothing to verify or stamp. Run "
            "`alembic upgrade head` directly if you need to apply pending "
            "migrations."
        )
        return 2

    if not ok:
        print("Schema does NOT match the expected 0001_baseline revision:")
        for p in problems:
            print(f"  - {p}")
        print(
            "\nRefusing to stamp. This database's actual schema needs to be "
            "reconciled with 0001_baseline (or a new baseline written to "
            "match it) before it can be brought under Alembic management."
        )
        return 1

    print("Schema matches 0001_baseline exactly.")
    if args.stamp:
        from alembic import command
        from alembic.config import Config

        cfg = Config("alembic.ini")
        command.stamp(cfg, "0001_baseline")
        print(
            "Stamped as 0001_baseline. Run `alembic upgrade head` next to "
            "apply migrations from here (e.g. 0002_provenance)."
        )
    else:
        print("Re-run with --stamp to record this as 0001_baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
