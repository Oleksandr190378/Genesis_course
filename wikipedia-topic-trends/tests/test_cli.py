"""Tests for cli.py: argument parsing, pure helpers, and command orchestration.

Network-facing calls (resolve_entity/resolve_language/fetch_per_article_views/
fetch_aggregate_views) are monkeypatched with synthetic fixtures shaped like their real
return types, same spirit as test_resolve.py. This exercises the actual risky code —
manifest state transitions and the JSON contract — without any HTTP call. Each test
redirects the run-manifest and cache directories into ``tmp_path`` so nothing touches the
real project's ``.wikitrends/``.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

import wikitrends.cli as cli
from wikitrends.cache import PageviewsCache
from wikitrends.fetch import FetchResult, PageviewPoint
from wikitrends.resolve import EntityCandidate, EntityResolution, LanguageResolution
from wikitrends.workspace import (
    LanguageRecord,
    create_manifest,
    load_manifest,
    save_manifest,
    with_entity,
    with_language,
)

pytestmark = pytest.mark.usefixtures("_isolated_workspace")


@pytest.fixture
def _isolated_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("wikitrends.workspace.RUNS_DIR_NAME", str(tmp_path / "runs"))
    monkeypatch.setattr(cli, "CACHE_DIR_NAME", str(tmp_path / "cache"))


# --------------------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------------------


def test_parse_langs_splits_and_strips_whitespace() -> None:
    assert cli._parse_langs("pl, cs ,uk") == ["pl", "cs", "uk"]


def test_parse_langs_empty_string_is_empty_list() -> None:
    assert cli._parse_langs("") == []


def test_shift_months_wraps_across_a_year_boundary() -> None:
    assert cli._shift_months(date(2024, 1, 15), -1) == date(2023, 12, 1)
    assert cli._shift_months(date(2024, 1, 15), 0) == date(2024, 1, 1)


def test_date_range_is_months_before_today_at_day_one() -> None:
    start, end = cli._date_range(24, today=date(2026, 1, 15))
    assert start == "2024010100"
    assert end == "2026011500"


def test_dedupe_preserves_first_occurrence_order() -> None:
    assert cli._dedupe(["a", "b", "a", "c"]) == ["a", "b", "c"]


# --------------------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------------------


def test_parser_research_requires_topic_and_langs() -> None:
    args = cli.build_parser().parse_args(["research", "--topic", "astronomy", "--langs", "uk"])
    assert args.command == "research"
    assert args.months == 24


def test_parser_fetch_requires_run() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["fetch"])


def test_parser_resolve_run_is_optional() -> None:
    args = cli.build_parser().parse_args(["resolve", "--topic", "x", "--langs", "uk"])
    assert args.run is None


def test_parser_resolve_allows_omitting_topic_and_langs() -> None:
    args = cli.build_parser().parse_args(["resolve", "--run", "run_1", "--qid", "Q1"])
    assert args.topic is None
    assert args.langs is None


def test_main_rejects_resolve_without_topic_langs_or_run(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--log-dir", str(tmp_path / "logs"), "resolve"])


# --------------------------------------------------------------------------------------
# _cmd_resolve
# --------------------------------------------------------------------------------------


def test_cmd_resolve_returns_no_match_when_wikidata_has_no_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_resolve_entity(client: httpx.Client, topic: str) -> EntityResolution:
        return EntityResolution(status="no_match")

    monkeypatch.setattr(cli, "resolve_entity", fake_resolve_entity)

    response = cli._cmd_resolve("run_1", "gibberish topic", ["uk"], 24, None)

    assert response["status"] == "no_match"
    assert response["run_id"] == "run_1"


def test_cmd_resolve_returns_ambiguous_without_resolving_languages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    close_candidates = [
        EntityCandidate(
            qid="Q1", label="a", description="", sitelink_count=5, instance_of=[], score=5.0
        ),
        EntityCandidate(
            qid="Q2", label="b", description="", sitelink_count=4, instance_of=[], score=4.0
        ),
    ]

    def fake_resolve_entity(client: httpx.Client, topic: str) -> EntityResolution:
        return EntityResolution(status="ok", candidates=close_candidates, confidence="low")

    resolve_language_calls: list[tuple[str, str]] = []

    def fake_resolve_language(client: httpx.Client, qid: str, lang: str) -> LanguageResolution:
        resolve_language_calls.append((qid, lang))
        return LanguageResolution(status="ok", lang=lang, title="x")

    monkeypatch.setattr(cli, "resolve_entity", fake_resolve_entity)
    monkeypatch.setattr(cli, "resolve_language", fake_resolve_language)

    response = cli._cmd_resolve("run_1", "intermittent fasting", ["uk"], 24, None)

    assert response["status"] == "ambiguous"
    assert [c["qid"] for c in response["candidates"]] == ["Q1", "Q2"]
    assert resolve_language_calls == []  # ambiguous entity: languages are not resolved yet


def test_cmd_resolve_ok_path_attaches_entity_and_resolves_each_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    top = EntityCandidate(
        qid="Q1666254",
        label="intermittent fasting",
        description="",
        sitelink_count=4,
        instance_of=[],
        score=4.0,
    )

    def fake_resolve_entity(client: httpx.Client, topic: str) -> EntityResolution:
        return EntityResolution(status="ok", candidates=[top], confidence="high")

    def fake_resolve_language(client: httpx.Client, qid: str, lang: str) -> LanguageResolution:
        if lang == "pl":
            return LanguageResolution(
                status="missing",
                lang="pl",
                title="Post",
                is_proxy=True,
                proxy_qid="Q10",
                proxy_label="fasting",
            )
        return LanguageResolution(status="ok", lang=lang, title="Intermittent fasting")

    monkeypatch.setattr(cli, "resolve_entity", fake_resolve_entity)
    monkeypatch.setattr(cli, "resolve_language", fake_resolve_language)

    response = cli._cmd_resolve("run_1", "intermittent fasting", ["uk", "pl"], 24, None)

    assert response["status"] == "ok"
    assert response["entity"] == {
        "qid": "Q1666254",
        "label": "intermittent fasting",
        "confidence": "high",
    }
    assert response["languages"]["uk"]["title"] == "Intermittent fasting"
    assert response["languages"]["pl"]["is_proxy"] is True
    assert any("proxy article" in c for c in response["caveats"])


def test_cmd_resolve_qid_override_skips_search(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_resolve_entity(client: httpx.Client, topic: str) -> EntityResolution:
        raise AssertionError("resolve_entity must not be called when --qid is given")

    def fake_resolve_language(client: httpx.Client, qid: str, lang: str) -> LanguageResolution:
        return LanguageResolution(status="ok", lang=lang, title="x")

    monkeypatch.setattr(cli, "resolve_entity", fail_resolve_entity)
    monkeypatch.setattr(cli, "resolve_language", fake_resolve_language)

    response = cli._cmd_resolve("run_1", "intermittent fasting", ["uk"], 24, "Q1666254")

    assert response["status"] == "ok"
    assert response["entity"]["qid"] == "Q1666254"


def test_cmd_resolve_disambiguation_followup_reuses_stored_langs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``--run ... --qid ...`` follow-up needs neither --topic nor --langs repeated.

    Regression test: the first live Tier 1 eval run (PROGRESS.md step 9) found both models
    under test proposing exactly `resolve --run <id> --qid <qid>` for this scenario, matching
    SKILL.md's own documented recipe -- and the real CLI rejected it because --topic/--langs
    were unconditionally required. This is the fix.
    """

    def fail_resolve_entity(client: httpx.Client, topic: str) -> EntityResolution:
        raise AssertionError("resolve_entity must not be called when --qid is given")

    def fake_resolve_language(client: httpx.Client, qid: str, lang: str) -> LanguageResolution:
        return LanguageResolution(status="ok", lang=lang, title="x")

    monkeypatch.setattr(cli, "resolve_entity", fail_resolve_entity)
    monkeypatch.setattr(cli, "resolve_language", fake_resolve_language)

    # First call: real request, langs get stored on the manifest even though it comes back
    # "ambiguous" (simulated here by just calling with qid=None and a low-confidence result
    # is out of scope for this test -- we only need the manifest state a real first call would
    # have left behind).
    save_manifest(create_manifest("run_1", "intermittent fasting", 24, "monthly", ["uk", "pl"]))

    response = cli._cmd_resolve("run_1", None, None, 24, "Q1666254")

    assert response["status"] == "ok"
    assert set(response["languages"]) == {"uk", "pl"}


