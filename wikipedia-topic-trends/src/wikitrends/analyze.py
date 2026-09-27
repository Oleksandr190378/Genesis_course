"""Trend estimation and trust scoring for one article's monthly pageview series.

Implements IMPLEMENTATION_PLAN.md §4: Mann-Kendall + Sen's slope for a robust trend
direction/magnitude, OLS-on-log for an interpretable %/year figure, and a rule-based
trust verdict (``high``/``medium``/``low``). Every number here is deterministic and
testable on synthetic data — none of it is decided by an LLM, per R7.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

from wikitrends.config import (
    MIN_MEDIAN_VIEWS_FLOOR,
    MIN_SERIES_BUCKETS,
    MK_SIGNIFICANCE_ALPHA,
    OLS_MIN_R_SQUARED,
    SPIKE_DOMINANCE_SHARE,
    SPIKE_MODIFIED_Z_THRESHOLD,
    ZERO_RUN_BUCKETS_FLAG,
)


@dataclass(frozen=True)
class MannKendallResult:
    """Result of the Mann-Kendall trend test."""

    direction: str  # "increasing" | "decreasing" | "no trend"
    s: float
    z: float
    p_value: float


@dataclass(frozen=True)
class OlsResult:
    """OLS fit of log1p(views) against time, in months."""

    percent_per_year: float
    r_squared: float


@dataclass(frozen=True)
class TrustVerdict:
    """Trust level with the human-readable rules that produced it."""

    level: str  # "high" | "medium" | "low"
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AnalysisResult:
    """Full trend + trust output for one monthly pageview series."""

    n_buckets: int
    median_views: float
    mk: MannKendallResult
    sens_slope_per_year: float
    ols: OlsResult
    spike_indices: list[int]
    spike_dominant: bool
    zero_run_max: int
    seasonality_checked: bool
    trust: TrustVerdict


def relative_share(article_views: Sequence[float], aggregate_views: Sequence[float]) -> list[float]:
    """Divide article views by project-wide aggregate views, bucket by bucket.

    This is the cross-language normalization from IMPLEMENTATION_PLAN.md §4: raw view
    counts compare audience size, not topic interest, so every comparison across
    languages must go through this function first.
    """
    if len(article_views) != len(aggregate_views):
        raise ValueError("article_views and aggregate_views must be the same length")
    return [
        a / total if total > 0 else 0.0
        for a, total in zip(article_views, aggregate_views, strict=True)
    ]


def mann_kendall(values: Sequence[float]) -> MannKendallResult:
    """Non-parametric trend test: robust to the spikes that dominate pageview data."""
    n = len(values)
    s = sum(_sign(values[j] - values[i]) for i in range(n - 1) for j in range(i + 1, n))

    tie_counts = _tie_counts(values)
    tie_correction = sum(c * (c - 1) * (2 * c + 5) for c in tie_counts)
    variance = (n * (n - 1) * (2 * n + 5) - tie_correction) / 18.0

    if variance <= 0 or s == 0:
        z = 0.0
    elif s > 0:
        z = (s - 1) / math.sqrt(variance)
    else:
        z = (s + 1) / math.sqrt(variance)

    p_value = 2 * (1 - _standard_normal_cdf(abs(z)))

    if s > 0:
        direction = "increasing"
    elif s < 0:
        direction = "decreasing"
    else:
        direction = "no trend"

    return MannKendallResult(direction=direction, s=float(s), z=z, p_value=p_value)


def sens_slope_per_bucket(values: Sequence[float]) -> float:
    """Median of all pairwise slopes — Sen's slope, in views per bucket."""
    n = len(values)
    slopes = [(values[j] - values[i]) / (j - i) for i in range(n - 1) for j in range(i + 1, n)]
    return statistics.median(slopes) if slopes else 0.0


def ols_log_trend(values: Sequence[float]) -> OlsResult:
    """Fit ``log(views) = a + b * month`` by OLS; return an interpretable %/year figure.

    Uses a genuine logarithm rather than ``log1p``: ``log(c * v) = log(c) + log(v)``, so the
    fitted slope -- and therefore ``percent_per_year`` -- is invariant to an overall rescaling
    of ``values``, whether they are raw view counts or the relative-share fractions
    ``cli.py`` normally passes in. ``log1p`` does not have that property: it only tracks
    ``log`` for values well above 1, and for values on the relative-share scale (~1e-4) it
    behaves like the identity function instead, silently collapsing every percent-change
    figure to a near-zero, meaningless number. Exact zeros are floored to half the smallest
    positive value in the series so the log stays finite without dominating the fit.
    """
    positive = [v for v in values if v > 0]
    floor = min(positive) / 2 if positive else 1e-9
    x = [float(i) for i in range(len(values))]
    log_y = [math.log(v) if v > 0 else math.log(floor) for v in values]

    mean_x = statistics.fmean(x)
    mean_log_y = statistics.fmean(log_y)
    sxx = sum((xi - mean_x) ** 2 for xi in x)
    sxy = sum((xi - mean_x) * (lyi - mean_log_y) for xi, lyi in zip(x, log_y, strict=True))
    slope = sxy / sxx if sxx > 0 else 0.0
    intercept = mean_log_y - slope * mean_x

    predicted = [intercept + slope * xi for xi in x]
    ss_res = sum((lyi - pi) ** 2 for lyi, pi in zip(log_y, predicted, strict=True))
    ss_tot = sum((lyi - mean_log_y) ** 2 for lyi in log_y)
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    percent_per_year = (math.exp(slope * 12) - 1) * 100
    return OlsResult(percent_per_year=percent_per_year, r_squared=r_squared)


