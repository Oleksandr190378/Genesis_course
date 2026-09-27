"""Synthetic, API-shaped fixtures for Tier 2 end-to-end evals.

IMPLEMENTATION_PLAN.md §8 (Tier 2) asks for "recorded HTTP fixtures for determinism". This
project's own tests (``tests/test_cli.py``, ``tests/test_resolve.py``) already established the
lighter-weight variant: monkeypatch the four network-boundary functions
(``resolve_entity``/``resolve_language``/``fetch_per_article_views``/``fetch_aggregate_views``)
with synthetic, API-shaped return values, rather than replaying raw recorded HTTP bytes. This
module reuses that exact convention so Tier 2 gets determinism today without a separate fixture
-recording effort against the live Wikimedia/Wikidata APIs.

Series are deliberately smooth geometric declines rather than real numbers: a pure geometric
series is exactly log-linear, so ``ols_log_trend`` fits it with ``r_squared == 1.0`` and
``mann_kendall`` is maximally significant (every pairwise comparison agrees) -- both by
construction, not by chance -- which makes the resulting trust verdict ("high" for a real
article, "low" for a proxy) a property of the code under test, not a coin flip. See
``references/METHODOLOGY.md`` §6 for the real (also-declining) worked examples this mirrors.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

from wikitrends.fetch import FetchResult, PageviewPoint
from wikitrends.resolve import EntityCandidate, EntityResolution, LanguageResolution

MONTHS = 24


def _timestamps(months: int = MONTHS) -> list[str]:
    return [f"{2024 + i // 12}{(i % 12) + 1:02d}0100" for i in range(months)]


def geometric_series(start: float, rate: float = 0.98, months: int = MONTHS) -> list[int]:
    """A smooth, monotonic decline: exactly log-linear, never spike-flagged (module docstring)."""
    return [round(start * (rate**i)) for i in range(months)]


def constant_series(value: float, months: int = MONTHS) -> list[int]:
    return [round(value)] * months


@dataclass(frozen=True)
class LangFixture:
    """One language's resolution + pageview series for a scenario."""

    resolution_status: str  # "ok" | "missing"
    title: str | None
    article_views: list[int] | None = None
    aggregate_views: list[int] | None = None
    is_proxy: bool = False
    proxy_qid: str | None = None
    proxy_label: str | None = None


@dataclass(frozen=True)
class ScenarioFixture:
    """Everything :func:`install_mocks` needs to fake one research scenario end-to-end."""

    entity_qid: str
    entity_label: str
    languages: dict[str, LangFixture] = field(default_factory=dict)


SCENARIOS: dict[str, ScenarioFixture] = {
    "fasting_pl_cs": ScenarioFixture(
        entity_qid="Q1666254",
        entity_label="intermittent fasting",
        languages={
            "pl": LangFixture(
                resolution_status="missing",
                title="Post",
                is_proxy=True,
                proxy_qid="Q44602",
                proxy_label="fasting",
                article_views=geometric_series(1500, rate=0.97),
                aggregate_views=constant_series(2.5e8),
            ),
            "cs": LangFixture(
                resolution_status="ok",
                title="Přerušovaný půst",
                article_views=geometric_series(4000, rate=0.98),
                aggregate_views=constant_series(3.0e8),
            ),
            "de": LangFixture(
                resolution_status="ok",
                title="Intermittierendes Fasten",
                article_views=geometric_series(6000, rate=0.98),
                aggregate_views=constant_series(9.0e8),
            ),
        },
    ),
    "astronomy_uk": ScenarioFixture(
        entity_qid="Q333",
        entity_label="astronomy",
        languages={
            "uk": LangFixture(
                resolution_status="ok",
                title="Астрономія",
                article_views=geometric_series(5000, rate=0.98),
                aggregate_views=constant_series(4.0e8),
            ),
        },
    ),
    "english_language_3langs": ScenarioFixture(
        entity_qid="Q1860",
        entity_label="English language",
        languages={
            "uk": LangFixture(
                resolution_status="ok",
                title="Англійська мова",
                article_views=geometric_series(3500, rate=0.98),
                aggregate_views=constant_series(4.0e8),
            ),
            "pl": LangFixture(
                resolution_status="ok",
                title="Język angielski",
                article_views=geometric_series(3800, rate=0.98),
                aggregate_views=constant_series(2.5e8),
            ),
            "es": LangFixture(
                resolution_status="ok",
                title="Idioma inglés",
                article_views=geometric_series(9000, rate=0.98),
                aggregate_views=constant_series(6.0e8),
            ),
        },
    ),
}