# --------------------------------------------------------------------------------------
# _cmd_fetch
# --------------------------------------------------------------------------------------


def test_cmd_fetch_unknown_run_is_an_error() -> None:
    response = cli._cmd_fetch("does_not_exist", [])
    assert response["status"] == "error"
    assert "unknown run_id" in response["problem"]


def test_cmd_fetch_without_a_resolved_entity_is_an_error() -> None:
    manifest = create_manifest("run_1", "topic", 24, "monthly")
    save_manifest(manifest)

    response = cli._cmd_fetch("run_1", [])

    assert response["status"] == "error"
    assert "no resolved entity" in response["problem"]


def test_cmd_fetch_skips_missing_languages_and_fetches_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = create_manifest("run_1", "topic", 24, "monthly")
    manifest = with_entity(manifest, "Q1", "label", "high")
    manifest = with_language(manifest, LanguageRecord(lang="pl", resolution_status="missing"))
    manifest = with_language(
        manifest, LanguageRecord(lang="uk", resolution_status="ok", title="Peryodyczne posty")
    )
    save_manifest(manifest)

    fetch_calls: list[str] = []

    def fake_fetch_per_article_views(
        client: httpx.Client,
        cache: PageviewsCache,
        project: str,
        article: str,
        start: str,
        end: str,
        granularity: str,
    ) -> FetchResult:
        fetch_calls.append(project)
        return FetchResult(status="ok", points=[PageviewPoint(timestamp="2024010100", views=10)])

    def fake_fetch_aggregate_views(
        client: httpx.Client,
        cache: PageviewsCache,
        project: str,
        start: str,
        end: str,
        granularity: str,
    ) -> FetchResult:
        return FetchResult(status="ok")

    monkeypatch.setattr(cli, "fetch_per_article_views", fake_fetch_per_article_views)
    monkeypatch.setattr(cli, "fetch_aggregate_views", fake_fetch_aggregate_views)

    response = cli._cmd_fetch("run_1", [])

    assert fetch_calls == ["uk.wikipedia"]
    assert response["languages"]["uk"]["fetched"] is True
    assert response["languages"]["pl"]["fetched"] is False
    assert any("No article exists" in c for c in response["caveats"])


