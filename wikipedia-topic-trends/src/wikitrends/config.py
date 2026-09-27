"""Runtime configuration: API endpoints, User-Agent, and fetch-layer thresholds.

Values here are the ones IMPLEMENTATION_PLAN.md §2.5 calls out as policy constraints
(descriptive User-Agent, ``agent=user`` to exclude bots, polite retry/backoff) rather than
arbitrary tuning knobs, so they live in one place instead of being scattered as literals.
"""

from __future__ import annotations

import os

DEFAULT_GRANULARITY = "monthly"
DEFAULT_ACCESS = "all-access"
DEFAULT_AGENT = "user"
DEFAULT_MONTHS = 24

# Trust-rule thresholds used in analysis and seasonality detection.
MIN_SERIES_BUCKETS = 24
MIN_MEDIAN_VIEWS_FLOOR = 100.0
SPIKE_MODIFIED_Z_THRESHOLD = 3.5
SPIKE_DOMINANCE_SHARE = 0.5
ZERO_RUN_BUCKETS_FLAG = 3
MK_SIGNIFICANCE_ALPHA = 0.05
OLS_MIN_R_SQUARED = 0.3

PER_ARTICLE_URL_TEMPLATE = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "{project}/{access}/{agent}/{article}/{granularity}/{start}/{end}"
)
AGGREGATE_URL_TEMPLATE = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/aggregate/"
    "{project}/{access}/{agent}/{granularity}/{start}/{end}"
)
WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"

# Wikimedia policy requires a descriptive, non-personal contact string. Override via env var
# rather than hardcoding a maintainer's personal address here.
_DEFAULT_CONTACT_URL = "https://github.com/wikitrends/wikipedia-topic-trends"
CONTACT_URL = os.environ.get("WIKITRENDS_CONTACT", _DEFAULT_CONTACT_URL)
USER_AGENT = f"wikipedia-topic-trends/0.1 ({CONTACT_URL})"

HTTP_TIMEOUT_SECONDS = 30.0
MAX_RETRIES = 3
RETRY_BACKOFF_BASE_SECONDS = 1.0
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

CACHE_DIR_NAME = ".wikitrends/cache"
RUNS_DIR_NAME = ".wikitrends/runs"
