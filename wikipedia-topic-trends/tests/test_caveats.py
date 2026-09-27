"""Tests for the rule-based caveat generator."""

from __future__ import annotations

import pytest

from wikitrends.analyze import analyze_series
from wikitrends.caveats import STANDING_CAVEATS, build_caveats


def test_missing_article_short_circuits_all_other_caveats() -> None:
    result = analyze_series([100.0 * 1.05**i for i in range(36)])
    caveats = build_caveats(result, is_missing=True)

    assert len(caveats) == 1
    assert "No article exists" in caveats[0]


def test_missing_article_needs_no_analysis_result() -> None:
    """cli.py discovers a missing language at resolve time, before any series exists."""
    caveats = build_caveats(None, is_missing=True)

    assert len(caveats) == 1
    assert "No article exists" in caveats[0]


def test_result_required_unless_missing() -> None:
    with pytest.raises(ValueError, match="result is required"):
        build_caveats(None)


def test_standing_caveats_always_present() -> None:
    result = analyze_series([100.0 * 1.05**i for i in range(36)])
    caveats = build_caveats(result)

    for standing in STANDING_CAVEATS:
        assert standing in caveats


def test_proxy_flag_adds_proxy_caveat() -> None:
    result = analyze_series([100.0 * 1.05**i for i in range(36)], is_proxy=True)
    caveats = build_caveats(result, is_proxy=True)

    assert any("proxy article" in c for c in caveats)
    assert result.trust.level == "low"


def test_truncation_flags_add_matching_caveats() -> None:
    result = analyze_series([100.0 * 1.05**i for i in range(36)])
    caveats = build_caveats(result, truncated_leading=1, truncated_trailing=1)

    assert any("first month" in c for c in caveats)
    assert any("last month" in c for c in caveats)


def test_short_series_adds_seasonality_caveat() -> None:
    result = analyze_series([100.0 + 20.0 * i for i in range(10)])
    caveats = build_caveats(result)

    assert any("seasonality could not be ruled out" in c for c in caveats)


def test_low_trust_adds_indicative_only_caveat() -> None:
    result = analyze_series([500.0] * 36)
    caveats = build_caveats(result)

    assert any("indicative only" in c for c in caveats)
