"""Tests for fetch.py's pure helpers.

Recorded-fixture tests for the network-facing fetch functions (partial-bucket guard, 404
handling, cache hit/miss against real HTTP shapes) remain deferred, per PROGRESS.md. This
file only covers ``align_by_timestamp``, which needs no network and is exercised directly
by cli.py's analyze step.
"""

from __future__ import annotations

from wikitrends.fetch import PageviewPoint, align_by_timestamp


def test_align_by_timestamp_pairs_matching_buckets_in_order() -> None:
    article = [
        PageviewPoint(timestamp="2024010100", views=10),
        PageviewPoint(timestamp="2024020100", views=20),
    ]
    aggregate = [
        PageviewPoint(timestamp="2024010100", views=1000),
        PageviewPoint(timestamp="2024020100", views=2000),
    ]

    article_values, aggregate_values = align_by_timestamp(article, aggregate)

    assert article_values == [10.0, 20.0]
    assert aggregate_values == [1000.0, 2000.0]


def test_align_by_timestamp_drops_buckets_missing_on_either_side() -> None:
    # The article was created in February; the project aggregate has January too.
    article = [PageviewPoint(timestamp="2024020100", views=20)]
    aggregate = [
        PageviewPoint(timestamp="2024010100", views=1000),
        PageviewPoint(timestamp="2024020100", views=2000),
    ]

    article_values, aggregate_values = align_by_timestamp(article, aggregate)

    assert article_values == [20.0]
    assert aggregate_values == [2000.0]


def test_align_by_timestamp_empty_when_no_overlap() -> None:
    article = [PageviewPoint(timestamp="2024010100", views=10)]
    aggregate = [PageviewPoint(timestamp="2024020100", views=1000)]

    assert align_by_timestamp(article, aggregate) == ([], [])
