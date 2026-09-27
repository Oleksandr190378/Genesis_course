"""``wikitrends`` command-line entry point: argparse subcommands, JSON in / JSON out.

IMPLEMENTATION_PLAN.md §3.4/§5: stdout carries only compact JSON (the contract a calling
agent parses); every diagnostic goes to stderr via structlog. Every response carries
``status``, ``run_id``, ``caveats[]`` and ``next_steps[]`` so a weak model can proceed
without re-reading ``SKILL.md``, and a failure returns a structured ``{"status": "error",
"problem": ..., "fix": ...}`` instead of a traceback on stdout.

``research`` is the one-shot happy path (resolve -> fetch -> analyze -> optional report in
one run); ``resolve``, ``fetch``, ``analyze`` and ``report`` are the composable path for
refinements, sharing state through the run manifest in :mod:`wikitrends.workspace`.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path
from typing import Any

from wikitrends.analyze import analyze_series, relative_share
from wikitrends.cache import PageviewsCache
from wikitrends.caveats import build_caveats
from wikitrends.config import CACHE_DIR_NAME, DEFAULT_GRANULARITY, DEFAULT_MONTHS
from wikitrends.fetch import (
    align_by_timestamp,
    build_http_client,
    fetch_aggregate_views,
    fetch_per_article_views,
)
from wikitrends.logging import configure_logging, get_logger
from wikitrends.report import render_report
from wikitrends.resolve import resolve_entity, resolve_language
from wikitrends.workspace import (
    LanguageRecord,
    RunManifest,
    create_manifest,
    load_manifest,
    mark_fetched,
    new_run_id,
    save_manifest,
    with_entity,
    with_language,
)

logger = get_logger(component="cli")


def build_parser() -> argparse.ArgumentParser:
    """Build the ``wikitrends`` argparse parser: one binary, one ``--help`` surface."""
    parser = argparse.ArgumentParser(
        prog="wikitrends", description="Wikipedia topic-trend research"
    )
    parser.add_argument(
        "--log-level", default=None, help="Override WIKITRENDS_LOG_LEVEL for this run."
    )
    parser.add_argument(
        "--log-dir", default=None, help="Override the default .wikitrends/logs directory."
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    research = subparsers.add_parser(
        "research", help="One-shot: topic -> resolve -> fetch -> analyze."
    )
    research.add_argument("--topic", required=True)
    research.add_argument(
        "--langs", required=True, help="Comma-separated language codes, e.g. uk,de."
    )
    research.add_argument("--months", type=int, default=DEFAULT_MONTHS)
    research.add_argument("--out", default=None, help="Optional PDF path for the rendered report.")

    resolve_p = subparsers.add_parser(
        "resolve", help="Resolve a topic to a Wikidata entity and per-language titles."
    )
    resolve_p.add_argument(
        "--topic", default=None, help="Required unless --run reuses an existing run."
    )
    resolve_p.add_argument(
        "--langs",
        default=None,
        help="Comma-separated language codes, e.g. uk,de. Required unless --run reuses an "
        "existing run.",
    )
    resolve_p.add_argument("--months", type=int, default=DEFAULT_MONTHS)
    resolve_p.add_argument(
        "--qid", default=None, help="Skip search; use this QID (disambiguation follow-up)."
    )
    resolve_p.add_argument(
        "--run", default=None, help="Reuse an existing run_id instead of starting a new one."
    )

    fetch_p = subparsers.add_parser("fetch", help="Fetch pageviews for a run's resolved languages.")
    fetch_p.add_argument("--run", required=True)
    fetch_p.add_argument(
        "--add-langs",
        default=None,
        help="Comma-separated language codes to resolve and fetch in addition.",
    )

    analyze_p = subparsers.add_parser(
        "analyze", help="Compute trend/trust for a run's fetched languages."
    )
    analyze_p.add_argument("--run", required=True)

    report_p = subparsers.add_parser("report", help="Render a run's analysis to a single-page PDF.")
    report_p.add_argument("--run", required=True)
    report_p.add_argument("--out", required=True)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse argv, dispatch to the matching subcommand, and print one compact JSON line."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "resolve" and not args.run and not (args.topic and args.langs):
        parser.error(
            "resolve: --topic and --langs are required unless --run reuses an existing run."
        )

    run_id = getattr(args, "run", None) or new_run_id()
    configure_logging(
        run_id,
        log_level=args.log_level,
        log_dir=Path(args.log_dir) if args.log_dir else None,
    )

    try:
        response = _dispatch(args, run_id)
    except Exception as exc:  # keeps stdout a valid JSON contract even on an unexpected bug
        logger.exception("command_failed", command=args.command)
        response = {
            "status": "error",
            "run_id": run_id,
            "problem": str(exc),
            "fix": "Check .wikitrends/logs for the full traceback and verify the "
            "command's arguments.",
            "caveats": [],
            "next_steps": [],
        }

    sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
    return 1 if response.get("status") == "error" else 0


def _dispatch(args: argparse.Namespace, run_id: str) -> dict[str, Any]:
    if args.command == "research":
        return _cmd_research(run_id, args.topic, _parse_langs(args.langs), args.months, args.out)
    if args.command == "resolve":
        langs = _parse_langs(args.langs) if args.langs else None
        return _cmd_resolve(run_id, args.topic, langs, args.months, args.qid)
    if args.command == "fetch":
        add_langs = _parse_langs(args.add_langs) if args.add_langs else []
        return _cmd_fetch(run_id, add_langs)
    if args.command == "analyze":
        return _cmd_analyze(run_id)
    if args.command == "report":
        return _cmd_report(run_id, args.out)
    # pragma: no cover - argparse enforces valid choices
    raise AssertionError(f"unhandled command: {args.command}")


def _cmd_resolve(
    run_id: str, topic: str | None, langs: list[str] | None, months: int, qid: str | None
) -> dict[str, Any]:
    """Resolve ``topic`` to a Wikidata entity, then that entity to a title per ``lang``.

    ``topic``/``langs`` may be omitted when ``--run`` reuses an existing manifest -- the
    original request (checked at the argparse level in :func:`main`) is already on file.
    """
    manifest = load_manifest(run_id) or create_manifest(
        run_id, topic or "", months, DEFAULT_GRANULARITY, requested_langs=langs or []
    )
    resolved_langs = langs if langs else manifest.requested_langs

    with build_http_client() as client:
        if qid is not None:
            # Disambiguation follow-up: the caller already saw the "ambiguous" candidates
            # and picked one, so there is no search to re-run.
            resolved_qid, label, confidence = qid, qid, "high"
        else:
            resolution = resolve_entity(client, topic or manifest.topic)
            if resolution.status == "no_match":
                save_manifest(manifest)
                return _response(
                    "no_match",
                    run_id,
                    caveats=["No Wikidata entity found for this topic."],
                    next_steps=[
                        "Retry `resolve` with a more specific or differently-worded --topic."
                    ],
                )
            if resolution.confidence == "low":
                save_manifest(manifest)
                return _response(
                    "ambiguous",
                    run_id,
                    candidates=[asdict(c) for c in resolution.candidates],
                    caveats=[
                        "The top Wikidata candidates are close in score; confirm which one "
                        "is correct."
                    ],
                    next_steps=[
                        f"Re-run `resolve --run {run_id} --qid <chosen QID>` using one of the "
                        "candidates above."
                    ],
                )
            top = resolution.candidates[0]
            resolved_qid, label, confidence = top.qid, top.label, resolution.confidence

        manifest = with_entity(manifest, resolved_qid, label, confidence)

        language_payload: dict[str, Any] = {}
        for lang in resolved_langs:
            lang_res = resolve_language(client, resolved_qid, lang)
            record = LanguageRecord(
                lang=lang,
                resolution_status=lang_res.status,
                title=lang_res.title,
                is_proxy=lang_res.is_proxy,
                proxy_qid=lang_res.proxy_qid,
                proxy_label=lang_res.proxy_label,
            )
            manifest = with_language(manifest, record)
            language_payload[lang] = asdict(record)

    save_manifest(manifest)
    return _response(
        "ok",
        run_id,
        entity={"qid": resolved_qid, "label": label, "confidence": confidence},
        languages=language_payload,
        caveats=_resolution_caveats(manifest),
        next_steps=[f"Run `wikitrends fetch --run {run_id}` to pull pageviews."],
    )


def _cmd_fetch(run_id: str, add_langs: list[str]) -> dict[str, Any]:
    """Fetch pageviews for every language already resolved on ``run_id``, plus ``add_langs``."""
    manifest = load_manifest(run_id)
    if manifest is None:
        return _unknown_run_error(run_id)
    if manifest.entity_qid is None:
        return _response(
            "error",
            run_id,
            problem="this run has no resolved entity yet",
            fix=f"Call `resolve --run {run_id} --topic ... --langs ...` first.",
        )

    start, end = _date_range(manifest.months)
    cache = PageviewsCache(Path(CACHE_DIR_NAME))
    language_payload: dict[str, Any] = {}

    with build_http_client() as client:
        for lang in dict.fromkeys([*manifest.languages, *add_langs]):
            record = manifest.languages.get(lang)
            if record is None:
                lang_res = resolve_language(client, manifest.entity_qid, lang)
                record = LanguageRecord(
                    lang=lang,
                    resolution_status=lang_res.status,
                    title=lang_res.title,
                    is_proxy=lang_res.is_proxy,
                    proxy_qid=lang_res.proxy_qid,
                    proxy_label=lang_res.proxy_label,
                )
                manifest = with_language(manifest, record)

            if record.title is not None and not record.fetched:
                project = f"{lang}.wikipedia"
                article_result = fetch_per_article_views(
                    client, cache, project, record.title, start, end, manifest.granularity
                )
                fetch_aggregate_views(client, cache, project, start, end, manifest.granularity)
                if article_result.status == "ok":
                    record = mark_fetched(record)
                    manifest = with_language(manifest, record)

            language_payload[lang] = asdict(record)

    save_manifest(manifest)
    return _response(
        "ok",
        run_id,
        languages=language_payload,
        caveats=_resolution_caveats(manifest),
        next_steps=[f"Run `wikitrends analyze --run {run_id}` to compute trends."],
    )


def _cmd_analyze(run_id: str) -> dict[str, Any]:
    """Compute relative-share trend/trust for every fetchable language on ``run_id``."""
    manifest = load_manifest(run_id)
    if manifest is None:
        return _unknown_run_error(run_id)

    start, end = _date_range(manifest.months)
    cache = PageviewsCache(Path(CACHE_DIR_NAME))
    language_payload: dict[str, Any] = {}

    with build_http_client() as client:
        for lang, record in manifest.languages.items():
            if record.title is None:
                record = replace(record, caveats=build_caveats(None, is_missing=True))
                manifest = with_language(manifest, record)
                language_payload[lang] = asdict(record)
                continue

            project = f"{lang}.wikipedia"
            article_result = fetch_per_article_views(
                client, cache, project, record.title, start, end, manifest.granularity
            )
            aggregate_result = fetch_aggregate_views(
                client, cache, project, start, end, manifest.granularity
            )

            article_values, aggregate_values = align_by_timestamp(
                article_result.points, aggregate_result.points
            )
            if len(article_values) < 2:
                language_payload[lang] = asdict(record)
                continue

            shares = relative_share(article_values, aggregate_values)
            result = analyze_series(shares, raw_views=article_values, is_proxy=record.is_proxy)
            record = replace(
                record,
                fetched=True,
                analysis=asdict(result),
                caveats=build_caveats(
                    result,
                    is_proxy=record.is_proxy,
                    truncated_leading=article_result.truncated_leading,
                    truncated_trailing=article_result.truncated_trailing,
                ),
                series=shares,
            )
            manifest = with_language(manifest, record)
            language_payload[lang] = asdict(record)

    save_manifest(manifest)
    return _response(
        "ok",
        run_id,
        languages=language_payload,
        caveats=_collect_caveats(manifest),
        next_steps=[f"Run `wikitrends report --run {run_id} --out report.pdf` to render a PDF."],
    )


def _cmd_report(run_id: str, out: str) -> dict[str, Any]:
    """Render a run's analysis to a single-page PDF at ``out``."""
    manifest = load_manifest(run_id)
    if manifest is None:
        return _unknown_run_error(run_id)
    if manifest.entity_qid is None:
        return _response(
            "error",
            run_id,
            problem="this run has no resolved entity yet",
            fix=f"Call `resolve --run {run_id} --topic ... --langs ...` first.",
        )

    caveats = _collect_caveats(manifest)
    out_path = Path(out)
    render_report(manifest, caveats, out_path)
    return _response(
        "ok",
        run_id,
        out=str(out_path),
        caveats=caveats,
        next_steps=[f"Open {out_path} to view the report."],
    )