def _fake_resolve_entity(scenario: ScenarioFixture) -> Any:
    candidate = EntityCandidate(
        qid=scenario.entity_qid,
        label=scenario.entity_label,
        description="",
        sitelink_count=50,
        instance_of=[],
        score=50.0,
    )

    def resolve_entity(client: Any, topic: str) -> EntityResolution:
        return EntityResolution(status="ok", candidates=[candidate], confidence="high")

    return resolve_entity


def _fake_resolve_language(scenario: ScenarioFixture) -> Any:
    def resolve_language(client: Any, qid: str, lang: str) -> LanguageResolution:
        lang_fixture = scenario.languages.get(lang)
        if lang_fixture is None:
            return LanguageResolution(status="missing", lang=lang, title=None)
        return LanguageResolution(
            status=lang_fixture.resolution_status,
            lang=lang,
            title=lang_fixture.title,
            is_proxy=lang_fixture.is_proxy,
            proxy_qid=lang_fixture.proxy_qid,
            proxy_label=lang_fixture.proxy_label,
        )

    return resolve_language


def _points(views: list[int]) -> list[PageviewPoint]:
    return [
        PageviewPoint(timestamp=ts, views=v) for ts, v in zip(_timestamps(), views, strict=True)
    ]


def _fake_fetch_per_article_views(scenario: ScenarioFixture, calls: list[str]) -> Any:
    by_title = {
        lf.title: lf.article_views
        for lf in scenario.languages.values()
        if lf.title is not None and lf.article_views is not None
    }

    def fetch_per_article_views(
        client: Any,
        cache: Any,
        project: str,
        article: str,
        start: str,
        end: str,
        granularity: str,
    ) -> FetchResult:
        calls.append(f"{project}/{article}")
        views = by_title.get(article)
        if views is None:
            return FetchResult(status="missing")
        return FetchResult(status="ok", points=_points(views))

    return fetch_per_article_views


def _fake_fetch_aggregate_views(scenario: ScenarioFixture) -> Any:
    by_project = {
        f"{lang}.wikipedia": lf.aggregate_views
        for lang, lf in scenario.languages.items()
        if lf.aggregate_views is not None
    }

    def fetch_aggregate_views(
        client: Any, cache: Any, project: str, start: str, end: str, granularity: str
    ) -> FetchResult:
        views = by_project.get(project, constant_series(1.0e8))
        return FetchResult(status="ok", points=_points(views))

    return fetch_aggregate_views


@contextmanager
def install_mocks(scenario_id: str) -> Iterator[list[str]]:
    """Patch the four network-boundary functions for the duration of the ``with`` block.

    Yields the list of ``"{project}/{article}"`` strings passed to
    ``fetch_per_article_views`` -- callers use its length to assert cache/skip behavior on
    refinement scenarios (a language fetched twice means the "already fetched" guard in
    ``cli._cmd_fetch`` did not fire).
    """
    scenario = SCENARIOS[scenario_id]
    article_fetch_calls: list[str] = []
    with ExitStack() as stack:
        stack.enter_context(
            patch("wikitrends.cli.resolve_entity", _fake_resolve_entity(scenario))
        )
        stack.enter_context(
            patch("wikitrends.cli.resolve_language", _fake_resolve_language(scenario))
        )
        stack.enter_context(
            patch(
                "wikitrends.cli.fetch_per_article_views",
                _fake_fetch_per_article_views(scenario, article_fetch_calls),
            )
        )
        stack.enter_context(
            patch("wikitrends.cli.fetch_aggregate_views", _fake_fetch_aggregate_views(scenario))
        )
        yield article_fetch_calls
