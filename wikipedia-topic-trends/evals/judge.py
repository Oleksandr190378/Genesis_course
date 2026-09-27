"""Deterministic checks + LLM-as-judge rubric scoring (IMPLEMENTATION_PLAN.md §8).

Two independent kinds of scoring live here:

- **Deterministic** (Tier 0 precision/recall/F1, Tier 1 CLI-argument validity, Tier 3
  assertions): cheap, objective, no model call. Tier 1 validity is checked by feeding the
  model's proposed command straight into the *real* ``wikitrends.cli.build_parser()`` --
  reusing the actual CLI contract instead of duplicating a flag list here means the eval can
  never silently drift from what the CLI really accepts.
- **LLM-as-judge** (Tier 3 output-quality rubric): the only part of this module that calls a
  model, and always the stronger judge model, never the model under test (§8, Tier 3).
"""

from __future__ import annotations

import contextlib
import io
import json
import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from evals.providers import Message, Provider, ToolSpec
from wikitrends import cli

if TYPE_CHECKING:
    from wikitrends.workspace import RunManifest

_SUBCOMMANDS = {"research", "resolve", "fetch", "analyze", "report"}

# --------------------------------------------------------------------------------------
# Tier 0: trigger precision / recall / F1
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TriggerCaseResult:
    """One trigger.yaml case, with the skill (or ``"none"``) chosen on each of the 3 repeats."""

    case_id: str
    category: str
    expected: str  # "trigger" | "no_trigger"
    chosen_per_repeat: list[str]


def trigger_pass_rate(result: TriggerCaseResult, target_skill: str) -> tuple[int, int]:
    """``(k, n)`` -- how many of the ``n`` repeats matched ``expected`` for this case."""
    matches = [
        _matches_expected(choice, result.expected, target_skill)
        for choice in result.chosen_per_repeat
    ]
    return sum(matches), len(matches)


def trigger_confusion(results: list[TriggerCaseResult], target_skill: str) -> dict[str, int]:
    """Confusion matrix over every repeat of every case, one prediction per repeat."""
    counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for result in results:
        for choice in result.chosen_per_repeat:
            fired = choice == target_skill
            if result.expected == "trigger":
                counts["tp" if fired else "fn"] += 1
            else:
                counts["fp" if fired else "tn"] += 1
    return counts


def precision_recall_f1(confusion: dict[str, int]) -> dict[str, float]:
    tp, fp, fn = confusion["tp"], confusion["fp"], confusion["fn"]
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def _matches_expected(choice: str, expected: str, target_skill: str) -> bool:
    fired = choice == target_skill
    return fired if expected == "trigger" else not fired


# --------------------------------------------------------------------------------------
# Tier 1: CLI command/argument validity
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedCommand:
    """The result of feeding a model-proposed command line through the real CLI parser."""

    raw: str
    ok: bool
    command: str | None = None
    args: dict[str, Any] | None = None
    error: str | None = None
    hallucinated_flag: bool = False


@dataclass(frozen=True)
class ActivationAttempt:
    """What the harness observed for one Tier 1 case attempt."""

    proposed_command: str | None
    turns_used: int
    read_reference_count: int


def parse_cli_command(command_text: str) -> ParsedCommand:
    """Validate a model-proposed ``wikitrends ...`` invocation against the real argparse schema."""
    tokens = shlex.split(command_text)
    sliced = slice_to_subcommand(tokens)
    if sliced is None:
        return ParsedCommand(
            raw=command_text, ok=False, error="no recognized wikitrends subcommand"
        )

    parser = cli.build_parser()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr):
            namespace = parser.parse_args(sliced)
    except SystemExit:
        message = stderr.getvalue().strip()
        return ParsedCommand(
            raw=command_text,
            ok=False,
            error=message,
            hallucinated_flag="unrecognized arguments" in message,
        )
    return ParsedCommand(raw=command_text, ok=True, command=namespace.command, args=vars(namespace))


def slice_to_subcommand(tokens: list[str]) -> list[str] | None:
    """Drop any invocation prefix (``uv run wikitrends`` / ``python -m wikitrends.cli`` / ...).

    Public so :mod:`evals.harness` can reuse it to rebuild ``argv`` with a harness-injected
    ``--log-dir`` prefix once :func:`parse_cli_command` has confirmed the rest is valid.
    """
    for i, token in enumerate(tokens):
        if token in _SUBCOMMANDS:
            return tokens[i:]
    return None


def score_activation_attempt(
    expected: dict[str, Any], attempt: ActivationAttempt
) -> dict[str, Any]:
    """Score one Tier 1 attempt against its case's ``expected`` block."""
    base = {
        "turns_to_first_valid_call": attempt.turns_used,
        "read_reference_count": attempt.read_reference_count,
    }

    if expected.get("command") is None:
        # Correct behavior is to recognize the limitation rather than invent a flag the real
        # CLI has no idea about; a command that fails for some *other* reason (or even one
        # that happens to be valid) is not the failure mode this case is checking for.
        if attempt.proposed_command is None:
            return {**base, "command_correct": True, "args_valid": True, "hallucinated_flag": False}
        parsed = parse_cli_command(attempt.proposed_command)
        correct = not parsed.hallucinated_flag
        return {
            **base,
            "command_correct": correct,
            "args_valid": correct,
            "hallucinated_flag": parsed.hallucinated_flag,
        }

    if attempt.proposed_command is None:
        return {**base, "command_correct": False, "args_valid": False, "hallucinated_flag": False}

    parsed = parse_cli_command(attempt.proposed_command)
    if not parsed.ok:
        return {
            **base,
            "command_correct": False,
            "args_valid": False,
            "hallucinated_flag": parsed.hallucinated_flag,
        }

    args = parsed.args or {}
    checks = [parsed.command == expected["command"]]
    if "topic_contains" in expected:
        checks.append(expected["topic_contains"].lower() in str(args.get("topic", "")).lower())
    if "langs" in expected:
        checks.append(_as_set(args.get("langs")) == set(expected["langs"]))
    if "months" in expected:
        checks.append(args.get("months") == expected["months"])
    if expected.get("out_required"):
        checks.append(bool(args.get("out")))
    if "run" in expected:
        checks.append(args.get("run") == expected["run"])
    if "add_langs" in expected:
        checks.append(_as_set(args.get("add_langs")) == set(expected["add_langs"]))
    if "qid" in expected:
        checks.append(args.get("qid") == expected["qid"])

    return {**base, "command_correct": all(checks), "args_valid": True, "hallucinated_flag": False}


