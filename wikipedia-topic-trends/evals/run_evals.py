"""CLI entry point for the eval suite (IMPLEMENTATION_PLAN.md §8.2).

Run as a module from the project root -- ``uv run python -m evals.run_evals --tier 0`` runs
just Tier 0 (cheapest, meant to run often); ``--tier all`` (the default) runs everything
available. Writes ``docs/EVAL_RESULTS_N.md``. (Running it as a script, ``python
evals/run_evals.py``, puts ``evals/`` itself on ``sys.path`` instead of the project root, so
``from evals import ...`` fails -- ``evals`` is deliberately not part of the installed
``wikitrends`` wheel, see PROGRESS.md step 8.)

Token/cost totals and a diff against the previous run (both named in §8.2) are not implemented
yet -- ``evals.providers.ChatResult`` does not carry usage data yet. See PROGRESS.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from evals import harness, judge
from evals.providers import Provider, available_providers, judge_provider

DOCS_DIR = Path(__file__).parent.parent / "docs"


def _next_results_path() -> Path:
    """Next unused ``docs/EVAL_RESULTS_N.md`` path, so each run keeps its own history."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    existing_n = [
        int(path.stem.rsplit("_", 1)[-1])
        for path in DOCS_DIR.glob("EVAL_RESULTS_*.md")
        if path.stem.rsplit("_", 1)[-1].isdigit()
    ]
    return DOCS_DIR / f"EVAL_RESULTS_{max(existing_n, default=0) + 1}.md"


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # picks up ANTHROPIC_API_KEY/OPENAI_API_KEY from a .env in the project root
    parser = argparse.ArgumentParser(description="Run the wikipedia-topic-trends eval suite.")
    parser.add_argument("--tier", choices=["0", "1", "2", "3", "all"], default="all")
    args = parser.parse_args(argv)
    tiers = {"0", "1", "2", "3"} if args.tier == "all" else {args.tier}

    providers = available_providers()
    if not providers:
        print(
            "evals: no provider API keys set (ANTHROPIC_API_KEY / OPENAI_API_KEY); nothing to run.",
            file=sys.stderr,
        )
        return 1

    trigger_data = harness.load_yaml("trigger.yaml") if "0" in tiers else None
    activation_data = harness.load_yaml("activation.yaml") if "1" in tiers else None
    e2e_data = harness.load_yaml("e2e.yaml") if tiers & {"2", "3"} else None
    skill_md = harness.load_skill_md() if activation_data or e2e_data else None

    sections: list[str] = []
    for provider in providers:
        print(f"=== running {provider.name} ({provider.model}) ===", file=sys.stderr)
        if trigger_data is not None:
            sections.append(_render_tier0(provider, trigger_data))
        if activation_data is not None:
            assert skill_md is not None
            sections.append(_render_tier1(provider, activation_data, skill_md))
        if e2e_data is not None:
            assert skill_md is not None
            sections.append(_render_tier2(provider, e2e_data, skill_md, include_tier3="3" in tiers))

    report = "# Eval results\n\n" + "\n\n".join(sections) + "\n"
    out_path = _next_results_path()
    out_path.write_text(report, encoding="utf-8")
    print(f"wrote {out_path}", file=sys.stderr)
    return 0


def _render_tier0(provider: Provider, data: dict[str, Any]) -> str:
    results = harness.run_tier0(provider, data["cases"], data["catalog"])
    confusion = judge.trigger_confusion(results, data["target_skill"])
    metrics = judge.precision_recall_f1(confusion)

    lines = [
        f"## Tier 0 (discovery) -- {provider.name} ({provider.model})",
        "",
        "| case | category | expected | k/3 | chosen (when < 3/3) |",
        "|---|---|---|---|---|",
    ]
    for result in results:
        k, n = judge.trigger_pass_rate(result, data["target_skill"])
        chosen = ", ".join(result.chosen_per_repeat) if k < n else "-"
        lines.append(
            f"| {result.case_id} | {result.category} | {result.expected} | {k}/{n} | {chosen} |"
        )
    lines.append("")
    lines.append(
        f"Precision: {metrics['precision']:.2f}  Recall: {metrics['recall']:.2f}  "
        f"F1: {metrics['f1']:.2f}  "
        f"(TP={confusion['tp']} FP={confusion['fp']} FN={confusion['fn']} TN={confusion['tn']})"
    )
    return "\n".join(lines)


