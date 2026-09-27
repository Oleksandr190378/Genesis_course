"""Offline tests for evals/judge.py's deterministic scoring (no model/API calls).

These cover the parts of the eval harness that do not depend on a live provider: Tier 0
precision/recall/F1 arithmetic, Tier 1 CLI-argument validation against the real
``wikitrends.cli`` parser, and Tier 3's deterministic assertions against a manifest. The
LLM-as-judge path (``judge_output_quality``) and the harness's agent loop
(``evals/harness.py``) need a real provider and are exercised manually via
``uv run python -m evals.run_evals``, not here.
"""

from __future__ import annotations

from evals.judge import (
    ActivationAttempt,
    TriggerCaseResult,
    check_e2e_assertions,
    parse_cli_command,
    precision_recall_f1,
    score_activation_attempt,
    trigger_confusion,
    trigger_pass_rate,
)
from wikitrends.workspace import LanguageRecord, RunManifest

TARGET_SKILL = "wikipedia-topic-trends"

# --------------------------------------------------------------------------------------
# Tier 0
# --------------------------------------------------------------------------------------


def test_trigger_pass_rate_counts_matches_for_a_positive_case() -> None:
    result = TriggerCaseResult(
        case_id="pos_1",
        category="positive",
        expected="trigger",
        chosen_per_repeat=[TARGET_SKILL, "none", TARGET_SKILL],
    )
    assert trigger_pass_rate(result, TARGET_SKILL) == (2, 3)


def test_trigger_pass_rate_counts_matches_for_a_negative_case() -> None:
    # Choosing a different skill ("wikipedia-editor") is still a correct outcome for a
    # no_trigger case: only actually firing the target skill counts against it.
    result = TriggerCaseResult(
        case_id="neg_1",
        category="hard_negative",
        expected="no_trigger",
        chosen_per_repeat=["wikipedia-editor", "none", TARGET_SKILL],
    )
    assert trigger_pass_rate(result, TARGET_SKILL) == (2, 3)


def test_trigger_confusion_and_precision_recall_f1() -> None:
    results = [
        TriggerCaseResult("pos_1", "positive", "trigger", [TARGET_SKILL, TARGET_SKILL]),
        TriggerCaseResult("neg_1", "hard_negative", "no_trigger", [TARGET_SKILL, "none"]),
    ]
    confusion = trigger_confusion(results, TARGET_SKILL)
    assert confusion == {"tp": 2, "fp": 1, "fn": 0, "tn": 1}

    metrics = precision_recall_f1(confusion)
    assert metrics["precision"] == 2 / 3
    assert metrics["recall"] == 1.0
    assert round(metrics["f1"], 4) == round(2 * (2 / 3) / (2 / 3 + 1), 4)


def test_precision_recall_f1_handles_no_positives_without_dividing_by_zero() -> None:
    confusion = {"tp": 0, "fp": 0, "fn": 0, "tn": 5}
    assert precision_recall_f1(confusion) == {"precision": 0.0, "recall": 0.0, "f1": 0.0}


# --------------------------------------------------------------------------------------
# Tier 1: CLI parsing against the real argparse schema
# --------------------------------------------------------------------------------------


def test_parse_cli_command_accepts_a_well_formed_research_call() -> None:
    parsed = parse_cli_command('wikitrends research --topic "astronomy" --langs uk --months 24')
    assert parsed.ok
    assert parsed.command == "research"
    assert parsed.args is not None
    assert parsed.args["topic"] == "astronomy"
    assert parsed.args["langs"] == "uk"
    assert parsed.args["months"] == 24


def test_parse_cli_command_strips_a_uv_run_prefix() -> None:
    parsed = parse_cli_command("uv run wikitrends research --topic astronomy --langs uk")
    assert parsed.ok
    assert parsed.command == "research"


def test_parse_cli_command_flags_a_hallucinated_flag() -> None:
    command = "wikitrends research --topic astronomy --langs uk --granularity weekly"
    parsed = parse_cli_command(command)
    assert not parsed.ok
    assert parsed.hallucinated_flag


def test_parse_cli_command_missing_required_arg_is_invalid_but_not_hallucinated() -> None:
    parsed = parse_cli_command("wikitrends research --langs uk")
    assert not parsed.ok
    assert not parsed.hallucinated_flag


def test_parse_cli_command_with_no_subcommand_is_invalid() -> None:
    parsed = parse_cli_command("please plot this chart for me")
    assert not parsed.ok
    assert parsed.command is None
    assert parsed.error == "no recognized wikitrends subcommand"


