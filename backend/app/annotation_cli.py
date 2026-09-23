"""Phase 6 command line: freeze/verify the split manifest, create the pilot
queue, summarize it, and export human-reviewed examples.

    DATABASE_URL=... python -m app.annotation_cli freeze-manifest [--write-file PATH]
    DATABASE_URL=... python -m app.annotation_cli verify-manifest
    DATABASE_URL=... python -m app.annotation_cli create-pilot
    DATABASE_URL=... python -m app.annotation_cli summary
    DATABASE_URL=... python -m app.annotation_cli export --out PATH

Nothing here generates model output or records a review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from app.core.database import get_sessionmaker
from app.services import splits
from app.services.annotation import export_rows, queue_summary


def _write_jsonl(path: Path, rows: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, sort_keys=True, default=str) + "\n" for row in rows)
    path.write_text(text)
    return hashlib.sha256(text.encode()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.annotation_cli")
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze-manifest")
    freeze.add_argument("--version", default=splits.MANIFEST_VERSION)
    freeze.add_argument("--write-file", type=Path)
    verify = sub.add_parser("verify-manifest")
    verify.add_argument("--version", default=splits.MANIFEST_VERSION)
    pilot = sub.add_parser("create-pilot")
    pilot.add_argument("--version", default=splits.MANIFEST_VERSION)
    pilot.add_argument("--queue", default=splits.PILOT_QUEUE)
    summary = sub.add_parser("summary")
    summary.add_argument("--queue", default=splits.PILOT_QUEUE)
    export = sub.add_parser("export")
    export.add_argument("--queue", default=None)
    export.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    session = get_sessionmaker()()
    try:
        if args.command == "freeze-manifest":
            manifest = splits.freeze_manifest(session, args.version)
            session.commit()
            result = {"manifest_version": manifest.version, "seed": manifest.seed,
                      "algorithm": manifest.algorithm, "lead_count": manifest.lead_count,
                      "group_count": manifest.group_count, "counts": manifest.counts,
                      "manifest_sha256": manifest.manifest_sha256}
            if args.write_file:
                result["file"] = str(args.write_file)
                result["file_sha256"] = _write_jsonl(args.write_file, splits.assignment_rows_for_export(session, manifest.version))
        elif args.command == "verify-manifest":
            result = splits.verify_manifest(session, args.version)
        elif args.command == "create-pilot":
            rows = splits.create_pilot_queue(session, args.version, args.queue)
            session.commit()
            result = queue_summary(session, args.queue)
            result["created"] = len(rows)
        elif args.command == "summary":
            result = queue_summary(session, args.queue)
        else:
            rows = export_rows(session, args.queue)
            result = {"examples": len(rows), "unique_companies": len({r["lead_id"] for r in rows}),
                      "out": str(args.out), "sha256": _write_jsonl(args.out, rows)}
    except splits.ManifestError as error:
        session.rollback()
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2
    finally:
        session.close()
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    if args.command == "verify-manifest" and not result["matches"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
