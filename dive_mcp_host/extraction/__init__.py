"""TIA Portal data extraction module.

Provides PLC block parsing and HMI screen extraction from
TIA Portal exported project data.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dive_mcp_host.extraction.models import HmiExtraction, PlcExtraction

logger = logging.getLogger(__name__)

__all__ = [
    "parse_plc_directory",
    "parse_hmi_project",
    "cache_result",
    "get_cached",
    "cache_snapshot",
]

# ---------------------------------------------------------------------------
# Lazy imports (avoid heavy parsing code import at module level)
# ---------------------------------------------------------------------------

def parse_plc_directory(*args, **kwargs):
    """Parse PLC blocks from exported XML directory. Lazy-loads the parser."""
    from dive_mcp_host.extraction.plc_parser import parse_plc_directory as _parse
    return _parse(*args, **kwargs)


def parse_hmi_project(*args, **kwargs):
    """Parse HMI screens from TIA project directory. Lazy-loads the parser."""
    from dive_mcp_host.extraction.hmi_parser import parse_hmi_project as _parse
    return _parse(*args, **kwargs)


# ---------------------------------------------------------------------------
# In-memory extraction cache
# ---------------------------------------------------------------------------

_CACHE_TTL = 1800  # 30 minutes
_cache: dict[str, tuple[float, PlcExtraction | HmiExtraction]] = {}
_cache_lock = threading.Lock()


def _cache_key(source_path: str, prefix: str) -> str:
    """Generate a deterministic cache key from source path."""
    path_hash = hashlib.md5(source_path.encode()).hexdigest()[:12]
    return f"{prefix}:{path_hash}"


def cache_result(source_path: str, result: PlcExtraction | HmiExtraction, prefix: str) -> str:
    """Store extraction result in cache. Returns the cache key."""
    key = _cache_key(source_path, prefix)
    with _cache_lock:
        _cache[key] = (time.time(), result)
        # Prune stale entries
        cutoff = time.time() - _CACHE_TTL
        stale = [k for k, (ts, _) in _cache.items() if ts < cutoff]
        for k in stale:
            del _cache[k]
    logger.debug("Cached extraction result: %s", key)
    return key


def get_cached(key: str) -> PlcExtraction | HmiExtraction | None:
    """Retrieve a cached extraction result by key."""
    with _cache_lock:
        entry = _cache.get(key)
    if entry is None:
        return None
    ts, result = entry
    if time.time() - ts > _CACHE_TTL:
        return None
    return result


def list_cache_keys() -> list[str]:
    """List all active cache keys (for debugging)."""
    with _cache_lock:
        return list(_cache.keys())


def cache_snapshot() -> list[dict]:
    """Return a snapshot of active cache entries: ``[{key, type, age_seconds}]``.

    ``type`` is the stored result's class name (``PlcExtraction`` /
    ``HmiExtraction``); ``age_seconds`` is seconds since the entry was cached.
    Stale entries (past the TTL) are excluded. Sorted by key.
    """
    now = time.time()
    snapshot: list[dict] = []
    with _cache_lock:
        for key, (ts, result) in _cache.items():
            if now - ts > _CACHE_TTL:
                continue
            snapshot.append(
                {
                    "key": key,
                    "type": type(result).__name__,
                    "age_seconds": int(now - ts),
                }
            )
    snapshot.sort(key=lambda entry: entry["key"])
    return snapshot
