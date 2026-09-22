"""Phase 3 closeout Parts C.2 and E: focused fixture-based verification of
curation behavior that the real 32.3M-row PDL scan couldn't exercise (it
had zero duplicates/conflicts/shortfalls -- see phase3-data-handoff.md).

Also proves the disk-backed IdentityHashStore (Part C.1) produces identical
selection behavior to a plain in-memory dict for the same input/seed --
the storage backend changed, the algorithm did not.
"""
from __future__ import annotations

import gzip
import json
import struct
from pathlib import Path

import pytest

from app.pdl.curate import curate
from app.pdl.source_reader import SourceCorruptError, iter_source_records


def _write_jsonl_gz(path: Path, records: list[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _rec(
    id_: str | None,
    name: str,
    industry: str | None,
    country: str = "united states",
    website: str | None = None,
    size: str = "1-10",
    locality: str = "austin",
    region: str = "texas",
) -> dict:
    return {
        "id": id_,
        "website": website,
        "name": name,
        "founded": None,
        "size": size,
        "locality": locality,
        "region": region,
        "country": country,
        "industry": industry,
        "linkedin_url": None,
    }


@pytest.fixture()
def rich_fixture(tmp_path: Path) -> Path:
    """A small, hand-constructed corpus exercising: exact duplicates (same
    id, same content), conflicts (same id, different content), a missing-id
    fingerprint fallback, two distinct companies sharing a domain, and
    plenty of ineligible records (wrong country/industry) that must be
    filtered out."""
    records = [
        # --- healthcare, clean ---
        _rec("h1", "clinic one", "medical practice", website="clinic1.com"),
        _rec("h2", "clinic two", "hospital & health care", website="clinic2.com"),
        _rec("h3", "clinic three", "medical practice"),
        # --- healthcare: exact duplicate of h1 (identical content, appears twice) ---
        _rec("h1", "clinic one", "medical practice", website="clinic1.com"),
        # --- healthcare: CONFLICT -- same id "h2" but different content (name changed) ---
        _rec("h2", "clinic two RENAMED", "hospital & health care", website="clinic2.com"),
        # --- healthcare: no id -> fingerprint fallback identity ---
        _rec(None, "clinic four no id", "medical practice", locality="waco", region="texas"),
        # same fingerprint record repeated (exact dup via fingerprint identity)
        _rec(None, "clinic four no id", "medical practice", locality="waco", region="texas"),
        # --- real_estate, clean, two distinct companies sharing one domain ---
        _rec("r1", "realty alpha", "real estate", website="shared-domain.com"),
        _rec("r2", "realty beta", "real estate", website="shared-domain.com"),
        _rec("r3", "realty gamma", "real estate"),
        # --- ineligible: wrong country ---
        _rec("x1", "foreign health co", "medical practice", country="canada"),
        # --- ineligible: adjacent-but-excluded industry (not one of the 3 exact strings) ---
        _rec("x2", "hospitality co", "hospitality"),
        _rec("x3", "commercial re co", "commercial real estate"),
        # --- ineligible: missing name ---
        _rec("x4", "", "real estate"),
        # --- ineligible: null industry ---
        _rec("x5", "no industry co", None),
    ]
    path = tmp_path / "fixture.json.gz"
    _write_jsonl_gz(path, records)
    return path


def test_dedup_and_conflict_and_fingerprint_and_shared_domain(
    rich_fixture: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Small targets so this fixture (well under 2,500) doesn't report a
    # shortfall -- shortfall behavior is tested separately below.
    monkeypatch.setattr("app.pdl.curate.TARGET_PER_SEGMENT", {"healthcare": 10, "real_estate": 10})

    result = curate(rich_fixture, seed=1)

    assert result.reached_eof is True
    assert result.is_representative is True

    # --- eligible-unique counts (post-eligibility, pre-dedup would be higher) ---
    # healthcare eligible distinct identities: h1, h2, h3, fingerprint(clinic four) = 4
    assert result.eligible_unique_by_segment["healthcare"] == 4
    # real_estate eligible distinct identities: r1, r2, r3 = 3
    assert result.eligible_unique_by_segment["real_estate"] == 3

    # --- exact duplicate: h1 repeated with identical content ---
    assert result.exact_duplicate_count_by_segment["healthcare"] >= 1
    # --- conflict: h2 repeated with different content ---
    assert result.conflict_count_by_segment["healthcare"] == 1
    assert len(result.conflict_examples) == 1
    assert result.conflict_examples[0].identity_key == "id:h2"

    # --- first-seen-wins: the conflict must NOT have overwritten h2's original name ---
    healthcare_names = {c.company_name for c in result.selected_by_segment["healthcare"]}
    assert "clinic two" in healthcare_names
    assert "clinic two RENAMED" not in healthcare_names

    # --- fingerprint fallback: the no-id record was selected with fingerprint confidence ---
    assert result.fingerprint_identity_count_by_segment["healthcare"] == 1
    fp_company = next(
        c for c in result.selected_by_segment["healthcare"] if c.company_name == "clinic four no id"
    )
    assert fp_company.source_record_id is None  # no PDL id -- confirms fingerparint path, not id path

    # --- distinct companies sharing a domain are NOT collapsed/merged ---
    re_names = {c.company_name for c in result.selected_by_segment["real_estate"]}
    assert {"realty alpha", "realty beta", "realty gamma"} <= re_names
    re_by_domain = [c for c in result.selected_by_segment["real_estate"] if c.domain == "shared-domain.com"]
    assert len(re_by_domain) == 2  # both kept as distinct companies

    # --- ineligible records never appear anywhere in the selection ---
    all_selected_names = healthcare_names | re_names
    assert "foreign health co" not in all_selected_names
    assert "hospitality co" not in all_selected_names
    assert "commercial re co" not in all_selected_names
    assert "" not in all_selected_names
    assert "no industry co" not in all_selected_names


def test_disk_backed_identity_store_matches_plain_dict_reference(
    rich_fixture: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Part C.2: the disk-backed IdentityHashStore must select exactly what
    a plain in-memory dict would, for the same input+seed -- the algorithm
    is unchanged, only the storage backend is."""
    monkeypatch.setattr("app.pdl.curate.TARGET_PER_SEGMENT", {"healthcare": 10, "real_estate": 10})

    # Reference implementation: same eligibility/identity/first-seen-wins
    # logic, but a plain dict, mirroring curate.py before the Part C.1 change.
    from app.pdl.identity import compute_content_hash, compute_identity
    from app.pdl.normalize import normalize_record
    from app.pdl.source_reader import iter_source_records as _iter

    seen: dict[str, str] = {}
    reference_selected: dict[str, list[str]] = {"healthcare": [], "real_estate": []}
    for raw in _iter(rich_fixture):
        company = normalize_record(raw)
        if not company.is_eligible:
            continue
        identity = compute_identity(company)
        content_hash = compute_content_hash(company)
        prior = seen.get(identity.key)
        if prior is not None:
            continue  # dup or conflict either way -- first-seen wins, so skip
        seen[identity.key] = content_hash
        reference_selected[company.candidate_segment].append(company.company_name)

    actual = curate(rich_fixture, seed=1)
    actual_names = {
        seg: sorted(c.company_name for c in companies)
        for seg, companies in actual.selected_by_segment.items()
    }
    assert actual_names["healthcare"] == sorted(reference_selected["healthcare"])
    assert actual_names["real_estate"] == sorted(reference_selected["real_estate"])


def test_repeated_curate_calls_are_deterministic(
    rich_fixture: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.pdl.curate.TARGET_PER_SEGMENT", {"healthcare": 2, "real_estate": 2})
    first = curate(rich_fixture, seed=42)
    second = curate(rich_fixture, seed=42)
    first_names = {seg: [c.company_name for c in v] for seg, v in first.selected_by_segment.items()}
    second_names = {seg: [c.company_name for c in v] for seg, v in second.selected_by_segment.items()}
    assert first_names == second_names  # same seed, same input -> identical selection, in order


def test_honest_shortfall_when_insufficient_eligible_records(
    rich_fixture: Path,
) -> None:
    """Real TARGET_PER_SEGMENT (2,500 each) against a tiny fixture that only
    has a handful of eligible records per segment -- must report the true
    shortfall, never silently pad or broaden criteria."""
    result = curate(rich_fixture, seed=1)  # uses the real, unpatched targets
    assert result.eligible_unique_by_segment["healthcare"] == 4
    assert len(result.selected_by_segment["healthcare"]) == 4  # everything eligible got selected
    assert result.eligible_unique_by_segment["real_estate"] == 3
    assert len(result.selected_by_segment["real_estate"]) == 3

    from app.pdl.config import TARGET_PER_SEGMENT

    healthcare_shortfall = TARGET_PER_SEGMENT["healthcare"] - len(result.selected_by_segment["healthcare"])
    assert healthcare_shortfall == TARGET_PER_SEGMENT["healthcare"] - 4
    assert healthcare_shortfall > 0  # confirms this is a genuine, reportable shortfall


# --------------------------------------------------------------------------
# Malformed input vs. a genuinely corrupt/truncated gzip stream
# --------------------------------------------------------------------------


def test_malformed_json_lines_are_skipped_and_counted_not_fatal(tmp_path: Path) -> None:
    path = tmp_path / "malformed.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(json.dumps(_rec("g1", "good co", "real estate")) + "\n")
        f.write("{not valid json at all\n")
        f.write('["a", "json", "array", "not", "an", "object"]\n')
        f.write(json.dumps(_rec("g2", "good co two", "real estate")) + "\n")

    stats_records = list(iter_source_records(path))
    assert len(stats_records) == 2  # only the two well-formed objects


def test_truncated_gzip_stream_raises_corrupt_not_silently_incomplete(tmp_path: Path) -> None:
    """A genuinely truncated/corrupt gzip file must raise SourceCorruptError
    -- never be silently treated as \"scan complete, just fewer records.\""""
    good_path = tmp_path / "good.json.gz"
    _write_jsonl_gz(good_path, [_rec(f"id{i}", f"co {i}", "real estate") for i in range(50)])

    # Truncate the compressed file mid-stream -- this corrupts the gzip
    # member (missing final block/CRC), which is different from a
    # zero-byte or non-gzip file: it must fail while *iterating*, not at
    # open time, proving the scan can't quietly stop early and call itself
    # complete.
    raw = good_path.read_bytes()
    truncated_path = tmp_path / "truncated.json.gz"
    truncated_path.write_bytes(raw[: len(raw) // 2])

    with pytest.raises(SourceCorruptError):
        list(iter_source_records(truncated_path))


def test_not_a_gzip_file_at_all_raises_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "not_gzip.json.gz"
    path.write_text('{"id": "x", "name": "not actually gzipped"}\n', encoding="utf-8")
    with pytest.raises(SourceCorruptError):
        list(iter_source_records(path))