def _as_set(raw: str | None) -> set[str]:
    return {part.strip() for part in (raw or "").split(",") if part.strip()}


# --------------------------------------------------------------------------------------
# Tier 3: deterministic output-quality assertions
# --------------------------------------------------------------------------------------


def check_e2e_assertions(
    assertions: dict[str, Any],
    *,
    manifest: RunManifest | None,
    pdf_ok: bool,
    fetch_calls: list[str],
    hallucinated_flag_seen: bool,
    crashed: bool,
) -> dict[str, bool]:
    """Evaluate the deterministic ``assertions`` block from an e2e.yaml scenario."""
    results: dict[str, bool] = {}

    if "pdf_produced" in assertions:
        results["pdf_produced"] = pdf_ok == assertions["pdf_produced"]

    if "langs_present" in assertions:
        present = set(manifest.languages) if manifest else set()
        results["langs_present"] = set(assertions["langs_present"]) <= present

    if "proxy_flagged_for" in assertions:
        results["proxy_flagged_for"] = _all_languages(
            manifest, assertions["proxy_flagged_for"], lambda record: record.is_proxy
        )

    if "trust_low_for" in assertions:
        results["trust_low_for"] = _all_languages(
            manifest, assertions["trust_low_for"], _is_trust_level("low")
        )

    if "trust_high_for" in assertions:
        results["trust_high_for"] = _all_languages(
            manifest, assertions["trust_high_for"], _is_trust_level("high")
        )

    if "no_refetch_for" in assertions:
        results["no_refetch_for"] = _no_refetch(manifest, assertions["no_refetch_for"], fetch_calls)

    if "no_hallucinated_flag" in assertions:
        expected_clean = assertions["no_hallucinated_flag"]
        results["no_hallucinated_flag"] = (not hallucinated_flag_seen) == expected_clean

    if "no_crash" in assertions:
        results["no_crash"] = (not crashed) == assertions["no_crash"]

    return results


def _all_languages(manifest: RunManifest | None, langs: list[str], predicate: Any) -> bool:
    if manifest is None:
        return False
    return all(lang in manifest.languages and predicate(manifest.languages[lang]) for lang in langs)


def _is_trust_level(level: str) -> Any:
    return lambda record: bool(record.analysis) and record.analysis["trust"]["level"] == level


def _no_refetch(manifest: RunManifest | None, langs: list[str], fetch_calls: list[str]) -> bool:
    if manifest is None:
        return False
    for lang in langs:
        record = manifest.languages.get(lang)
        if record is None or record.title is None:
            return False
        key = f"{lang}.wikipedia/{record.title}"
        if fetch_calls.count(key) > 1:
            return False
    return True


# --------------------------------------------------------------------------------------
# Tier 3: LLM-as-judge rubric
# --------------------------------------------------------------------------------------

_JUDGE_TOOL = ToolSpec(
    name="submit_scores",
    description="Submit rubric scores (1-5, 5=excellent) for the assistant's final answer.",
    input_schema={
        "type": "object",
        "properties": {
            "decision_addressed": {"type": "integer", "minimum": 1, "maximum": 5},
            "caveats_preserved": {"type": "integer", "minimum": 1, "maximum": 5},
            "concrete_recommendation": {"type": "integer", "minimum": 1, "maximum": 5},
            "unsupported_claims_absent": {"type": "integer", "minimum": 1, "maximum": 5},
            "rationale": {"type": "string"},
        },
        "required": [
            "decision_addressed",
            "caveats_preserved",
            "concrete_recommendation",
            "unsupported_claims_absent",
            "rationale",
        ],
    },
)

_JUDGE_SYSTEM = (
    "You are grading an AI assistant's final answer to a B2C founder who asked a "
    "Wikipedia-pageview-trend research question. Score each rubric dimension 1 (poor) to 5 "
    "(excellent) strictly from the rubric and the reference analysis JSON (ground truth "
    "numbers) provided. Call submit_scores exactly once."
)


def judge_output_quality(
    provider: Provider | None,
    rubric: str,
    final_text: str | None,
    reference_analysis: dict[str, Any],
) -> dict[str, Any] | None:
    """Score ``final_text`` on the Tier 3 rubric with the stronger judge model, or ``None``."""
    if provider is None or not final_text:
        return None

    user_content = (
        f"Rubric:\n{rubric}\n\n"
        f"Reference analysis JSON (ground truth):\n{json.dumps(reference_analysis)}\n\n"
        f"Assistant's final answer:\n{final_text}"
    )
    result = provider.complete(
        _JUDGE_SYSTEM, [Message(role="user", content=user_content)], tools=[_JUDGE_TOOL]
    )
    for call in result.tool_calls:
        if call.name == "submit_scores":
            return call.arguments
    return None
