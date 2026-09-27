"""Tests for report.py: render_report is pure (manifest + caveats in, PDF file out).

No network or cache mocking needed here -- report.py never touches either. The
deterministic assertions from IMPLEMENTATION_PLAN.md §8 (PDF exists, is exactly one page,
a "low" trust verdict never reads like a confident recommendation) are checked directly
against the rendered PDF via pypdf.
"""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from wikitrends.report import render_report
from wikitrends.workspace import (
    LanguageRecord,
    RunManifest,
    create_manifest,
    with_entity,
    with_language,
)

_ANALYSIS_HIGH_TRUST = {
    "n_buckets": 24,
    "median_views": 500.0,
    "mk": {"direction": "increasing", "s": 10.0, "z": 2.5, "p_value": 0.01},
    "sens_slope_per_year": 60.0,
    "ols": {"percent_per_year": 25.0, "r_squared": 0.8},
    "spike_indices": [],
    "spike_dominant": False,
    "zero_run_max": 0,
    "seasonality_checked": True,
    "trust": {"level": "high", "reasons": ["clean trend"]},
}

_ANALYSIS_LOW_TRUST = {
    "n_buckets": 10,
    "median_views": 50.0,
    "mk": {"direction": "no trend", "s": 0.0, "z": 0.0, "p_value": 0.9},
    "sens_slope_per_year": 0.0,
    "ols": {"percent_per_year": 1.0, "r_squared": 0.05},
    "spike_indices": [],
    "spike_dominant": False,
    "zero_run_max": 0,
    "seasonality_checked": False,
    "trust": {"level": "low", "reasons": ["not statistically significant"]},
}


def _manifest_with_two_languages() -> RunManifest:
    manifest = create_manifest("run_1", "intermittent fasting", 24, "monthly")
    manifest = with_entity(manifest, "Q1666254", "intermittent fasting", "high")
    manifest = with_language(
        manifest,
        LanguageRecord(
            lang="en",
            resolution_status="ok",
            title="Intermittent fasting",
            fetched=True,
            series=[0.01 * i for i in range(1, 25)],
            analysis=_ANALYSIS_HIGH_TRUST,
            caveats=["Pageviews measure attention, not willingness to pay."],
        ),
    )
    manifest = with_language(
        manifest,
        LanguageRecord(
            lang="pl",
            resolution_status="missing",
            caveats=["No article exists for this concept in pl."],
        ),
    )
    return manifest


def test_render_report_writes_a_single_page_pdf(tmp_path: Path) -> None:
    manifest = _manifest_with_two_languages()
    out_path = tmp_path / "report.pdf"

    render_report(manifest, ["standing caveat one", "standing caveat two"], out_path)

    assert out_path.exists()
    reader = PdfReader(str(out_path))
    assert len(reader.pages) == 1


def test_render_report_includes_caveats_and_entity_label(tmp_path: Path) -> None:
    manifest = _manifest_with_two_languages()
    out_path = tmp_path / "report.pdf"
    caveats = ["a distinctive standing caveat"]

    render_report(manifest, caveats, out_path)

    text = PdfReader(str(out_path)).pages[0].extract_text()
    assert "intermittent fasting" in text
    assert "a distinctive standing caveat" in text
    assert "no article exists" in text.lower()


def test_render_report_never_phrases_a_low_trust_verdict_as_a_recommendation(
    tmp_path: Path,
) -> None:
    manifest = create_manifest("run_1", "topic", 12, "monthly")
    manifest = with_entity(manifest, "Q1", "topic", "high")
    manifest = with_language(
        manifest,
        LanguageRecord(
            lang="de",
            resolution_status="ok",
            title="Thema",
            fetched=True,
            series=[1.0, 2.0, 3.0],
            analysis=_ANALYSIS_LOW_TRUST,
            caveats=[],
        ),
    )
    out_path = tmp_path / "report.pdf"

    render_report(manifest, ["some caveat"], out_path)

    text = PdfReader(str(out_path)).pages[0].extract_text()
    assert "not a recommendation" in text
    assert "recommend" not in text.lower().replace("not a recommendation", "")


def test_render_report_creates_parent_directories(tmp_path: Path) -> None:
    manifest = _manifest_with_two_languages()
    out_path = tmp_path / "nested" / "dir" / "report.pdf"

    render_report(manifest, ["caveat"], out_path)

    assert out_path.exists()
