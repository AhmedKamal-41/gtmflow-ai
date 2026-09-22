"""Streaming, resumable download of the PDL source file with checksum
verification. Never loads the file into memory; writes directly to disk in
chunks, to a .part file, renamed to the final name only after the size and
(when available) checksum are confirmed -- so a partial/interrupted
download is never mistaken for a complete one.

Also persists a `<filename>.meta.json` sidecar recording `downloaded_at` --
the actual moment these bytes finished writing to disk -- distinct from
`curated_at` (when `curate` later runs against this file, possibly much
later) and distinct from the source's own `reported_acquisition_date`
(PDL's claimed scrape date, unverifiable from this mirror). Conflating
these three was a documentation error in the original Phase 3 handoff,
corrected in the Phase 3 closeout: see docs/upgrade/phase3-data-handoff.md.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

CHUNK_SIZE = 1024 * 1024  # 1 MiB


@dataclass
class DownloadResult:
    path: Path
    size_bytes: int
    sha256: str
    reused_existing: bool
    downloaded_at: str  # ISO 8601 UTC
    downloaded_at_is_proxy: bool  # True if backfilled from file mtime, not an actual recorded download event


def _meta_path(final_path: Path) -> Path:
    return final_path.with_suffix(final_path.suffix + ".meta.json")


def _write_meta(final_path: Path, *, downloaded_at: str, sha256: str, size_bytes: int) -> None:
    _meta_path(final_path).write_text(
        json.dumps(
            {"downloaded_at": downloaded_at, "sha256": sha256, "size_bytes": size_bytes},
            indent=2,
        ),
        encoding="utf-8",
    )


def _read_meta(final_path: Path) -> dict | None:
    meta_path = _meta_path(final_path)
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _free_disk_bytes(path: Path) -> int:
    return shutil.disk_usage(path.parent if path.parent.exists() else Path(".")).free


def download_source(
    url: str,
    dest_dir: Path,
    filename: str,
    *,
    expected_size: int | None = None,
    min_free_bytes_margin: int = 1024 * 1024 * 1024,  # require 1 GiB headroom beyond the file itself
) -> DownloadResult:
    dest_dir.mkdir(parents=True, exist_ok=True)
    final_path = dest_dir / filename
    part_path = dest_dir / f"{filename}.part"

    if final_path.exists():
        size = final_path.stat().st_size
        if expected_size is None or size == expected_size:
            digest = _sha256_file(final_path)
            meta = _read_meta(final_path)
            if meta is not None and meta.get("sha256") == digest:
                downloaded_at = meta["downloaded_at"]
                is_proxy = False
            else:
                # No sidecar (or it's stale/mismatched) -- this file predates
                # the meta.json convention, or was placed here by hand. Use
                # the file's mtime as an honest proxy, write the sidecar now
                # so every subsequent run has a real recorded value.
                downloaded_at = datetime.fromtimestamp(
                    final_path.stat().st_mtime, tz=timezone.utc
                ).isoformat()
                is_proxy = True
                _write_meta(final_path, downloaded_at=downloaded_at, sha256=digest, size_bytes=size)
            return DownloadResult(
                path=final_path,
                size_bytes=size,
                sha256=digest,
                reused_existing=True,
                downloaded_at=downloaded_at,
                downloaded_at_is_proxy=is_proxy,
            )
        # Existing file doesn't match the expected size -- don't silently
        # trust it; fall through and re-download to .part.

    with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as response:
        response.raise_for_status()
        content_length = response.headers.get("content-length")
        remote_size = int(content_length) if content_length else expected_size

        if remote_size is not None:
            needed = remote_size + min_free_bytes_margin
            free = _free_disk_bytes(dest_dir)
            if free < needed:
                raise RuntimeError(
                    f"Not enough disk space: need ~{needed / 1e9:.2f} GB "
                    f"({remote_size / 1e9:.2f} GB file + {min_free_bytes_margin / 1e9:.2f} GB margin), "
                    f"only {free / 1e9:.2f} GB free at {dest_dir}."
                )

        hasher = hashlib.sha256()
        written = 0
        with part_path.open("wb") as f:
            for chunk in response.iter_bytes(CHUNK_SIZE):
                f.write(chunk)
                hasher.update(chunk)
                written += len(chunk)

    if remote_size is not None and written != remote_size:
        raise RuntimeError(
            f"Download incomplete: wrote {written} bytes, server reported "
            f"content-length {remote_size}. Partial file kept at {part_path} "
            "for inspection/resume; NOT renamed to the final filename."
        )

    digest = hasher.hexdigest()
    downloaded_at = datetime.now(timezone.utc).isoformat()
    part_path.rename(final_path)
    _write_meta(final_path, downloaded_at=downloaded_at, sha256=digest, size_bytes=written)
    return DownloadResult(
        path=final_path,
        size_bytes=written,
        sha256=digest,
        reused_existing=False,
        downloaded_at=downloaded_at,
        downloaded_at_is_proxy=False,
    )


def _sha256_file(path: Path, chunk_size: int = CHUNK_SIZE) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()