def test_cmd_fetch_add_langs_resolves_and_fetches_a_new_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = create_manifest("run_1", "topic", 24, "monthly")
    manifest = with_entity(manifest, "Q1", "label", "high")
    save_manifest(manifest)

    def fake_resolve_language(client: httpx.Client, qid: str, lang: str) -> LanguageResolution:
        return LanguageResolution(status="ok", lang=lang, title="Titel")

    def fake_fetch_per_article_views(
        client: httpx.Client,
        cache: PageviewsCache,
        project: str,
        article: str,
        start: str,
        end: str,
        granularity: str,
    ) -> FetchResult:
        return FetchResult(status="ok", points=[PageviewPoint(timestamp="2024010100", views=5)])

    def fake_fetch_aggregate_views(
        client: httpx.Client,
        cache: PageviewsCache,
        project: str,
        start: str,
        end: str,
        granularity: str,
    ) -> FetchResult:
        return FetchResult(status="ok")

    monkeypatch.setattr(cli, "resolve_language", fake_resolve_language)
    monkeypatch.setattr(cli, "fetch_per_article_views", fake_fetch_per_article_views)
    monkeypatch.setattr(cli, "fetch_aggregate_views", fake_fetch_aggregate_views)

    response = cli._cmd_fetch("run_1", ["de"])

    assert response["languages"]["de"]["title"] == "Titel"
    assert response["languages"]["de"]["fetched"] is True


# --------------------------------------------------------------------------------------
# _cmd_analyze
# --------------------------------------------------------------------------------------


