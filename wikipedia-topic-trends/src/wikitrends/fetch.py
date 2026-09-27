"""Pageview fetching: per-article and project-aggregate, with two policy guards.

- **Partial-bucket guard** (IMPLEMENTATION_PLAN.md §2.4): a monthly bucket that only covers a
  few days of its month reports a manufactured collapse if fed into a regression. Any
  leading/trailing bucket not fully covered by the requested range is dropped and the
  truncation is reported on :class:`FetchResult` so callers can surface it as a caveat.
- **Missing article as a first-class state** (§2.2): a 404 means "no article exists", not a
  failure. Callers get ``FetchResult(status="missing", ...)`` instead of an exception.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

import httpx

from wikitrends.cache import PageviewsCache, cache_key
from wikitrends.config import (
    AGGREGATE_URL_TEMPLATE,
    DEFAULT_ACCESS,
    DEFAULT_AGENT,
    DEFAULT_GRANULARITY,
    HTTP_TIMEOUT_SECONDS,
    MAX_RETRIES,
    PER_ARTICLE_URL_TEMPLATE,
    RETRY_BACKOFF_BASE_SECONDS,
    RETRYABLE_STATUS_CODES,
    USER_AGENT,
)
from wikitrends.logging import get_logger

logger = get_logger(component="fetch")


@dataclass(frozen=True)
class PageviewPoint:
    """One bucket from the Wikimedia pageviews API."""

    timestamp: str  # e.g. "2024010100" (YYYYMMDD + hour, always "00" for our granularities)
    views: int


@dataclass(frozen=True)
class FetchResult:
    """Outcome of one fetch call: either a point series or a first-class "missing" state."""

    status: str  # "ok" | "missing"
    points: list[PageviewPoint] = field(default_factory=list)
    truncated_leading: int = 0
    truncated_trailing: int = 0


def build_http_client() -> httpx.Client:
    """Build an ``httpx.Client`` with the mandatory descriptive User-Agent set once."""
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT_SECONDS)


def fetch_per_article_views(
    client: httpx.Client,
    cache: PageviewsCache,
    project: str,
    article: str,
    start: str,
    end: str,
    granularity: str = DEFAULT_GRANULARITY,
) -> FetchResult:
    """Fetch per-article pageviews, cache-first, with the partial-bucket guard applied."""
    key = cache_key("per_article", project, article, start, end, granularity)
    url = PER_ARTICLE_URL_TEMPLATE.format(
        project=project,
        access=DEFAULT_ACCESS,
        agent=DEFAULT_AGENT,
        article=quote(article, safe=""),
        granularity=granularity,
        start=start,
        end=end,
    )
    logger.debug("fetch_per_article", project=project, article=article, start=start, end=end)
    return _fetch_and_cache(client, cache, key, url, start, end, granularity)


def fetch_aggregate_views(
    client: httpx.Client,
    cache: PageviewsCache,
    project: str,
    start: str,
    end: str,
    granularity: str = DEFAULT_GRANULARITY,
) -> FetchResult:
    """Fetch project-wide aggregate pageviews — the denominator for relative-share normalization."""
    key = cache_key("aggregate", project, None, start, end, granularity)
    url = AGGREGATE_URL_TEMPLATE.format(
        project=project,
        access=DEFAULT_ACCESS,
        agent=DEFAULT_AGENT,
        granularity=granularity,
        start=start,
        end=end,
    )
    logger.debug("fetch_aggregate", project=project, start=start, end=end)
    return _fetch_and_cache(client, cache, key, url, start, end, granularity)


def _fetch_and_cache(
    client: httpx.Client,
    cache: PageviewsCache,
    key: str,
    url: str,
    start: str,
    end: str,
    granularity: str,
) -> FetchResult:
    cached = cache.get(key)
    if cached is not None:
        logger.debug("fetch_cache_hit", key=key)
        return _result_from_json(cached, start, end, granularity)

    payload = _get_with_retries(client, url)
    if payload is None:
        cache.set(key, {"status": "missing", "items": []})
        return FetchResult(status="missing")

    result_json = {"status": "ok", "items": payload["items"]}
    cache.set(key, result_json)
    return _result_from_json(result_json, start, end, granularity)


def _get_with_retries(client: httpx.Client, url: str) -> dict[str, Any] | None:
    """GET ``url``, retrying retryable statuses/transport errors. ``None`` means 404."""
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.get(url)
        except httpx.TransportError as exc:
            last_exc = exc
            logger.warning("http_transport_error", url=url, attempt=attempt, error=str(exc))
            time.sleep(RETRY_BACKOFF_BASE_SECONDS * attempt)
            continue

        if response.status_code == 404:
            logger.info("http_404", url=url)
            return None
        if response.status_code in RETRYABLE_STATUS_CODES:
            logger.warning(
                "http_retryable_status", url=url, status=response.status_code, attempt=attempt
            )
            time.sleep(RETRY_BACKOFF_BASE_SECONDS * attempt)
            continue

        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data

    raise RuntimeError(f"exhausted {MAX_RETRIES} retries fetching {url}") from last_exc


def _result_from_json(
    payload: dict[str, Any], start: str, end: str, granularity: str
) -> FetchResult:
    if payload.get("status") == "missing":
        return FetchResult(status="missing")

    points = [
        PageviewPoint(timestamp=item["timestamp"], views=item["views"]) for item in payload["items"]
    ]

    truncated_leading = 0
    truncated_trailing = 0
    if granularity == "monthly":
        points, truncated_leading, truncated_trailing = _drop_partial_monthly_buckets(
            points, _parse_bucket_date(start), _parse_bucket_date(end)
        )

    if truncated_leading or truncated_trailing:
        logger.info(
            "partial_bucket_truncated",
            leading=truncated_leading,
            trailing=truncated_trailing,
            granularity=granularity,
        )

    return FetchResult(
        status="ok",
        points=points,
        truncated_leading=truncated_leading,
        truncated_trailing=truncated_trailing,
    )


def align_by_timestamp(
    article: Sequence[PageviewPoint], aggregate: Sequence[PageviewPoint]
) -> tuple[list[float], list[float]]:
    """Pair article/aggregate views by timestamp, dropping buckets missing from either side.

    The two endpoints can be truncated independently by the partial-bucket guard (e.g. an
    article created mid-range has fewer buckets than the project aggregate), so they must be
    aligned before ``analyze.relative_share`` can divide them bucket by bucket.
    """
    aggregate_by_ts = {point.timestamp: point.views for point in aggregate}
    article_values: list[float] = []
    aggregate_values: list[float] = []
    for point in article:
        if point.timestamp in aggregate_by_ts:
            article_values.append(float(point.views))
            aggregate_values.append(float(aggregate_by_ts[point.timestamp]))
    return article_values, aggregate_values


def _parse_bucket_date(value: str) -> date:
    """Parse the first 8 digits of a Wikimedia timestamp/param (``YYYYMMDD...``) as a date."""
    return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))


def _month_end(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _drop_partial_monthly_buckets(
    points: list[PageviewPoint], start: date, end: date
) -> tuple[list[PageviewPoint], int, int]:
    """Drop a leading/trailing monthly bucket not fully covered by ``[start, end]``."""
    if not points:
        return points, 0, 0

    kept = list(points)
    truncated_leading = 0
    truncated_trailing = 0

    first_bucket = _parse_bucket_date(kept[0].timestamp)
    if start > first_bucket.replace(day=1):
        kept = kept[1:]
        truncated_leading = 1

    if kept:
        last_bucket = _parse_bucket_date(kept[-1].timestamp)
        if end < _month_end(last_bucket.year, last_bucket.month):
            kept = kept[:-1]
            truncated_trailing = 1

    return kept, truncated_leading, truncated_trailing
