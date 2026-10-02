"""Phase 3 PDL pipeline CLI. One entry point, five subcommands:

    python -m app.pdl.cli download   [--cache-dir DIR]
    python -m app.pdl.cli inspect    [--cache-dir DIR] [--limit N]
    python -m app.pdl.cli curate     [--cache-dir DIR] [--output-dir DIR] [--seed N] [--limit N] [--dry-run]
    python -m app.pdl.cli import     [--manifest PATH] [--dry-run]
    python -m app.pdl.cli report     [--output-dir DIR]

Run `python -m app.pdl.cli <subcommand> --help` for each command's options.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from app.pdl import config
from app.pdl.curate import curate as run_curate
from app.pdl.download import download_source
from app.pdl.importer import (
    compute_logical_key,
    find_or_create_batch,
    find_or_create_source_snapshot,
    import_companies,
)
from app.pdl.report import (
    write_canonical_csv,
    write_human_report,
    write_quality_report,
    write_spreadsheet_safe_csv,
)
from app.pdl.report import write_manifest as _write_manifest
from app.pdl.source_reader import SourceCorruptError, iter_source_records

DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "pdl_source"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "pdl_curated"
DOWNLOAD_URL = (
    "https://huggingface.co/datasets/andreaaltomani/company-dataset/"
    "resolve/main/free_company_dataset.json.gz?download=true"
)
SOURCE_FILENAME = "free_company_dataset.json.gz"


class ProvenanceError(RuntimeError):
    """Raised when source provenance is missing/empty/inconsistent -- this
    must be caught BEFORE any SourceSnapshot/LeadBatch/CompanyIdentity/Lead
    write happens, never after (Part B.1 of the Phase 3 closeout: a
    forgettable CLI flag is not sufficient enforcement, so provenance is
    derived automatically from a verified download and validated before any
    DB write is attempted)."""


def _source_snapshot_info(download_result) -> dict:
    """Built ONLY from an actual, freshly-verified DownloadResult -- never
    from a manually-supplied flag. `retrieved_at` is the real moment these
    bytes were downloaded (download_result.downloaded_at), distinct from
    `reported_acquisition_date` (PDL's own claimed scrape date, unverified)
    and from `curated_at` (set separately, when `curate` runs -- see
    cmd_curate)."""
    return {
        "provider": config.PROVIDER,
        "mirror_url": config.MIRROR_URL,
        "source_revision": config.SOURCE_REVISION,
        "reported_acquisition_date": config.REPORTED_ACQUISITION_DATE,
        "retrieved_at": download_result.downloaded_at,
        "retrieved_at_is_proxy": download_result.downloaded_at_is_proxy,
        "retrieved_license": config.RETRIEVED_LICENSE,
        "retrieved_attribution": config.RETRIEVED_ATTRIBUTION,
        "checksum": download_result.sha256,
        "checksum_algorithm": config.CHECKSUM_ALGORITHM,
        "parser_version": config.MAPPING_VERSION,
    }


def validate_snapshot_info(snapshot_info: dict) -> None:
    """Fail clearly, before any write, if essential provenance is missing,
    empty, or inconsistent. Called by `import` immediately after loading a
    manifest, and would also catch a hand-edited or corrupted manifest."""
    required = ["provider", "mirror_url", "checksum", "checksum_algorithm"]
    missing = [k for k in required if not snapshot_info.get(k)]
    if missing:
        raise ProvenanceError(
            f"Manifest is missing required source provenance field(s): {missing}. "
            "Refusing to create a SourceSnapshot, LeadBatch, CompanyIdentity, or "
            "Lead from unverified provenance. Re-run `curate` (which derives "
            "these automatically from a verified download) to produce a valid "
            "manifest."
        )
    if snapshot_info.get("checksum_algorithm") != config.CHECKSUM_ALGORITHM:
        raise ProvenanceError(
            f"Manifest checksum_algorithm={snapshot_info.get('checksum_algorithm')!r} "
            f"does not match the algorithm this pipeline verifies ({config.CHECKSUM_ALGORITHM!r})."
        )
    checksum = snapshot_info["checksum"]
    if not isinstance(checksum, str) or len(checksum) != 64:
        raise ProvenanceError(
            f"Manifest checksum {checksum!r} does not look like a valid "
            f"{config.CHECKSUM_ALGORITHM} hex digest (expected 64 hex characters)."
        )


def cmd_download(args: argparse.Namespace) -> int:
    cache_dir = Path(args.cache_dir)
    print(f"Downloading (or reusing) {SOURCE_FILENAME} into {cache_dir} ...")
    result = download_source(DOWNLOAD_URL, cache_dir, SOURCE_FILENAME)
    print(f"path={result.path}")
    print(f"size_bytes={result.size_bytes}")
    print(f"sha256={result.sha256}")
    print(f"reused_existing={result.reused_existing}")
    print(f"downloaded_at={result.downloaded_at} (proxy={result.downloaded_at_is_proxy})")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    path = Path(args.cache_dir) / SOURCE_FILENAME
    if not path.exists():
        print(f"Source file not found at {path}. Run `download` first.", file=sys.stderr)
        return 2

    from app.pdl.normalize import normalize_record

    industry_counts: dict[str, int] = {}
    country_counts: dict[str, int] = {}
    segment_counts: dict[str, int] = {}
    sample: list[dict] = []
    try:
        for i, raw in enumerate(iter_source_records(path, limit=args.limit)):
            if i < 5:
                sample.append(raw)
            normalized = normalize_record(raw)
            if normalized.raw_industry:
                industry_counts[normalized.raw_industry] = (
                    industry_counts.get(normalized.raw_industry, 0) + 1
                )
            if normalized.country_raw:
                country_counts[normalized.country_raw] = (
                    country_counts.get(normalized.country_raw, 0) + 1
                )
            if normalized.candidate_segment:
                segment_counts[normalized.candidate_segment] = (
                    segment_counts.get(normalized.candidate_segment, 0) + 1
                )
    except SourceCorruptError as e:
        print(f"CORRUPT SOURCE STREAM: {e}", file=sys.stderr)
        return 3

    print(f"limit={args.limit!r} (None means full file)")
    print("sample records:")
    for r in sample:
        print(" ", json.dumps(r, ensure_ascii=False))
    print(f"top industries seen (of {len(industry_counts)} distinct): ")
    for name, count in sorted(industry_counts.items(), key=lambda kv: -kv[1])[:10]:
        print(f"  {name}: {count}")
    print(f"segment matches this scan: {segment_counts}")
    return 0


def _run_curation(args: argparse.Namespace):
    path = Path(args.cache_dir) / SOURCE_FILENAME
    if not path.exists():
        print(f"Source file not found at {path}. Run `download` first.", file=sys.stderr)
        return None
    print(
        f"Curating from {path} (seed={args.seed}, limit={args.limit!r}) -- "
        "this is a full streaming pass; a --limit run is a smoke test only, "
        "not a representative selection."
    )
    try:
        result = run_curate(path, seed=args.seed, limit=args.limit)
    except SourceCorruptError as e:
        print(f"CORRUPT SOURCE STREAM: {e}", file=sys.stderr)
        return None
    return result


def cmd_curate(args: argparse.Namespace) -> int:
    result = _run_curation(args)
    if result is None:
        return 3

    print(f"reached_eof={result.reached_eof} is_representative={result.is_representative}")
    print(f"lines_read={result.scan_stats.lines_read} records_parsed={result.scan_stats.records_parsed}")
    for segment, companies in result.selected_by_segment.items():
        target = config.TARGET_PER_SEGMENT[segment]
        eligible = result.eligible_unique_by_segment[segment]
        shortfall = max(0, target - len(companies))
        print(
            f"{segment}: selected={len(companies)} target={target} "
            f"eligible_unique={eligible} shortfall={shortfall}"
        )
        if shortfall:
            print(
                f"  SHORTFALL: only {eligible} eligible unique {segment} records exist "
                f"in this scan; cannot reach {target} without broadening the fixed "
                "selection rules, which this pipeline will not do silently."
            )

    if args.dry_run:
        print("--dry-run: not writing artifacts.")
        return 0

    # Checksum is ALWAYS derived from an actual, freshly-verified download
    # (re-hashing the cached file, not trusting a stale value) -- never
    # solely from an optional CLI flag. This is the Part B.1 fix: the
    # original --source-checksum flag was easy to forget, and forgetting it
    # silently created a duplicate SourceSnapshot on every import attempt
    # (see docs/engineering-log/decisions.md's Phase 3 closeout section). The flag
    # still exists, but only as an optional cross-check assertion now.
    cache_dir = Path(args.cache_dir)
    download_result = download_source(DOWNLOAD_URL, cache_dir, SOURCE_FILENAME)
    if args.source_checksum and args.source_checksum != download_result.sha256:
        print(
            f"--source-checksum {args.source_checksum} does not match the "
            f"cached file's actual sha256 {download_result.sha256}. Refusing "
            "to proceed with mismatched provenance.",
            file=sys.stderr,
        )
        return 4

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_info = _source_snapshot_info(download_result)
    snapshot_info["curated_at"] = datetime.now(timezone.utc).isoformat()

    csv_path = output_dir / "curated_companies.csv"
    safe_csv_path = output_dir / "curated_companies.spreadsheet_safe.csv"
    manifest_path = output_dir / "selection_manifest.json"
    quality_path = output_dir / "quality_report.json"
    human_path = output_dir / "data_report.md"

    row_count = write_canonical_csv(csv_path, result, {})
    write_spreadsheet_safe_csv(csv_path, safe_csv_path)
    _write_manifest(manifest_path, result, snapshot_info)
    quality = write_quality_report(quality_path, result)
    write_human_report(human_path, quality, snapshot_info)

    print(f"wrote {row_count} rows to {csv_path}")
    print(f"wrote {manifest_path}, {quality_path}, {human_path}, {safe_csv_path}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    from app.core.database import get_engine, get_sessionmaker
    from app.models import ImportRun, LeadBatch

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"Manifest not found at {manifest_path}. Run `curate` first.", file=sys.stderr)
        return 2

    from app.pdl.report import load_companies_from_manifest

    companies_by_segment, snapshot_info = load_companies_from_manifest(manifest_path)
    total = sum(len(v) for v in companies_by_segment.values())
    print(f"loaded {total} companies from manifest across {list(companies_by_segment)}")

    # Fail clearly, before any write, if provenance is missing/empty/
    # inconsistent (Part B.1) -- never proceed on the hope that a flag was
    # remembered somewhere upstream.
    try:
        validate_snapshot_info(snapshot_info)
    except ProvenanceError as e:
        print(f"PROVENANCE VALIDATION FAILED: {e}", file=sys.stderr)
        return 5

    if args.dry_run:
        print("--dry-run: not writing to the database.")
        return 0

    logical_key = compute_logical_key(
        source_checksum=snapshot_info["checksum"],
        mapping_version=config.MAPPING_VERSION,
        target_per_segment=config.TARGET_PER_SEGMENT,
        seed=args.seed,
        country_filter="united states",
    )

    session_factory = get_sessionmaker()
    session = session_factory()
    try:
        source_snapshot, snap_created = find_or_create_source_snapshot(
            session,
            provider=snapshot_info["provider"],
            mirror_url=snapshot_info["mirror_url"],
            source_revision=snapshot_info["source_revision"],
            reported_acquisition_date=(
                date.fromisoformat(snapshot_info["reported_acquisition_date"])
                if snapshot_info.get("reported_acquisition_date")
                else None
            ),
            retrieved_license=snapshot_info.get("retrieved_license"),
            retrieved_attribution=snapshot_info.get("retrieved_attribution"),
            checksum=snapshot_info["checksum"],
            checksum_algorithm=snapshot_info["checksum_algorithm"],
            parser_version=snapshot_info.get("parser_version"),
        )
        # Commit now, before find_or_create_batch's own race-retry logic can
        # possibly roll back this same transaction -- otherwise a batch-name
        # race would silently undo an already-flushed, uncommitted snapshot
        # sharing this session (see importer.py's docstrings).
        session.commit()
        session.refresh(source_snapshot)
        print(f"source_snapshot_id={source_snapshot.id} created={snap_created}")

        batch, batch_created = find_or_create_batch(session, logical_key=logical_key)
        session.commit()
        session.refresh(batch)
        print(f"batch_id={batch.id} name={batch.name!r} logical_key={logical_key} created={batch_created}")

        import_run = ImportRun(
            source_snapshot_id=source_snapshot.id,
            config_seed={
                "seed": args.seed,
                "target_per_segment": config.TARGET_PER_SEGMENT,
                "mapping_version": config.MAPPING_VERSION,
                "country_filter": "united states",
            },
            logical_key=logical_key,
            status="running",
            total_records=total,
            started_at=datetime.now(timezone.utc),
        )
        session.add(import_run)
        session.commit()
        session.refresh(import_run)
        # Capture plain ids before the session closes -- import_companies
        # opens its own per-chunk sessions and must not touch objects bound
        # to this one (see importer.py's docstring on why it takes ids).
        source_snapshot_id = source_snapshot.id
        batch_id = batch.id
        import_run_id = import_run.id
        print(f"import_run_id={import_run_id} logical_key={logical_key}")
    finally:
        session.close()

    summary = import_companies(
        session_factory,
        companies_by_segment=companies_by_segment,
        source_snapshot_id=source_snapshot_id,
        batch_id=batch_id,
        import_run_id=import_run_id,
    )

    session = session_factory()
    try:
        run = session.get(ImportRun, import_run_id)
        run.imported_count = summary.inserted
        run.skipped_count = summary.reused
        run.error_count = summary.failed
        run.completed_at = datetime.now(timezone.utc)
        run.status = "completed" if summary.all_chunks_committed else "partial"
        if not summary.all_chunks_committed:
            run.error_summary = [
                {"chunk_index": c.chunk_index, "error": c.error}
                for c in summary.chunks
                if not c.committed
            ][: config.MAX_ERROR_EXAMPLES]

        # Keep LeadBatch's denormalized counters accurate (Part E.7: server
        # computes aggregate counts across the actual dataset) -- a plain
        # find_or_create_batch never touches these, so without this they'd
        # sit at their model default (0) forever, same bug class as the
        # unbounded-list issue this phase is fixing elsewhere.
        from sqlalchemy import func, select as sa_select

        from app.models import Lead as LeadModel

        batch_row = session.get(LeadBatch, batch_id)
        lead_count = session.scalar(
            sa_select(func.count()).select_from(LeadModel).where(LeadModel.batch_id == batch_id)
        ) or 0
        batch_row.total_leads = lead_count
        batch_row.processed_leads = lead_count
        session.commit()
        final_status = run.status
    finally:
        session.close()

    print(f"import_run status={final_status}")
    print(f"inserted={summary.inserted} reused={summary.reused} failed={summary.failed}")
    print(f"chunks={len(summary.chunks)} all_committed={summary.all_chunks_committed}")
    if not summary.all_chunks_committed:
        print("PARTIAL IMPORT -- see import_run.error_summary. Re-run `import` with the", file=sys.stderr)
        print("same manifest to resume; already-committed rows are reused, not duplicated.", file=sys.stderr)
        return 1
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Reprint the human-readable report from already-written artifacts --
    no recomputation, just surfaces what `curate` already produced."""
    output_dir = Path(args.output_dir)
    human_path = output_dir / "data_report.md"
    if not human_path.exists():
        print(
            f"No report found at {human_path}. Run `curate` first.", file=sys.stderr
        )
        return 2
    print(human_path.read_text(encoding="utf-8"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.pdl.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_download = sub.add_parser("download", help="download (or reuse cached) source file")
    p_download.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    p_download.set_defaults(func=cmd_download)

    p_inspect = sub.add_parser("inspect", help="peek at the source file's shape")
    p_inspect.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    p_inspect.add_argument("--limit", type=int, default=10000)
    p_inspect.set_defaults(func=cmd_inspect)

    p_curate = sub.add_parser("curate", help="stream, filter, dedup, sample, write artifacts")
    p_curate.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    p_curate.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    p_curate.add_argument("--seed", type=int, default=20260922)
    p_curate.add_argument("--limit", type=int, default=None, help="smoke-test only; caps lines read")
    p_curate.add_argument(
        "--source-checksum",
        default=None,
        help=(
            "Optional cross-check only. The real checksum is always derived "
            "from re-hashing the actual cached/downloaded file; if this is "
            "given and doesn't match, curate refuses to proceed."
        ),
    )
    p_curate.add_argument("--dry-run", action="store_true")
    p_curate.set_defaults(func=cmd_curate)

    p_import = sub.add_parser("import", help="load a curate-produced manifest into the database")
    p_import.add_argument(
        "--manifest", default=str(DEFAULT_OUTPUT_DIR / "selection_manifest.json")
    )
    p_import.add_argument("--seed", type=int, default=20260922, help="must match the curate run's seed")
    p_import.add_argument("--dry-run", action="store_true")
    p_import.set_defaults(func=cmd_import)

    p_report = sub.add_parser("report", help="reprint the human-readable report from prior artifacts")
    p_report.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    p_report.set_defaults(func=cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