def test_cmd_analyze_unknown_run_is_an_error() -> None:
    assert cli._cmd_analyze("does_not_exist")["status"] == "error"


def test_cmd_analyze_missing_language_gets_the_missing_caveat_and_no_analysis() -> None:
    manifest = create_manifest("run_1", "topic", 24, "monthly")
    manifest = with_language(manifest, LanguageRecord(lang="pl", resolution_status="missing"))
    save_manifest(manifest)

    response = cli._cmd_analyze("run_1")

    assert response["languages"]["pl"]["analysis"] is None
    assert any("No article exists" in c for c in response["languages"]["pl"]["caveats"])


def test_cmd_analyze_computes_trend_for_a_fetched_language(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = create_manifest("run_1", "topic", 24, "monthly")
    manifest = with_language(manifest, LanguageRecord(lang="uk", resolution_status="ok", title="x"))
    save_manifest(manifest)

    points = [
        PageviewPoint(timestamp=f"2024{month:02d}0100", views=100 + month) for month in range(1, 13)
    ]

    def fake_fetch_per_article_views(
        client: httpx.Client,
        cache: PageviewsCache,
        project: str,
        article: str,
        start: str,
        end: str,
        granularity: str,
    ) -> FetchResult:
        return FetchResult(status="ok", points=points)

    def fake_fetch_aggregate_views(
        client: httpx.Client,
        cache: PageviewsCache,
        project: str,
        start: str,
        end: str,
        granularity: str,
    ) -> FetchResult:
        aggregate_points = [PageviewPoint(timestamp=p.timestamp, views=10_000) for p in points]
        return FetchResult(status="ok", points=aggregate_points)

    monkeypatch.setattr(cli, "fetch_per_article_views", fake_fetch_per_article_views)
    monkeypatch.setattr(cli, "fetch_aggregate_views", fake_fetch_aggregate_views)

    response = cli._cmd_analyze("run_1")

    assert response["status"] == "ok"
    analysis = response["languages"]["uk"]["analysis"]
    assert analysis is not None
    assert analysis["n_buckets"] == 12
    assert "trust" in analysis


def test_cmd_analyze_skips_a_language_with_too_few_aligned_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = create_manifest("run_1", "topic", 24, "monthly")
    manifest = with_language(manifest, LanguageRecord(lang="uk", resolution_status="ok", title="x"))
    save_manifest(manifest)

    def fake_fetch_per_article_views(
        client: httpx.Client,
        cache: PageviewsCache,
        project: str,
        article: str,
        start: str,
        end: str,
        granularity: str,
    ) -> FetchResult:
        return FetchResult(status="ok", points=[PageviewPoint(timestamp="2024010100", views=1)])

    def fake_fetch_aggregate_views(
        client: httpx.Client,
        cache: PageviewsCache,
        project: str,
        start: str,
        end: str,
        granularity: str,
    ) -> FetchResult:
        return FetchResult(status="ok")

    monkeypatch.setattr(cli, "fetch_per_article_views", fake_fetch_per_article_views)
    monkeypatch.setattr(cli, "fetch_aggregate_views", fake_fetch_aggregate_views)

    response = cli._cmd_analyze("run_1")

    assert response["languages"]["uk"]["analysis"] is None


# --------------------------------------------------------------------------------------
# _cmd_report / _cmd_research
# --------------------------------------------------------------------------------------


def test_cmd_report_unknown_run_is_an_error() -> None:
    assert cli._cmd_report("does_not_exist", "out.pdf")["status"] == "error"


def test_cmd_report_without_a_resolved_entity_is_an_error() -> None:
    save_manifest(create_manifest("run_1", "topic", 24, "monthly"))

    response = cli._cmd_report("run_1", "out.pdf")

    assert response["status"] == "error"
    assert "no resolved entity" in response["problem"]


def test_cmd_report_renders_a_pdf_for_a_resolved_run(tmp_path: Path) -> None:
    # A record as it would look right after `analyze`: caveats already computed and,
    # for the "ok" language, a series to chart.
    manifest = create_manifest("run_1", "intermittent fasting", 24, "monthly")
    manifest = with_entity(manifest, "Q1666254", "intermittent fasting", "high")
    manifest = with_language(
        manifest,
        LanguageRecord(
            lang="pl",
            resolution_status="missing",
            caveats=["No article exists for this concept in pl."],
        ),
    )
    manifest = with_language(
        manifest,
        LanguageRecord(
            lang="uk",
            resolution_status="ok",
            title="x",
            fetched=True,
            series=[0.01, 0.02, 0.03],
            analysis={
                "n_buckets": 3,
                "median_views": 100.0,
                "mk": {"direction": "increasing", "s": 3.0, "z": 1.0, "p_value": 0.01},
                "sens_slope_per_year": 12.0,
                "ols": {"percent_per_year": 10.0, "r_squared": 0.9},
                "spike_indices": [],
                "spike_dominant": False,
                "zero_run_max": 0,
                "seasonality_checked": False,
                "trust": {"level": "medium", "reasons": ["short series"]},
            },
            caveats=["short series", "Pageviews measure attention, not willingness to pay."],
        ),
    )
    save_manifest(manifest)

    out_path = tmp_path / "report.pdf"
    response = cli._cmd_report("run_1", str(out_path))

    assert response["status"] == "ok"
    assert response["out"] == str(out_path)
    assert out_path.exists()
    assert out_path.read_bytes().startswith(b"%PDF")
    assert response["caveats"]  # standing caveats are never empty


def test_cmd_research_runs_the_full_pipeline_in_one_call(monkeypatch: pytest.MonkeyPatch) -> None:
    top = EntityCandidate(
        qid="Q1", label="topic", description="", sitelink_count=4, instance_of=[], score=4.0
    )
    points = [
        PageviewPoint(timestamp=f"2024{month:02d}0100", views=100 + month) for month in range(1, 13)
    ]

    def fake_resolve_entity(client: httpx.Client, topic: str) -> EntityResolution:
        return EntityResolution(status="ok", candidates=[top], confidence="high")

    def fake_resolve_language(client: httpx.Client, qid: str, lang: str) -> LanguageResolution:
        return LanguageResolution(status="ok", lang=lang, title="x")

    def fake_fetch_per_article_views(
        client: httpx.Client,
        cache: PageviewsCache,
        project: str,
        article: str,
        start: str,
        end: str,
        granularity: str,
    ) -> FetchResult:
        return FetchResult(status="ok", points=points)

    def fake_fetch_aggregate_views(
        client: httpx.Client,
        cache: PageviewsCache,
        project: str,
        start: str,
        end: str,
        granularity: str,
    ) -> FetchResult:
        aggregate_points = [PageviewPoint(timestamp=p.timestamp, views=10_000) for p in points]
        return FetchResult(status="ok", points=aggregate_points)

    monkeypatch.setattr(cli, "resolve_entity", fake_resolve_entity)
    monkeypatch.setattr(cli, "resolve_language", fake_resolve_language)
    monkeypatch.setattr(cli, "fetch_per_article_views", fake_fetch_per_article_views)
    monkeypatch.setattr(cli, "fetch_aggregate_views", fake_fetch_aggregate_views)

    response = cli._cmd_research("run_1", "topic", ["uk"], 24, None)

    assert response["status"] == "ok"
    assert response["entity"]["qid"] == "Q1"
    assert response["languages"]["uk"]["analysis"] is not None
    assert response["out"] is None
    assert any("wikitrends report" in step for step in response["next_steps"])

    # The returned payload is built from an in-memory dict, not re-read from disk -- a
    # caller that re-loads the run later (a refinement turn, or this eval harness's own
    # post-hoc check) depends on the *persisted* manifest matching it exactly.
    on_disk = load_manifest("run_1")
    assert on_disk is not None
    assert "uk" in on_disk.languages
    assert on_disk.languages["uk"].analysis is not None


def test_cmd_research_with_out_renders_a_pdf(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    top = EntityCandidate(
        qid="Q1", label="topic", description="", sitelink_count=4, instance_of=[], score=4.0
    )
    points = [
        PageviewPoint(timestamp=f"2024{month:02d}0100", views=100 + month) for month in range(1, 13)
    ]

    def fake_resolve_entity(client: httpx.Client, topic: str) -> EntityResolution:
        return EntityResolution(status="ok", candidates=[top], confidence="high")

    def fake_resolve_language(client: httpx.Client, qid: str, lang: str) -> LanguageResolution:
        return LanguageResolution(status="ok", lang=lang, title="x")

    def fake_fetch_per_article_views(
        client: httpx.Client,
        cache: PageviewsCache,
        project: str,
        article: str,
        start: str,
        end: str,
        granularity: str,
    ) -> FetchResult:
        return FetchResult(status="ok", points=points)

    def fake_fetch_aggregate_views(
        client: httpx.Client,
        cache: PageviewsCache,
        project: str,
        start: str,
        end: str,
        granularity: str,
    ) -> FetchResult:
        aggregate_points = [PageviewPoint(timestamp=p.timestamp, views=10_000) for p in points]
        return FetchResult(status="ok", points=aggregate_points)

    monkeypatch.setattr(cli, "resolve_entity", fake_resolve_entity)
    monkeypatch.setattr(cli, "resolve_language", fake_resolve_language)
    monkeypatch.setattr(cli, "fetch_per_article_views", fake_fetch_per_article_views)
    monkeypatch.setattr(cli, "fetch_aggregate_views", fake_fetch_aggregate_views)

    out_path = tmp_path / "report.pdf"
    response = cli._cmd_research("run_1", "topic", ["uk"], 24, str(out_path))

    assert response["status"] == "ok"
    assert response["out"] == str(out_path)
    assert out_path.exists()
    assert out_path.read_bytes().startswith(b"%PDF")

    # render_report() tolerates an empty manifest.languages (it just draws a placeholder),
    # so a valid PDF alone doesn't prove resolve/fetch/analyze actually persisted "uk" --
    # that needs its own assertion against the on-disk manifest.
    on_disk = load_manifest("run_1")
    assert on_disk is not None
    assert "uk" in on_disk.languages
    assert on_disk.languages["uk"].analysis is not None


def test_cmd_research_stops_early_and_surfaces_ambiguity(monkeypatch: pytest.MonkeyPatch) -> None:
    close_candidates = [
        EntityCandidate(
            qid="Q1", label="a", description="", sitelink_count=5, instance_of=[], score=5.0
        ),
        EntityCandidate(
            qid="Q2", label="b", description="", sitelink_count=4, instance_of=[], score=4.0
        ),
    ]

    def fake_resolve_entity(client: httpx.Client, topic: str) -> EntityResolution:
        return EntityResolution(status="ok", candidates=close_candidates, confidence="low")

    monkeypatch.setattr(cli, "resolve_entity", fake_resolve_entity)

    response = cli._cmd_research("run_1", "topic", ["uk"], 24, None)

    assert response["status"] == "ambiguous"


# --------------------------------------------------------------------------------------
# main() / _dispatch integration
# --------------------------------------------------------------------------------------


def test_main_prints_one_compact_json_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def fake_resolve_entity(client: httpx.Client, topic: str) -> EntityResolution:
        return EntityResolution(status="no_match")

    monkeypatch.setattr(cli, "resolve_entity", fake_resolve_entity)

    exit_code = cli.main(
        ["--log-dir", str(tmp_path / "logs"), "resolve", "--topic", "gibberish", "--langs", "uk"]
    )

    # Only stdout carries the JSON contract; structlog's human-readable sink is stderr, but
    # pytest's log-capturing plugin can still echo root-logger records into captured stdout,
    # so this checks the contract that actually matters: the last line is valid JSON.
    out = capsys.readouterr().out
    last_line = out.strip().splitlines()[-1]
    payload = json.loads(last_line)

    assert exit_code == 0
    assert last_line.startswith("{") and last_line.endswith("}")
    assert payload["status"] == "no_match"


def test_main_returns_exit_code_1_on_error(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    exit_code = cli.main(["--log-dir", str(tmp_path / "logs"), "fetch", "--run", "does_not_exist"])

    out = capsys.readouterr().out
    last_line = out.strip().splitlines()[-1]
    payload = json.loads(last_line)

    assert exit_code == 1
    assert payload["status"] == "error"
