"""Render one research run's analysis to a single-page PDF.

IMPLEMENTATION_PLAN.md §11 step 6: a chart of each language's relative-share series, a
verdict bullet per language derived directly from analyze.py's trust rules, and the
standing assumptions/limitations block. Every number here is read from the run manifest
that analyze.py already computed and persisted -- report.py performs no analysis of its
own, so every figure in the PDF is traceable back to the analysis JSON (R7).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from matplotlib.axes import Axes
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure

from wikitrends.workspace import LanguageRecord, RunManifest

# A "low" verdict must never read like a confident recommendation (IMPLEMENTATION_PLAN.md
# §8 deterministic assertions): the qualifier text is the only place that phrasing lives.
_TRUST_QUALIFIER = {
    "high": "high confidence",
    "medium": "medium confidence -- verify independently before acting on it",
    "low": "low confidence -- treat as indicative only, not a recommendation",
}


def render_report(manifest: RunManifest, caveats: list[str], out_path: Path) -> None:
    """Write a single-page PDF summarizing ``manifest`` to ``out_path``."""
    fig = Figure(figsize=(8.5, 11))

    title = manifest.entity_label or manifest.topic
    fig.text(0.08, 0.96, f"Wikipedia topic-trend report: {title}", fontsize=16, weight="bold")
    fig.text(
        0.08,
        0.935,
        f"run {manifest.run_id} -- {manifest.months} months, {manifest.granularity} buckets",
        fontsize=9,
        color="dimgray",
    )

    ax = fig.add_axes((0.08, 0.56, 0.87, 0.33))
    _draw_chart(ax, manifest)

    fig.text(0.08, 0.50, "Verdict", fontsize=13, weight="bold")
    fig.text(0.08, 0.475, _verdict_block(manifest), fontsize=9, va="top")

    fig.text(0.08, 0.22, "Assumptions & limitations", fontsize=13, weight="bold")
    fig.text(0.08, 0.195, "\n".join(f"- {c}" for c in caveats), fontsize=8, va="top")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(out_path) as pdf:
        pdf.savefig(fig)


def _draw_chart(ax: Axes, manifest: RunManifest) -> None:
    plotted = False
    for lang, record in sorted(manifest.languages.items()):
        if record.series:
            ax.plot(range(len(record.series)), record.series, label=lang)
            plotted = True

    ax.set_xlabel(f"{manifest.granularity} bucket")
    ax.set_ylabel("relative share (article / project total)")
    if plotted:
        ax.legend(loc="upper left", fontsize=8)
    else:
        ax.text(
            0.5,
            0.5,
            "No analyzable series for this run",
            ha="center",
            va="center",
            transform=ax.transAxes,
        )


def _verdict_block(manifest: RunManifest) -> str:
    lines = [_verdict_line(lang, record) for lang, record in sorted(manifest.languages.items())]
    return "\n".join(lines)


def _verdict_line(lang: str, record: LanguageRecord) -> str:
    if record.title is None:
        return f"{lang}: no article exists for this concept -- demand cannot be measured directly."
    if record.analysis is None:
        return f"{lang} ({record.title}): not enough analyzed data yet to compute a trend."

    analysis: dict[str, Any] = record.analysis
    direction = analysis["mk"]["direction"]
    trend_phrase = direction if direction == "no trend" else f"{direction} trend"
    pct_per_year = analysis["ols"]["percent_per_year"]
    trust_level = analysis["trust"]["level"]
    qualifier = _TRUST_QUALIFIER[trust_level]
    label = f"[proxy article: {record.title}]" if record.is_proxy else f"({record.title})"
    return f"{lang} {label}: {trend_phrase}, {pct_per_year:+.1f}%/year -- {qualifier}."
