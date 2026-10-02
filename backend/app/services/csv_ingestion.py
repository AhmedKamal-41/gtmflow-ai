"""Pure CSV parsing + per-row validation. No database side effects.

`iter_cleaned_leads` is the true streaming entry point: it reads directly
from a text-mode file object via `csv.reader` (which itself streams lines,
never materializing the whole file) and yields one `CleanedLead` at a time,
so a caller can insert+commit in bounded chunks as rows are parsed, instead
of first collecting every row into a list (Part E.1/E.3 of the Phase 3
closeout -- see app/api/batches.py's `upload_batch`, which drives this
generator directly against a temp file rather than an in-memory string).

`parse_csv` still exists, unchanged in signature and return shape, as a
thin wrapper for callers that want the whole result materialized (existing
tests, and any future caller that doesn't need bounded writes).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Any, Iterator, TextIO

KNOWN_COLUMNS: set[str] = {
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
}
REQUIRED_COLUMN = "company_name"

# Part E defaults (docs/engineering-log's Phase 3 handoff). Enforced here (row
# count, while parsing) and in the API layer (byte size, while reading the
# upload) -- neither limit is trusted from a client-supplied header alone.
DEFAULT_MAX_ROWS = 50_000
DEFAULT_MAX_ERROR_EXAMPLES = 50


class CSVValidationError(Exception):
    """Whole-file problem: empty file, missing header, or missing required column."""

    def __init__(self, message: str, field_name: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.field_name = field_name


@dataclass
class RowError:
    row_number: int
    field: str
    message: str


@dataclass
class CleanedLead:
    company_name: str
    website: str | None = None
    industry: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    contact_title: str | None = None
    company_size: str | None = None
    location: str | None = None
    source: str | None = None
    status: str | None = None
    cleaned_data: dict[str, Any] | None = None


@dataclass
class IngestionResult:
    total_rows: int
    valid_leads: list[CleanedLead] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    # Total invalid-row count, independent of how many RowError *examples*
    # were kept in `errors` (bounded by max_error_examples) -- a file with
    # thousands of bad rows still reports an accurate total, not just the
    # size of the truncated example list.
    total_error_count: int = 0


@dataclass
class StreamParseStats:
    """Mutated in place by `iter_cleaned_leads` as it streams -- read this
    after fully consuming the generator (or after it raises) to get
    row/error counts without needing the generator to also "return" them."""

    total_rows: int = 0
    total_error_count: int = 0
    error_examples: list[RowError] = field(default_factory=list)


def _normalize_header(name: str) -> str:
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def _normalize_cell(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _is_blank_row(row: list[str]) -> bool:
    return not row or all(not (cell or "").strip() for cell in row)


def iter_cleaned_leads(
    file_obj: TextIO,
    *,
    max_rows: int | None = DEFAULT_MAX_ROWS,
    max_error_examples: int = DEFAULT_MAX_ERROR_EXAMPLES,
    stats: StreamParseStats | None = None,
) -> Iterator[CleanedLead]:
    """True streaming parse: reads directly from `file_obj` (text mode) via
    `csv.reader`, which streams lines itself -- never materializes the
    whole file or the whole row list. Yields one valid `CleanedLead` at a
    time; invalid rows are counted into `stats` and skipped, not yielded.

    Raises immediately (before yielding anything) for an empty file, blank
    header, or missing required column; raises mid-stream if `max_rows` is
    exceeded -- in both cases the caller sees a clean `CSVValidationError`,
    never a silently truncated result.
    """
    if stats is None:
        stats = StreamParseStats()

    reader = csv.reader(file_obj)
    try:
        header_row = next(reader)
    except StopIteration:
        raise CSVValidationError("CSV file is empty.") from None
    if _is_blank_row(header_row):
        raise CSVValidationError("CSV file is empty.")

    header = [_normalize_header(c) for c in header_row]
    if REQUIRED_COLUMN not in header:
        raise CSVValidationError(
            f"Required column '{REQUIRED_COLUMN}' is missing.",
            field_name=REQUIRED_COLUMN,
        )

    for offset, row in enumerate(reader):
        row_number = offset + 2  # header was line 1
        if _is_blank_row(row):
            continue
        stats.total_rows += 1
        if max_rows is not None and stats.total_rows > max_rows:
            raise CSVValidationError(
                f"CSV has more than {max_rows} data rows. Split it into "
                "smaller files, or use the PDL curation CLI for bulk "
                "imports (see backend/app/pdl/cli.py)."
            )

        cells = list(row) + [""] * (len(header) - len(row))
        mapped = dict(zip(header, cells))
        cleaned = {k: _normalize_cell(v) for k, v in mapped.items()}

        company_name = cleaned.get(REQUIRED_COLUMN)
        if not company_name:
            stats.total_error_count += 1
            if len(stats.error_examples) < max_error_examples:
                stats.error_examples.append(
                    RowError(
                        row_number=row_number,
                        field=REQUIRED_COLUMN,
                        message="company_name is required",
                    )
                )
            continue

        extras = {
            k: v
            for k, v in cleaned.items()
            if k not in KNOWN_COLUMNS and v is not None
        }
        cleaned_data: dict[str, Any] | None = extras or None

        yield CleanedLead(
            company_name=company_name,
            website=cleaned.get("website"),
            industry=cleaned.get("industry"),
            contact_name=cleaned.get("contact_name"),
            contact_email=cleaned.get("contact_email"),
            contact_title=cleaned.get("contact_title"),
            company_size=cleaned.get("company_size"),
            location=cleaned.get("location"),
            source=cleaned.get("source"),
            status=cleaned.get("status"),
            cleaned_data=cleaned_data,
        )


def parse_csv(
    raw_text: str,
    *,
    max_rows: int | None = DEFAULT_MAX_ROWS,
    max_error_examples: int = DEFAULT_MAX_ERROR_EXAMPLES,
) -> IngestionResult:
    """Whole-result convenience wrapper around `iter_cleaned_leads`, for
    callers that want everything materialized at once (existing tests; any
    caller not doing bounded/chunked writes). Same signature and return
    shape as before this function was reimplemented on top of the streaming
    generator.

    Raises:
        CSVValidationError: empty file, blank header, missing required
            column, or more data rows than `max_rows`.
    """
    stats = StreamParseStats()
    valid = list(
        iter_cleaned_leads(
            io.StringIO(raw_text),
            max_rows=max_rows,
            max_error_examples=max_error_examples,
            stats=stats,
        )
    )
    return IngestionResult(
        total_rows=stats.total_rows,
        valid_leads=valid,
        errors=stats.error_examples,
        total_error_count=stats.total_error_count,
    )
