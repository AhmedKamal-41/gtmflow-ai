import hashlib
import json
from typing import Any


def content_hash(content: Any) -> str:
    """SHA-256 of canonical JSON -- the identity of an output's exact
    content. Output rows are immutable, so a row's hash never changes."""
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