def _cmd_research(
    run_id: str, topic: str, langs: list[str], months: int, out: str | None
) -> dict[str, Any]:
    """One-shot happy path: resolve -> fetch -> analyze -> optional report in a single run."""
    resolved = _cmd_resolve(run_id, topic, langs, months, qid=None)
    if resolved["status"] != "ok":
        return resolved

    fetched = _cmd_fetch(run_id, add_langs=[])
    if fetched["status"] != "ok":
        return fetched

    analyzed = _cmd_analyze(run_id)
    if analyzed["status"] != "ok":
        return analyzed

    report_out: str | None = None
    if out is not None:
        report_response = _cmd_report(run_id, out)
        if report_response["status"] != "ok":
            return report_response
        report_out = report_response["out"]
        next_steps = report_response["next_steps"]
    else:
        next_steps = [f"Run `wikitrends report --run {run_id} --out report.pdf` to render a PDF."]

    return _response(
        "ok",
        run_id,
        entity=resolved.get("entity"),
        languages=analyzed.get("languages"),
        out=report_out,
        caveats=list(analyzed.get("caveats", [])),
        next_steps=next_steps,
    )


def _unknown_run_error(run_id: str) -> dict[str, Any]:
    return _response(
        "error",
        run_id,
        problem=f"unknown run_id: {run_id}",
        fix="Call `resolve` first to start a run.",
    )