def detect_spikes(values: Sequence[float]) -> list[int]:
    """Indices of buckets whose modified z-score (MAD-based) exceeds the spike threshold."""
    med = statistics.median(values)
    abs_devs = [abs(v - med) for v in values]
    mad = statistics.median(abs_devs)
    if mad == 0:
        return []
    return [i for i, d in enumerate(abs_devs) if 0.6745 * d / mad > SPIKE_MODIFIED_Z_THRESHOLD]


def is_spike_dominant(values: Sequence[float], spike_indices: Sequence[int]) -> bool:
    """Whether the spike buckets alone account for most of the series' total views."""
    if not spike_indices:
        return False
    total = sum(values)
    if total <= 0:
        return False
    spike_total = sum(values[i] for i in spike_indices)
    return (spike_total / total) >= SPIKE_DOMINANCE_SHARE


def max_zero_run(values: Sequence[float]) -> int:
    """Length of the longest run of consecutive zero-view buckets."""
    longest = current = 0
    for v in values:
        if v == 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def seasonality_checked(n_buckets: int) -> bool:
    """Whether the series is long enough for a year-over-year seasonality comparison."""
    return n_buckets >= MIN_SERIES_BUCKETS


def compute_trust(
    n_buckets: int,
    median_views: float,
    mk_p_value: float,
    ols_r_squared: float,
    spike_dominant: bool,
    zero_run_max: int,
    is_proxy: bool,
) -> TrustVerdict:
    """Apply the documented trust rules in order; the first matching hard cap wins."""
    if is_proxy:
        return TrustVerdict(level="low", reasons=["proxy-measured concept: capped at low trust"])

    if median_views < MIN_MEDIAN_VIEWS_FLOOR:
        return TrustVerdict(
            level="low",
            reasons=[
                f"median views ({median_views:.0f}) below the noise floor "
                f"({MIN_MEDIAN_VIEWS_FLOOR:.0f})"
            ],
        )

    if zero_run_max >= ZERO_RUN_BUCKETS_FLAG:
        return TrustVerdict(
            level="low",
            reasons=[
                f"{zero_run_max} consecutive zero-view buckets suggest a data integrity "
                "issue (creation, rename, or redirect breakage)"
            ],
        )

    if spike_dominant:
        return TrustVerdict(
            level="low",
            reasons=[
                "a few outlier buckets account for most of the change: this is an event, "
                "not a sustained trend"
            ],
        )

    significant = mk_p_value < MK_SIGNIFICANCE_ALPHA

    if n_buckets < MIN_SERIES_BUCKETS:
        level = "medium" if significant else "low"
        reasons = [f"series has only {n_buckets} monthly buckets (<{MIN_SERIES_BUCKETS})"]
        reasons.append(
            "trend is statistically significant"
            if significant
            else "trend is not statistically significant"
        )
        return TrustVerdict(level=level, reasons=reasons)

    if significant and ols_r_squared >= OLS_MIN_R_SQUARED:
        return TrustVerdict(
            level="high",
            reasons=[
                f"Mann-Kendall trend significant (p={mk_p_value:.3f}) with adequate "
                f"log-linear fit (R²={ols_r_squared:.2f})"
            ],
        )

    if significant:
        return TrustVerdict(
            level="medium",
            reasons=[
                f"Mann-Kendall trend significant (p={mk_p_value:.3f}) but weak "
                f"log-linear fit (R²={ols_r_squared:.2f})"
            ],
        )

    return TrustVerdict(
        level="low",
        reasons=[f"Mann-Kendall trend not statistically significant (p={mk_p_value:.3f})"],
    )


def analyze_series(
    views: Sequence[float],
    *,
    raw_views: Sequence[float] | None = None,
    is_proxy: bool = False,
) -> AnalysisResult:
    """Run the full trend + trust pipeline on one monthly pageview series.

    ``views`` drives trend/spike/seasonality detection and is normally the relative-share
    series (article views / project-wide views) so results are comparable across languages.
    ``MIN_MEDIAN_VIEWS_FLOOR`` is a raw-traffic threshold, not a share threshold, so the
    volume-floor check needs the actual view counts: pass them as ``raw_views`` (same length
    as ``views``). When omitted, ``views`` doubles as the raw-view series too, which is what
    the synthetic single-series tests in this module rely on.
    """
    if len(views) < 2:
        raise ValueError("need at least 2 data points to analyze a trend")

    mk = mann_kendall(views)
    ols = ols_log_trend(views)
    median_views = statistics.median(raw_views if raw_views is not None else views)
    spikes = detect_spikes(views)
    spike_dominant = is_spike_dominant(views, spikes)
    zero_run = max_zero_run(views)
    seasonal_ok = seasonality_checked(len(views))

    trust = compute_trust(
        n_buckets=len(views),
        median_views=median_views,
        mk_p_value=mk.p_value,
        ols_r_squared=ols.r_squared,
        spike_dominant=spike_dominant,
        zero_run_max=zero_run,
        is_proxy=is_proxy,
    )

    return AnalysisResult(
        n_buckets=len(views),
        median_views=median_views,
        mk=mk,
        sens_slope_per_year=sens_slope_per_bucket(views) * 12,
        ols=ols,
        spike_indices=spikes,
        spike_dominant=spike_dominant,
        zero_run_max=zero_run,
        seasonality_checked=seasonal_ok,
        trust=trust,
    )


def _sign(x: float) -> int:
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def _tie_counts(values: Sequence[float]) -> list[int]:
    counts: dict[float, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return [c for c in counts.values() if c > 1]


def _standard_normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))