# --------------------------------------------------------------------------------------
# Tier 1: attempt scoring
# --------------------------------------------------------------------------------------


def test_score_activation_attempt_matches_command_topic_and_langs() -> None:
    expected = {"command": "research", "topic_contains": "astronomy", "langs": ["uk"]}
    attempt = ActivationAttempt(
        proposed_command="wikitrends research --topic astronomy --langs uk",
        turns_used=1,
        read_reference_count=0,
    )
    scored = score_activation_attempt(expected, attempt)
    assert scored["command_correct"] is True
    assert scored["args_valid"] is True


def test_score_activation_attempt_rejects_wrong_languages() -> None:
    expected = {"command": "research", "langs": ["pl", "cs"]}
    attempt = ActivationAttempt(
        proposed_command="wikitrends research --topic fasting --langs uk",
        turns_used=1,
        read_reference_count=0,
    )
    scored = score_activation_attempt(expected, attempt)
    assert scored["command_correct"] is False


def test_score_activation_attempt_unsupported_case_passes_when_model_abstains() -> None:
    expected = {"command": None, "unsupported": True}
    attempt = ActivationAttempt(proposed_command=None, turns_used=2, read_reference_count=1)
    scored = score_activation_attempt(expected, attempt)
    assert scored["command_correct"] is True


def test_score_activation_attempt_unsupported_case_fails_on_hallucinated_flag() -> None:
    expected = {"command": None, "unsupported": True}
    attempt = ActivationAttempt(
        proposed_command="wikitrends analyze --run run_1 --granularity weekly",
        turns_used=1,
        read_reference_count=0,
    )
    scored = score_activation_attempt(expected, attempt)
    assert scored["command_correct"] is False
    assert scored["hallucinated_flag"] is True


# --------------------------------------------------------------------------------------
# Tier 3: deterministic assertions
# --------------------------------------------------------------------------------------


def _manifest_with_two_languages() -> RunManifest:
    manifest = RunManifest(
        run_id="run_1", topic="intermittent fasting", months=24, granularity="monthly"
    )
    manifest.languages["pl"] = LanguageRecord(
        lang="pl",
        resolution_status="missing",
        title="Post",
        is_proxy=True,
        analysis={"trust": {"level": "low", "reasons": ["proxy-measured: capped at low trust"]}},
    )
    manifest.languages["cs"] = LanguageRecord(
        lang="cs",
        resolution_status="ok",
        title="Přerušovaný půst",
        analysis={"trust": {"level": "high", "reasons": ["..."]}},
    )
    return manifest


def test_check_e2e_assertions_all_pass_on_a_matching_manifest() -> None:
    manifest = _manifest_with_two_languages()
    assertions = {
        "pdf_produced": True,
        "langs_present": ["pl", "cs"],
        "proxy_flagged_for": ["pl"],
        "trust_low_for": ["pl"],
        "trust_high_for": ["cs"],
    }
    results = check_e2e_assertions(
        assertions,
        manifest=manifest,
        pdf_ok=True,
        fetch_calls=[],
        hallucinated_flag_seen=False,
        crashed=False,
    )
    assert all(results.values()), results


def test_check_e2e_assertions_fails_when_pdf_missing() -> None:
    manifest = _manifest_with_two_languages()
    results = check_e2e_assertions(
        {"pdf_produced": True},
        manifest=manifest,
        pdf_ok=False,
        fetch_calls=[],
        hallucinated_flag_seen=False,
        crashed=False,
    )
    assert results["pdf_produced"] is False


def test_check_e2e_assertions_no_refetch_for_detects_a_double_fetch() -> None:
    manifest = _manifest_with_two_languages()
    fetch_calls = ["cs.wikipedia/Přerušovaný půst", "cs.wikipedia/Přerušovaný půst"]
    results = check_e2e_assertions(
        {"no_refetch_for": ["cs"]},
        manifest=manifest,
        pdf_ok=True,
        fetch_calls=fetch_calls,
        hallucinated_flag_seen=False,
        crashed=False,
    )
    assert results["no_refetch_for"] is False


def test_check_e2e_assertions_no_hallucinated_flag_and_no_crash() -> None:
    results = check_e2e_assertions(
        {"no_hallucinated_flag": True, "no_crash": True},
        manifest=None,
        pdf_ok=False,
        fetch_calls=[],
        hallucinated_flag_seen=False,
        crashed=False,
    )
    assert results == {"no_hallucinated_flag": True, "no_crash": True}
