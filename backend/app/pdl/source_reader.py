"""Streaming reader for the PDL source file: gzip + JSON-Lines, one company
object per line. Confirmed empirically (byte-range peek of the real file,
see docs/engineering-log's Phase 3 handoff) -- NOT a giant JSON array, so plain
`gzip.open(..., "rt")` + line-by-line `json.loads` is sufficient; no
incremental-JSON-array parser (e.g. ijson) is needed for this source.

Memory-bounded by construction: `gzip.open` in text mode decompresses one
block at a time, and we only ever hold one line/record at a time -- nothing
here reads the file (compressed or decompressed) into memory as a whole.
"""
from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.pdl.config import MAX_ROW_BYTES


class SourceCorruptError(RuntimeError):
    """Raised when the gzip stream itself is truncated/corrupt.

    This is distinct from a malformed JSON line (which is a per-row error,
    recorded and skipped) -- a corrupt stream means everything after this
    point is unknown, so the scan must be reported as incomplete, never as a
    silently-truncated "complete" result.
    """


@dataclass
class RowError:
    line_number: int
    reason: str
    raw_excerpt: str  # bounded, for diagnosis -- never the full oversized row


@dataclass
class ScanStats:
    lines_read: int = 0
    records_parsed: int = 0
    rows_too_large: int = 0
    json_errors: int = 0
    reached_eof: bool = False
    error_examples: list[RowError] = field(default_factory=list)


def iter_source_records(
    path: Path,
    *,
    max_row_bytes: int = MAX_ROW_BYTES,
    max_error_examples: int = 50,
    limit: int | None = None,
    stats: ScanStats | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield each valid record as a dict. Malformed lines are counted and
    skipped (bounded examples kept in `stats`); a genuinely corrupt/truncated
    gzip stream raises SourceCorruptError instead of silently stopping.

    `limit`, when set, stops after that many *lines read* (not records
    parsed) -- this is a smoke-test knob only. A run using `limit` must be
    labeled as a partial/prefix scan, never presented as end-of-file reached
    (see ScanStats.reached_eof and the CLI's `inspect`/`curate` output).
    """
    if stats is None:
        stats = ScanStats()

    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="strict") as f:
            for line_number, raw_line in enumerate(f, start=1):
                stats.lines_read += 1
                if limit is not None and stats.lines_read > limit:
                    stats.reached_eof = False
                    return

                line = raw_line.strip()
                if not line:
                    continue

                if len(line.encode("utf-8", errors="replace")) > max_row_bytes:
                    stats.rows_too_large += 1
                    if len(stats.error_examples) < max_error_examples:
                        stats.error_examples.append(
                            RowError(
                                line_number=line_number,
                                reason=f"row exceeds {max_row_bytes} bytes, skipped",
                                raw_excerpt=line[:200],
                            )
                        )
                    continue

                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    stats.json_errors += 1
                    if len(stats.error_examples) < max_error_examples:
                        stats.error_examples.append(
                            RowError(
                                line_number=line_number,
                                reason=f"JSON parse error: {e}",
                                raw_excerpt=line[:200],
                            )
                        )
                    continue

                if not isinstance(record, dict):
                    stats.json_errors += 1
                    if len(stats.error_examples) < max_error_examples:
                        stats.error_examples.append(
                            RowError(
                                line_number=line_number,
                                reason=f"top-level JSON value is {type(record).__name__}, not an object",
                                raw_excerpt=line[:200],
                            )
                        )
                    continue

                stats.records_parsed += 1
                yield record
            else:
                # Loop completed without an unbounded `limit` break -> the
                # underlying file iterator was exhausted normally.
                stats.reached_eof = True
    except EOFError as e:
        raise SourceCorruptError(
            f"gzip stream ended unexpectedly (truncated) after {stats.lines_read} "
            f"lines read, {stats.records_parsed} records parsed: {e}"
        ) from e
    except gzip.BadGzipFile as e:
        raise SourceCorruptError(f"not a valid/complete gzip file: {e}") from e
    except OSError as e:
        # zlib surfaces mid-stream corruption (bad CRC, truncated block) as
        # an OSError from the gzip module in some Python versions.
        if "CRC" in str(e) or "Incorrect" in str(e) or "unexpected end of" in str(e).lower():
            raise SourceCorruptError(f"gzip stream corrupt or truncated: {e}") from e
        raise
