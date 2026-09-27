"""Disk cache for Wikimedia pageviews responses.

Keyed on ``(kind, project, article, start, end, granularity)`` per IMPLEMENTATION_PLAN.md
§3.3, so a later ``fetch --add-langs de`` reuses everything already fetched instead of
re-running the whole pipeline.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from wikitrends.logging import get_logger

logger = get_logger(component="cache")

_CACHE_VERSION = "v1"


def cache_key(
    kind: str,
    project: str,
    article: str | None,
    start: str,
    end: str,
    granularity: str,
) -> str:
    """Build a stable, filesystem-safe cache key for one fetch request.

    ``kind`` distinguishes per-article requests from project-aggregate requests that would
    otherwise share the same ``(project, start, end, granularity)`` tuple.
    """
    raw = "|".join([_CACHE_VERSION, kind, project, article or "", start, end, granularity])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{kind}_{digest}"


class PageviewsCache:
    """Flat JSON-file cache under ``.wikitrends/cache``."""

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        return self._cache_dir / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        """Return the cached JSON value for ``key``, or ``None`` on a miss."""
        path = self._path_for(key)
        if not path.exists():
            logger.debug("cache_miss", key=key)
            return None
        logger.debug("cache_hit", key=key)
        value: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return value

    def set(self, key: str, value: dict[str, Any]) -> None:
        """Persist ``value`` under ``key``, overwriting any previous entry."""
        path = self._path_for(key)
        path.write_text(json.dumps(value), encoding="utf-8")
        logger.debug("cache_write", key=key)
