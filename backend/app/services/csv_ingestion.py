"""Pure CSV parsing + per-row validation. No database side effects."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Any

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


def _normalize_header(name: str) -> str:
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def _normalize_cell(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _is_blank_row(row: list[str]) -> bool:
    return not row or all(not (cell or "").strip() for cell in row)


def parse_csv(raw_text: str) -> IngestionResult:
    """Parse a CSV string into cleaned leads + per-row errors.

    Raises:
        CSVValidationError: empty file, blank header, or missing required column.
    """
    reader = csv.reader(io.StringIO(raw_text))
    rows = list(reader)
    if not rows or _is_blank_row(rows[0]):
        raise CSVValidationError("CSV file is empty.")

    header = [_normalize_header(c) for c in rows[0]]
    if REQUIRED_COLUMN not in header:
        raise CSVValidationError(
            f"Required column '{REQUIRED_COLUMN}' is missing.",
            field_name=REQUIRED_COLUMN,
        )

    valid: list[CleanedLead] = []
    errors: list[RowError] = []
    total_data_rows = 0

    for offset, row in enumerate(rows[1:]):
        row_number = offset + 2  # header was line 1
        if _is_blank_row(row):
            continue
        total_data_rows += 1

        cells = list(row) + [""] * (len(header) - len(row))
        mapped = dict(zip(header, cells))
        cleaned = {k: _normalize_cell(v) for k, v in mapped.items()}

        company_name = cleaned.get(REQUIRED_COLUMN)
        if not company_name:
            errors.append(
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

        valid.append(
            CleanedLead(
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
        )

    return IngestionResult(
        total_rows=total_data_rows,
        valid_leads=valid,
        errors=errors,
    )
