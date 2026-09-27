"""Synthetic-series tests for analyze.py, per IMPLEMENTATION_PLAN.md §7.1.

Each case has a known qualitative answer (direction, trust cap, or a specific flag) so
these tests run before any real Wikimedia data is touched.
"""

from __future__ import annotations

import math

import pytest

from wikitrends.analyze import (
    analyze_series,
    max_zero_run,
    ols_log_trend,
    relative_share,
)


def test_pure_growth_is_high_trust() -> None:
    views = [100.0 * 1.05**i for i in range(36)]
    result = analyze_series(views)

    assert result.mk.direction == "increasing"
    assert result.mk.p_value < 0.05
    assert result.ols.percent_per_year > 0
    assert result.trust.level == "high"


def test_plateau_has_no_significant_trend() -> None:
    views = [500.0] * 36
    result = analyze_series(views)

    assert result.mk.direction == "no trend"
    assert result.mk.p_value == pytest.approx(1.0)
    assert result.trust.level == "low"


def test_noise_only_is_not_significant() -> None:
    # A palindrome (values[i] == values[n-1-i]) has Mann-Kendall S == 0 exactly: every
    # cross pair (i, j) is cancelled by its mirror pair (n-1-j, n-1-i), and pairs on the
    # i+j == n-1 diagonal are ties by construction. This mound shape has real variation
    # (peak in the middle) but, by that symmetry, no net monotonic trend.
    views = [500.0 + 10.0 * min(i, 35 - i) for i in range(36)]
    result = analyze_series(views)

    assert result.mk.direction == "no trend"
    assert result.mk.p_value == pytest.approx(1.0)
    assert result.trust.level == "low"


def test_single_spike_is_flagged_and_capped() -> None:
    baseline = [505.0 if i % 2 == 0 else 495.0 for i in range(35)]
    views = baseline[:18] + [50_000.0] + baseline[18:]
    result = analyze_series(views)

    assert result.spike_indices == [18]
    assert result.spike_dominant is True
    assert result.trust.level == "low"


def test_seasonal_with_trend_is_checked_and_increasing() -> None:
    # Seasonal amplitude (peak-to-trough 200) is deliberately small next to the drift
    # accumulated over one period (25 * 12 = 300), so the underlying upward trend
    # dominates the wobble and Mann-Kendall should still detect it as increasing.
    views = [1000.0 + 100.0 * math.sin(2 * math.pi * i / 12) + 25.0 * i for i in range(30)]
    result = analyze_series(views)

    assert result.seasonality_checked is True
    assert result.mk.direction == "increasing"


def test_short_series_caps_below_high_and_skips_seasonality() -> None:
    views = [100.0 + 20.0 * i for i in range(10)]
    result = analyze_series(views)

    assert result.n_buckets == 10
    assert result.seasonality_checked is False
    assert result.trust.level in ("medium", "low")
    assert result.trust.level != "high"


def test_zero_run_caps_trust_low() -> None:
    views = [0.0] * 4 + [500.0 + i for i in range(26)]
    result = analyze_series(views)

    assert result.zero_run_max == 4
    assert result.trust.level == "low"


def test_analyze_series_requires_at_least_two_points() -> None:
    with pytest.raises(ValueError):
        analyze_series([100.0])


def test_relative_share_divides_elementwise() -> None:
    shares = relative_share([10.0, 20.0, 30.0], [100.0, 200.0, 300.0])
    assert shares == pytest.approx([0.1, 0.1, 0.1])


def test_relative_share_guards_zero_denominator() -> None:
    shares = relative_share([10.0], [0.0])
    assert shares == [0.0]


def test_relative_share_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError):
        relative_share([1.0, 2.0], [1.0])


def test_max_zero_run_counts_longest_run_only() -> None:
    assert max_zero_run([0, 0, 5, 0, 0, 0, 5]) == 3
    assert max_zero_run([1, 2, 3]) == 0


def test_trust_noise_floor_uses_raw_views_not_relative_share() -> None:
    # cli.py feeds analyze_series a relative-share series (article / project-wide views),
    # which is always a tiny fraction. The volume floor (MIN_MEDIAN_VIEWS_FLOOR=100) is a
    # raw-traffic threshold, so it must be checked against raw_views, not the shares -- a
    # popular, clearly-trending article must not be capped "low" just because its share of
    # total wiki traffic is a small fraction.
    raw_views = [100.0 * 1.05**i for i in range(36)]
    shares = [v / 1_000_000 for v in raw_views]

    result = analyze_series(shares, raw_views=raw_views)

    assert result.median_views > 100.0
    assert result.trust.level == "high"


def test_ols_percent_per_year_is_scale_invariant() -> None:
    # A ~5%/month decline is unambiguous at raw-view scale; the same proportional decline
    # expressed as a tiny relative-share fraction must report the same percent_per_year.
    # log1p broke this invariance for small-scale values (see ols_log_trend's docstring):
    # it behaves like the identity function there instead of like log, so a real decline
    # was previously reported as a near-zero, meaningless percentage.
    raw_views = [1000.0 * 0.95**i for i in range(24)]
    shares = [v * 1e-6 for v in raw_views]

    raw_result = ols_log_trend(raw_views)
    share_result = ols_log_trend(shares)

    assert raw_result.percent_per_year == pytest.approx(share_result.percent_per_year, rel=1e-6)
    assert raw_result.percent_per_year < -40  # 0.95**12 - 1 ≈ -46%/year


def test_ols_log_trend_floors_zeros_instead_of_letting_them_dominate() -> None:
    values = [0.0, 100.0, 105.0, 110.0, 115.0, 120.0]
    result = ols_log_trend(values)

    assert result.percent_per_year > 0
    assert result.r_squared > 0.5


def test_trust_noise_floor_still_flags_low_raw_traffic() -> None:
    # The inverse case: raw views genuinely below the floor must still cap trust "low",
    # even if the share happens to look "large" (e.g. a tiny wiki where this article
    # dominates a near-empty aggregate).
    raw_views = [5.0] * 36
    shares = [0.9] * 36

    result = analyze_series(shares, raw_views=raw_views)

    assert result.trust.level == "low"
    assert "noise floor" in result.trust.reasons[0]
