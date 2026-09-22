"""Streaming curation: eligibility filtering, within-snapshot identity/dedup,
and deterministic per-segment reservoir sampling -- all in a single pass over
the source file, with bounded memory.

SAMPLING AND END-OF-FILE
-------------------------
Selection uses seeded reservoir sampling (Algorithm R) per segment. This
does NOT require knowing the eligible population size in advance, but it
DOES require seeing every eligible record to produce a fair, uniform sample
-- every eligible record must have a chance to be selected or to replace an
earlier pick, right up to the last one in the file. A run stopped early
(via `--limit`, a smoke-test knob) only samples from the prefix it saw and
is NOT a representative selection of the full corpus; it is labeled as a
partial scan (`reached_eof=False`) everywhere it's reported, never
presented as if it were the real curated set.

MEMORY BOUNDS
-------------
Two kinds of state track the scan, neither holding full record dicts and
neither scaling with the full ~32M-row corpus itself:
  - The identity/content-hash dedup index (`IdentityHashStore`,
    identity_store.py) is disk-backed (a temp SQLite file), not an
    in-memory dict, with a small bounded LRU cache in front for throughput.
    It still logically holds one entry per ELIGIBLE record seen so far
    (~962,000 on the real PDL corpus) -- disk-backed specifically so that
    population isn't required to fit in memory even though it's much
    smaller than the full corpus.
  - The reservoirs: at most TARGET_PER_SEGMENT full records per segment,
    fixed-size regardless of corpus size, kept in memory (this part was
    never the concern -- it's small and bounded by construction).
Everything else (raw record, NormalizedCompany) is discarded after each
record is processed unless it's copied into a reservoir slot.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.pdl.config import MAX_ERROR_EXAMPLES, TARGET_PER_SEGMENT
from app.pdl.identity import compute_content_hash, compute_identity
from app.pdl.identity_store import IdentityHashStore
from app.pdl.normalize import NormalizedCompany, normalize_record
from app.pdl.source_reader import ScanStats, iter_source_records


def _segment_seed(seed: int, segment: str) -> int:
    """Deterministic per-segment seed. Never uses Python's built-in hash()
    for strings -- that's randomized per-process (PYTHONHASHSEED) and would
    silently break reproducibility across runs."""
    digest = hashlib.sha256(f"{seed}:{segment}".encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


class _Reservoir:
    """Algorithm R, deterministic given (seed, insertion order)."""

    def __init__(self, target: int, seed: int) -> None:
        self.target = target
        self._rng = random.Random(seed)
        self.items: list[NormalizedCompany] = []
        self.eligible_unique_seen = 0

    def offer(self, company: NormalizedCompany) -> None:
        self.eligible_unique_seen += 1
        k = self.eligible_unique_seen
        if len(self.items) < self.target:
            self.items.append(company)
            return
        j = self._rng.randint(1, k)
        if j <= self.target:
            self.items[j - 1] = company


@dataclass
class ConflictRecord:
    identity_key: str
    segment: str
    reason: str


@dataclass
class CurationResult:
    selected_by_segment: dict[str, list[NormalizedCompany]]
    eligible_unique_by_segment: dict[str, int]
    exact_duplicate_count_by_segment: dict[str, int]
    conflict_count_by_segment: dict[str, int]
    conflict_examples: list[ConflictRecord]
    fingerprint_identity_count_by_segment: dict[str, int]
    scan_stats: ScanStats
    seed: int

    @property
    def reached_eof(self) -> bool:
        return self.scan_stats.reached_eof

    @property
    def is_representative(self) -> bool:
        """False for any --limit'd smoke run -- see module docstring."""
        return self.reached_eof


def curate(
    path: Path,
    *,
    seed: int,
    limit: int | None = None,
    max_error_examples: int = MAX_ERROR_EXAMPLES,
    identity_store_path: Path | None = None,
) -> CurationResult:
    """`identity_store_path`: where to put the disk-backed dedup index's
    SQLite file. Defaults to a temp file, removed when the scan finishes.
    Exposed as a parameter so tests can point it somewhere inspectable.
    """
    reservoirs = {
        segment: _Reservoir(target=target, seed=_segment_seed(seed, segment))
        for segment, target in TARGET_PER_SEGMENT.items()
    }
    exact_duplicate_count: dict[str, int] = {seg: 0 for seg in TARGET_PER_SEGMENT}
    conflict_count: dict[str, int] = {seg: 0 for seg in TARGET_PER_SEGMENT}
    conflict_examples: list[ConflictRecord] = []
    fingerprint_count: dict[str, int] = {seg: 0 for seg in TARGET_PER_SEGMENT}
    scan_stats = ScanStats()

    # Disk-backed, not a plain in-memory dict (Part C.1 of the Phase 3
    # closeout): this index holds one entry per ELIGIBLE record seen so far
    # (~962,000 on the real PDL corpus, not the full ~32.3M rows), which is
    # still unbounded in the general case. A small bounded LRU cache sits in
    # front for throughput; the reservoirs above remain in-memory and
    # fixed-size (TARGET_PER_SEGMENT), unaffected by this change.
    with IdentityHashStore(db_path=identity_store_path) as seen_identity_hash:
        for raw in iter_source_records(
            path, limit=limit, max_error_examples=max_error_examples, stats=scan_stats
        ):
            company = normalize_record(raw)
            if not company.is_eligible:
                continue

            segment = company.candidate_segment
            assert segment is not None  # guaranteed by is_eligible

            identity = compute_identity(company)
            content_hash = compute_content_hash(company)

            prior_hash = seen_identity_hash.get(identity.key)
            if prior_hash is not None:
                if prior_hash == content_hash:
                    exact_duplicate_count[segment] += 1
                else:
                    conflict_count[segment] += 1
                    if len(conflict_examples) < max_error_examples:
                        conflict_examples.append(
                            ConflictRecord(
                                identity_key=identity.key,
                                segment=segment,
                                reason=(
                                    "same identity seen again with different "
                                    "normalized content; first-seen version kept, "
                                    "this occurrence discarded"
                                ),
                            )
                        )
                continue  # never re-offer a known identity to the reservoir

            seen_identity_hash.set(identity.key, content_hash)
            if identity.confidence == "fingerprint":
                fingerprint_count[segment] += 1
            reservoirs[segment].offer(company)

    return CurationResult(
        selected_by_segment={seg: r.items for seg, r in reservoirs.items()},
        eligible_unique_by_segment={
            seg: r.eligible_unique_seen for seg, r in reservoirs.items()
        },
        exact_duplicate_count_by_segment=exact_duplicate_count,
        conflict_count_by_segment=conflict_count,
        conflict_examples=conflict_examples,
        fingerprint_identity_count_by_segment=fingerprint_count,
        scan_stats=scan_stats,
        seed=seed,
    )