def _render_tier1(provider: Provider, data: dict[str, Any], skill_md: str) -> str:
    attempts_by_case = harness.run_tier1(provider, data["cases"], skill_md)
    cases_by_id = {case["id"]: case for case in data["cases"]}

    lines = [
        f"## Tier 1 (activation) -- {provider.name} ({provider.model})",
        "",
        "| case | command k/3 | hallucinated flag | avg turns | avg reference reads |",
        "|---|---|---|---|---|",
    ]
    for case_id, attempts in attempts_by_case.items():
        expected = cases_by_id[case_id]["expected"]
        scored = [judge.score_activation_attempt(expected, attempt) for attempt in attempts]
        k = sum(1 for s in scored if s["command_correct"])
        hallucinated = any(s["hallucinated_flag"] for s in scored)
        avg_turns = sum(a.turns_used for a in attempts) / len(attempts)
        avg_reads = sum(a.read_reference_count for a in attempts) / len(attempts)
        lines.append(
            f"| {case_id} | {k}/{len(scored)} | {'yes' if hallucinated else 'no'} | "
            f"{avg_turns:.1f} | {avg_reads:.1f} |"
        )
    return "\n".join(lines)


def _render_tier2(
    provider: Provider, data: dict[str, Any], skill_md: str, *, include_tier3: bool
) -> str:
    scenarios = data["scenarios"]
    runs_by_scenario = harness.run_tier2(provider, scenarios, skill_md)
    scenarios_by_id = {scenario["id"]: scenario for scenario in scenarios}
    judge_model = judge_provider() if include_tier3 else None

    lines = [f"## Tier 2/3 (execution / output quality) -- {provider.name} ({provider.model})", ""]
    for scenario_id, runs in runs_by_scenario.items():
        scenario = scenarios_by_id[scenario_id]
        lines.append(f"### {scenario_id}")
        lines.append("")
        lines.append("| repeat | assertions | tool calls | judge scores |")
        lines.append("|---|---|---|---|")
        for i, run in enumerate(runs, start=1):
            checks = judge.check_e2e_assertions(
                scenario["assertions"],
                manifest=run.manifest,
                pdf_ok=run.pdf_ok,
                fetch_calls=run.fetch_calls,
                hallucinated_flag_seen=run.hallucinated_flag_seen,
                crashed=run.crashed,
            )
            judge_scores = None
            if judge_model is not None and run.manifest is not None:
                reference = {lang: rec.analysis for lang, rec in run.manifest.languages.items()}
                judge_scores = judge.judge_output_quality(
                    judge_model, scenario["rubric"], run.final_text, reference
                )
            lines.append(
                f"| {i} | {sum(checks.values())}/{len(checks)} ({_failed_summary(checks)}) | "
                f"{run.tool_call_count} | {_format_judge_scores(judge_scores)} |"
            )
        lines.append("")
    return "\n".join(lines)


def _failed_summary(checks: dict[str, bool]) -> str:
    failed = [key for key, passed in checks.items() if not passed]
    return "all pass" if not failed else "failed: " + ", ".join(failed)


def _format_judge_scores(scores: dict[str, Any] | None) -> str:
    if scores is None:
        return "-"
    dimensions = (
        "decision_addressed",
        "caveats_preserved",
        "concrete_recommendation",
        "unsupported_claims_absent",
    )
    return ", ".join(f"{dim}={scores.get(dim, '?')}" for dim in dimensions)


if __name__ == "__main__":
    raise SystemExit(main())
