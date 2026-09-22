"""Phase 3 artifact writers: canonical CSV, provenance/selection manifest,
machine-readable quality report, human-readable data report.

Canonical CSV columns match app/services/csv_ingestion.py's KNOWN_COLUMNS
exactly, so this file is genuinely upload-compatible with the existing web
path (contact_* columns are always empty -- PDL has no contact data, and
nothing here invents any). Extra PDL-specific columns (source_record_id,
candidate_segment, locality, region, country, founded, linkedin_url) are
NOT in KNOWN_COLUMNS, so the existing ingestion code already preserves them
into `cleaned_data` on upload, with zero special-casing needed.
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.pdl.curate import CurationResult
from app.pdl.normalize import NormalizedCompany

CSV_FIELDNAMES = [
    "company_name",
    "website",
    "industry",
    "contact_name",
    "contact_email",
    "contact_title",
    "company_size",
    "location",
    "source",
    "status",
    "source_record_id",
    "identity_confidence",
    "candidate_segment",
    "locality",
    "region",
    "country",
    "founded",
    "linkedin_url",
]

_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _row_for(company: NormalizedCompany, segment: str, confidence: str) -> dict[str, Any]:
    return {
        "company_name": company.company_name,
        "website": company.website or "",
        "industry": company.raw_industry or "",
        "contact_name": "",
        "contact_email": "",
        "contact_title": "",
        "company_size": company.company_size or "",
        "location": company.location or "",
        "source": "pdl_import",
        "status": "",
        "source_record_id": company.source_record_id or "",
        "identity_confidence": confidence,
        "candidate_segment": segment,
        "locality": company.locality or "",
        "region": company.region or "",
        "country": company.country_raw or "",
        "founded": company.founded if company.founded is not None else "",
        "linkedin_url": company.linkedin_url or "",
    }


def _escape_formula(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def write_canonical_csv(
    path: Path, result: CurationResult, identity_confidence_by_key: dict[str, str]
) -> int:
    """The interchange copy -- values unescaped, exactly what came out of
    normalization. This is what gets imported; never run formula escaping
    on this file."""
    from app.pdl.identity import compute_identity

    row_count = 0
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for segment, companies in result.selected_by_segment.items():
            for company in companies:
                identity = compute_identity(company)
                writer.writerow(_row_for(company, segment, identity.confidence))
                row_count += 1
    return row_count


def write_spreadsheet_safe_csv(canonical_path: Path, safe_path: Path) -> None:
    """Human-review copy ONLY -- formula-injection-escaped (leading
    =/+/-/@ prefixed with a quote) so opening it in Excel/Sheets can't
    execute anything. Never re-import this file; re-import the canonical
    one, which is left untouched by this function."""
    with canonical_path.open("r", newline="", encoding="utf-8") as src, safe_path.open(
        "w", newline="", encoding="utf-8"
    ) as dst:
        reader = csv.DictReader(src)
        writer = csv.DictWriter(dst, fieldnames=reader.fieldnames or CSV_FIELDNAMES)
        writer.writeheader()
        for row in reader:
            writer.writerow({k: _escape_formula(v) for k, v in row.items()})


def write_manifest(path: Path, result: CurationResult, snapshot_info: dict[str, Any]) -> None:
    """Full-fidelity record of the selection: identity + content hash (for
    traceability) AND every normalized field plus the original raw source
    dict (so `import` can load from this file alone, without a second
    32M-row scan of the source, and without losing any provenance detail
    the lean canonical CSV doesn't carry, e.g. the raw pre-normalization
    values)."""
    from app.pdl.identity import compute_content_hash, compute_identity

    rows = []
    for segment, companies in result.selected_by_segment.items():
        for company in companies:
            identity = compute_identity(company)
            rows.append(
                {
                    "segment": segment,
                    "identity_key": identity.key,
                    "identity_confidence": identity.confidence,
                    "content_hash": compute_content_hash(company),
                    "company_name": company.company_name,
                    "website": company.website,
                    "domain": company.domain,
                    "raw_industry": company.raw_industry,
                    "company_size": company.company_size,
                    "location": company.location,
                    "locality": company.locality,
                    "region": company.region,
                    "country_raw": company.country_raw,
                    "source_record_id": company.source_record_id,
                    "founded": company.founded,
                    "linkedin_url": company.linkedin_url,
                    "source_raw_data": company.source_raw_data,
                }
            )
    manifest = {
        "source_snapshot": snapshot_info,
        "seed": result.seed,
        "reached_eof": result.reached_eof,
        "is_representative": result.is_representative,
        "row_count": len(rows),
        "rows": rows,
    }
    path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")


def load_companies_from_manifest(path: Path) -> tuple[dict[str, list[NormalizedCompany]], dict[str, Any]]:
    """Reconstruct NormalizedCompany objects from a manifest written by
    write_manifest, grouped by segment -- what `import` reads instead of
    re-scanning the source file."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    by_segment: dict[str, list[NormalizedCompany]] = {}
    for row in payload["rows"]:
        company = NormalizedCompany(
            company_name=row["company_name"],
            website=row["website"],
            domain=row["domain"],
            raw_industry=row["raw_industry"],
            normalized_industry=row["segment"],
            candidate_segment=row["segment"],
            company_size=row["company_size"],
            location=row["location"],
            locality=row["locality"],
            region=row["region"],
            country_raw=row["country_raw"],
            country_normalized="united states",
            source_record_id=row["source_record_id"],
            founded=row["founded"],
            linkedin_url=row["linkedin_url"],
            source_raw_data=row["source_raw_data"],
        )
        by_segment.setdefault(row["segment"], []).append(company)
    return by_segment, payload["source_snapshot"]


