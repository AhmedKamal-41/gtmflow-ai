"""Phase 3 PDL curation config: target counts, and explicit, versioned
industry/country alias mappings.

The alias maps below are grounded in an actual full-corpus scan of the
downloaded source file (2,378,824,357 bytes, sha256
2d529f107b1b592d942c01dfbf696061fc33e646fcbfe92db547ae930706cfc2), not
guessed:

  - `zcat ... | grep -oE '"industry": *"[^"]*(health|medical|hospital|clinic|
    real estate|realty|property)[^"]*"' | sort -u` found exactly 8 distinct
    values: "commercial real estate", "health, wellness and fitness",
    "hospital & health care", "hospitality", "medical devices",
    "medical practice", "mental health care", "real estate". Per the fixed
    selection rules, only "hospital & health care" and "medical practice"
    map to the healthcare segment, and only "real estate" maps to the
    real-estate segment -- the other five are deliberately excluded (they
    are adjacent but not the specified categories; including them would be
    exactly the kind of silent scope-broadening the brief prohibits).
    Notably, "hospitality" would have been a false positive under simple
    substring matching (it starts with "hospital") -- this is the same bug
    class flagged in docs/upgrade/audit.md B.3, avoided here by exact
    (case/whitespace-normalized) matching, never substring matching.
  - `zcat ... | grep -oE '"country": *"[^"]*"' | sort -u` found 249 distinct
    country values; the only exact match for "United States" is the literal
    string "united states" (distinct from "american samoa",
    "u.s. virgin islands", "united states minor outlying islands" -- all
    excluded as territories, not the country itself, per the brief's plain
    "Country: United States" instruction).

Bump MAPPING_VERSION whenever either map changes; it's recorded on every
SourceSnapshot/ImportRun this pipeline produces so a later reader can tell
which normalization rules produced a given row.
"""
from __future__ import annotations

MAPPING_VERSION = "pdl-alias-v1"

SEGMENT_HEALTHCARE = "healthcare"
SEGMENT_REAL_ESTATE = "real_estate"

TARGET_PER_SEGMENT: dict[str, int] = {
    SEGMENT_HEALTHCARE: 2500,
    SEGMENT_REAL_ESTATE: 2500,
}
TARGET_TOTAL = sum(TARGET_PER_SEGMENT.values())

# Exact (lowercased, whitespace-collapsed) source `industry` value -> segment.
# Every value here was empirically confirmed present in the actual source
# file (see module docstring). Anything not listed is simply not a candidate
# for either segment -- there is no fallback substring/fuzzy matching.
INDUSTRY_ALIAS_MAP: dict[str, str] = {
    "hospital & health care": SEGMENT_HEALTHCARE,
    "medical practice": SEGMENT_HEALTHCARE,
    "real estate": SEGMENT_REAL_ESTATE,
}

# Exact (lowercased, whitespace-collapsed) source `country` value -> the
# canonical location string we store. Only "united states" was confirmed
# present as an exact match for the United States in the source file; no
# abbreviation/alias variant ("usa", "u.s.a.", "us") was found, so none is
# speculatively added here. If a later snapshot's corpus contains one,
# extend this map and bump MAPPING_VERSION rather than special-casing it
# inline somewhere else.
COUNTRY_ALIAS_MAP: dict[str, str] = {
    "united states": "united states",
}

# Provider identity for SourceSnapshot rows this pipeline produces.
PROVIDER = "people_data_labs"
MIRROR_URL = (
    "https://huggingface.co/datasets/andreaaltomani/company-dataset/"
    "resolve/main/free_company_dataset.json.gz"
)
# Pinned via `curl -sI` against the download URL (see docs/upgrade's Phase 3
# handoff for the exact command/output) -- the HF repo commit the file was
# resolved from, independent of any future `main` branch changes.
SOURCE_REVISION = "0689a4c96cd3156b43d298860d5b6f29e82432bd"
REPORTED_ACQUISITION_DATE = "2025-07-28"  # per the dataset uploader's claim; not independently verified
RETRIEVED_LICENSE = "CC-BY-4.0"
RETRIEVED_ATTRIBUTION = (
    "Originally compiled by People Data Labs; mirrored on Hugging Face by "
    "andreaaltomani (huggingface.co/datasets/andreaaltomani/company-dataset)."
)
CHECKSUM_ALGORITHM = "sha256"

# Bounds (Part C.5/C.6/Part E defaults).
MAX_ROW_BYTES = 32_768  # a single JSONL line larger than this is rejected, not parsed
MAX_ERROR_EXAMPLES = 50  # bounded error detail retention -- counts are unbounded, examples aren't
CSV_MAX_BYTES = 20 * 1024 * 1024  # 20 MiB, Part E.2 default
CSV_MAX_ROWS = 50_000  # Part E.2 default
PAGINATION_MAX_PAGE_SIZE = 200  # Part E.5 default