def _response(status: str, run_id: str, **extra: Any) -> dict[str, Any]:
    """Build a response dict guaranteed to carry status/run_id/caveats/next_steps."""
    payload: dict[str, Any] = {"status": status, "run_id": run_id, "caveats": [], "next_steps": []}
    payload.update(extra)
    return payload


def _parse_langs(raw: str) -> list[str]:
    return [lang.strip() for lang in raw.split(",") if lang.strip()]


def _resolution_caveats(manifest: RunManifest) -> list[str]:
    """Caveats knowable right after resolve/fetch, before any analysis has run."""
    caveats: list[str] = []
    for record in manifest.languages.values():
        if record.title is None:
            caveats.extend(build_caveats(None, is_missing=True))
        elif record.is_proxy:
            caveats.append(
                f"No direct {record.lang} article for this concept; using proxy article "
                f"'{record.title}' ({record.proxy_label})."
            )
    return _dedupe(caveats)


def _collect_caveats(manifest: RunManifest) -> list[str]:
    """Merge every language's analyze-stage caveats, deduped (standing caveats repeat/language)."""
    caveats: list[str] = []
    for record in manifest.languages.values():
        caveats.extend(record.caveats)
    return _dedupe(caveats)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def _date_range(months: int, today: date | None = None) -> tuple[str, str]:
    """The ``[start, end]`` window for the Wikimedia pageviews API, ``months`` back from today."""
    end = today or date.today()
    start = _shift_months(end, -months)
    return f"{start:%Y%m%d}00", f"{end:%Y%m%d}00"


def _shift_months(day: date, delta_months: int) -> date:
    """The first day of the month ``delta_months`` away from ``day``'s month."""
    month_index = day.month - 1 + delta_months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


if __name__ == "__main__":
    raise SystemExit(main())