def write_quality_report(path: Path, result: CurationResult) -> dict[str, Any]:
    scan = result.scan_stats
    selected_counts = {seg: len(items) for seg, items in result.selected_by_segment.items()}

    def _rate(numerator: int, denominator: int) -> float:
        return round((numerator / denominator) * 100, 2) if denominator else 0.0

    missing_website: dict[str, Any] = {}
    missing_size: dict[str, Any] = {}
    for segment, companies in result.selected_by_segment.items():
        n = len(companies)
        missing_website[segment] = {
            "count": sum(1 for c in companies if not c.website),
            "rate_pct": _rate(sum(1 for c in companies if not c.website), n),
        }
        missing_size[segment] = {
            "count": sum(1 for c in companies if not c.company_size),
            "rate_pct": _rate(sum(1 for c in companies if not c.company_size), n),
        }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scan": {
            "lines_read": scan.lines_read,
            "records_parsed": scan.records_parsed,
            "rows_too_large": scan.rows_too_large,
            "json_errors": scan.json_errors,
            "reached_eof": scan.reached_eof,
            "error_examples": [asdict(e) for e in scan.error_examples],
        },
        "eligible_unique_by_segment": result.eligible_unique_by_segment,
        "selected_count_by_segment": selected_counts,
        "target_per_segment": {
            seg: len(companies) for seg, companies in result.selected_by_segment.items()
        },
        "exact_duplicate_count_by_segment": result.exact_duplicate_count_by_segment,
        "conflict_count_by_segment": result.conflict_count_by_segment,
        "conflict_examples": [asdict(c) for c in result.conflict_examples],
        "fingerprint_identity_count_by_segment": result.fingerprint_identity_count_by_segment,
        "missing_website_rate": missing_website,
        "missing_company_size_rate": missing_size,
        "is_representative": result.is_representative,
        "seed": result.seed,
    }
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def write_human_report(path: Path, quality: dict[str, Any], snapshot_info: dict[str, Any]) -> None:
    lines = [
        "# GTMFlow Phase 3 PDL curation report",
        "",
        f"Generated: {quality['generated_at']}",
        "",
        "## Source",
        f"- Provider: {snapshot_info.get('provider')}",
        f"- Mirror URL: {snapshot_info.get('mirror_url')}",
        f"- Pinned revision: {snapshot_info.get('source_revision')}",
        f"- Reported acquisition date: {snapshot_info.get('reported_acquisition_date')} (per uploader, not independently verified)",
        f"- Retrieved: {snapshot_info.get('retrieved_at')}",
        f"- License / attribution: {snapshot_info.get('retrieved_license')} / {snapshot_info.get('retrieved_attribution')}",
        f"- Checksum ({snapshot_info.get('checksum_algorithm')}): {snapshot_info.get('checksum')}",
        "",
        "## Scan",
        f"- Lines read: {quality['scan']['lines_read']:,}",
        f"- Records parsed: {quality['scan']['records_parsed']:,}",
        f"- Reached end-of-file: {quality['scan']['reached_eof']}",
        f"- Malformed/oversized rows skipped: {quality['scan']['json_errors'] + quality['scan']['rows_too_large']:,}",
        "",
        "## Selection",
        f"- Representative (full-corpus, seeded reservoir sample): {quality['is_representative']}",
        f"- Seed: {quality['seed']}",
    ]
    for segment, count in quality["selected_count_by_segment"].items():
        eligible = quality["eligible_unique_by_segment"].get(segment, 0)
        target = quality["target_per_segment"].get(segment, 0)
        shortfall = max(0, target - count)
        lines.append(
            f"- {segment}: selected {count:,} / target {target:,} "
            f"(eligible unique pool: {eligible:,}, shortfall: {shortfall:,})"
        )
    lines += [
        "",
        "## Data quality in the selected sample",
    ]
    for segment in quality["selected_count_by_segment"]:
        mw = quality["missing_website_rate"].get(segment, {})
        ms = quality["missing_company_size_rate"].get(segment, {})
        lines.append(
            f"- {segment}: missing website {mw.get('count', 0):,} "
            f"({mw.get('rate_pct', 0)}%), missing company_size "
            f"{ms.get('count', 0):,} ({ms.get('rate_pct', 0)}%)"
        )
    lines += [
        "",
        "## Identity and conflicts",
    ]
    for segment in quality["selected_count_by_segment"]:
        fp = quality["fingerprint_identity_count_by_segment"].get(segment, 0)
        dup = quality["exact_duplicate_count_by_segment"].get(segment, 0)
        conf = quality["conflict_count_by_segment"].get(segment, 0)
        lines.append(
            f"- {segment}: {fp:,} selected via fingerprint identity (no source id), "
            f"{dup:,} exact duplicates skipped, {conf:,} identity conflicts "
            "(same identity, differing content -- first-seen kept)"
        )
    lines += [
        "",
        "## What this is NOT",
        "- No contact names, emails, titles, buying intent, or verified pain points -- the source doesn't have them, and none were invented.",
        "- Segment membership (healthcare / real_estate) is a candidate classification from the source's own industry field, not a sales-qualification claim. Real estate does not prove property management; healthcare does not prove a clinic.",
        "- No AI drafts were generated and no Slack messages were sent for this data in this phase.",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
