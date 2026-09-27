"""Agent-loop harness: drives a :class:`~evals.providers.Provider` through Tiers 0-2.

IMPLEMENTATION_PLAN.md §8: agent behaviour is non-deterministic, so every case is run
``REPEATS`` (3) times; callers (``run_evals.py``) score the returned raw results as a pass
rate ``k/3``, never as a boolean.

Each tier hands the model a different, deliberately narrow set of tools standing in for what a
real host application would give it (see the conversation in this session for why: a real
agent gets its Skill-triggering and command-execution ability from its *host's* tool-calling
API, not from the Skill itself):

- Tier 0 -- ``select_skill``: the model never sees SKILL.md's body, only name+description for
  every skill in a decoy catalog, exactly like real skill discovery.
- Tier 1 -- ``read_reference`` + ``propose_command``: SKILL.md is the system prompt; nothing is
  actually executed, so this tier costs one model call per turn and no network/CLI risk.
- Tier 2 -- ``run_wikitrends``: the real ``wikitrends`` CLI runs in-process
  (``wikitrends.cli.main``) with the network boundary swapped for ``evals.fixtures``, closest
  to what a real Bash tool would do.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml

from evals import fixtures
from evals.judge import (
    ActivationAttempt,
    TriggerCaseResult,
    parse_cli_command,
    slice_to_subcommand,
)
from evals.providers import ChatResult, Message, Provider, ToolSpec
from wikitrends import cli
from wikitrends.logging import shutdown_logging
from wikitrends.workspace import RunManifest, load_manifest

REPEATS = 3
_SKILL_DIR = Path(__file__).parent.parent
CASES_DIR = Path(__file__).parent / "cases"
SKILL_MD_PATH = _SKILL_DIR / "SKILL.md"
REFERENCES_DIR = (_SKILL_DIR / "references").resolve()


def _log(message: str) -> None:
    """One short progress line to stderr -- these runs make several slow model calls each."""
    print(f"  {message}", file=sys.stderr, flush=True)


def load_yaml(name: str) -> dict[str, Any]:
    """Load one ``evals/cases/*.yaml`` file."""
    with (CASES_DIR / name).open(encoding="utf-8") as handle:
        loaded: dict[str, Any] = yaml.safe_load(handle)
        return loaded


def load_skill_md() -> str:
    return SKILL_MD_PATH.read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------
# Tier 0 -- discovery / trigger precision
# --------------------------------------------------------------------------------------


def _catalog_system_prompt(catalog: list[dict[str, Any]]) -> str:
    lines = [
        "You are an AI assistant with access to the following Skills. A skill is only "
        "invoked when it is clearly relevant to the user's message; most messages need none "
        "of the skills below.",
        "",
    ]
    for entry in catalog:
        lines.append(f"### {entry['name']}\n{entry['description']}\n")
    lines.append(
        "Given the user's message, decide which single skill (if any) applies. Call "
        '`select_skill` exactly once with the chosen skill\'s exact name, or "none" if no '
        "skill applies."
    )
    return "\n".join(lines)


def _select_skill_tool(catalog_names: list[str]) -> ToolSpec:
    return ToolSpec(
        name="select_skill",
        description="Record which skill (if any) should be invoked for the user's message.",
        input_schema={
            "type": "object",
            "properties": {"skill_name": {"type": "string", "enum": [*catalog_names, "none"]}},
            "required": ["skill_name"],
        },
    )


def run_tier0(
    provider: Provider,
    cases: list[dict[str, Any]],
    catalog: list[dict[str, Any]],
    repeats: int = REPEATS,
) -> list[TriggerCaseResult]:
    """One ``select_skill`` call per case per repeat; no multi-turn loop needed."""
    system = _catalog_system_prompt(catalog)
    tool = _select_skill_tool([entry["name"] for entry in catalog])
    results: list[TriggerCaseResult] = []
    _log(f"tier 0: {len(cases)} cases x {repeats} repeats")
    for i, case in enumerate(cases, start=1):
        _log(f"tier 0 [{i}/{len(cases)}] {case['id']}")
        chosen: list[str] = []
        for _ in range(repeats):
            response = provider.complete(
                system, [Message(role="user", content=case["query"])], tools=[tool]
            )
            choice = "none"
            for call in response.tool_calls:
                if call.name == "select_skill":
                    choice = str(call.arguments.get("skill_name", "none"))
                    break
            chosen.append(choice)
        results.append(
            TriggerCaseResult(
                case_id=case["id"],
                category=case["category"],
                expected=case["expected"],
                chosen_per_repeat=chosen,
            )
        )
    return results


# --------------------------------------------------------------------------------------
# Tier 1 -- activation / command selection (tools mocked, nothing executed)
# --------------------------------------------------------------------------------------

_READ_REFERENCE_TOOL = ToolSpec(
    name="read_reference",
    description="Read one references/*.md file for more detail than SKILL.md gives.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "enum": [
                    "references/METHODOLOGY.md",
                    "references/API_REFERENCE.md",
                    "references/TROUBLESHOOTING.md",
                ],
            }
        },
        "required": ["path"],
    },
)

_PROPOSE_COMMAND_TOOL = ToolSpec(
    name="propose_command",
    description=(
        "Give the exact `wikitrends ...` command line you would run next. Calling this ends "
        "the task -- it is recorded but not actually executed."
    ),
    input_schema={
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
)


def _flatten_context(context: list[dict[str, Any]], final_message: str) -> str:
    """Fold a replayed prior turn into one plain-text user message.

    Sending a synthetic ``tool`` role reply with no matching preceding ``tool_use`` block
    would be rejected by both APIs' message validation, so a refinement case's prior turn is
    narrated in plain text instead -- always valid, and the model only needs to read it, not
    parse a live tool-call protocol out of it.
    """
    parts = []
    for turn in context:
        label = "Earlier user message" if turn["role"] == "user" else "Earlier tool result JSON"
        parts.append(f"[{label}]: {turn['content'].strip()}")
    parts.append(f"[Current user message]: {final_message.strip()}")
    return "\n\n".join(parts)


def _assistant_turn(response: ChatResult) -> Message:
    """The assistant message for a tool-calling turn (``tool_calls`` is never empty here)."""
    return Message(
        role="assistant", content=response.text or "", tool_calls=tuple(response.tool_calls)
    )


def _read_reference_file(path: str) -> str:
    resolved = (_SKILL_DIR / path).resolve()
    if resolved.parent != REFERENCES_DIR or not resolved.exists():
        return f"error: no such reference file: {path}"
    return resolved.read_text(encoding="utf-8")


def run_tier1(
    provider: Provider,
    cases: list[dict[str, Any]],
    skill_md: str,
    repeats: int = REPEATS,
    max_turns: int = 4,
) -> dict[str, list[ActivationAttempt]]:
    tools = [_READ_REFERENCE_TOOL, _PROPOSE_COMMAND_TOOL]
    results: dict[str, list[ActivationAttempt]] = {}
    _log(f"tier 1: {len(cases)} cases x {repeats} repeats")
    for i, case in enumerate(cases, start=1):
        _log(f"tier 1 [{i}/{len(cases)}] {case['id']}")
        seed = _flatten_context(case.get("context", []), case["prompt"])
        results[case["id"]] = [
            _run_tier1_attempt(provider, skill_md, tools, seed, max_turns) for _ in range(repeats)
        ]
    return results


def _run_tier1_attempt(
    provider: Provider, skill_md: str, tools: list[ToolSpec], seed: str, max_turns: int
) -> ActivationAttempt:
    messages: list[Message] = [Message(role="user", content=seed)]
    proposed: str | None = None
    read_count = 0
    turns_used = 0
    for turn in range(1, max_turns + 1):
        turns_used = turn
        response = provider.complete(skill_md, messages, tools=tools)
        if not response.tool_calls:
            break
        messages.append(_assistant_turn(response))
        stop = False
        for call in response.tool_calls:
            if call.name == "propose_command":
                proposed = str(call.arguments.get("command", ""))
                messages.append(
                    Message(role="tool", content="recorded", tool_call_id=call.id, name=call.name)
                )
                stop = True
            elif call.name == "read_reference":
                read_count += 1
                content = _read_reference_file(str(call.arguments.get("path", "")))
                messages.append(
                    Message(role="tool", content=content, tool_call_id=call.id, name=call.name)
                )
        if stop:
            break
    return ActivationAttempt(
        proposed_command=proposed, turns_used=turns_used, read_reference_count=read_count
    )


# --------------------------------------------------------------------------------------
# Tier 2 -- execution / end-to-end (real cli.main(), network boundary faked)
# --------------------------------------------------------------------------------------

_RUN_WIKITRENDS_TOOL = ToolSpec(
    name="run_wikitrends",
    description=(
        "Execute one `wikitrends ...` command for real and get back its single-line JSON "
        "stdout (or a structured error if the command line was invalid). Call it as many "
        "times as needed -- typically no more than 6 -- to fulfill the user's request, then "
        "reply in plain text with no further tool call."
    ),
    input_schema={
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
)


@dataclass
class Tier2RunResult:
    """Everything Tier 3's deterministic checks + LLM judge need from one scenario run."""

    scenario_id: str
    final_text: str | None
    manifest: RunManifest | None
    pdf_ok: bool
    fetch_calls: list[str]
    hallucinated_flag_seen: bool
    crashed: bool
    tool_call_count: int


def run_tier2(
    provider: Provider, scenarios: list[dict[str, Any]], skill_md: str, repeats: int = REPEATS
) -> dict[str, list[Tier2RunResult]]:
    results: dict[str, list[Tier2RunResult]] = {}
    _log(f"tier 2: {len(scenarios)} scenarios x {repeats} repeats (slowest tier)")
    for i, scenario in enumerate(scenarios, start=1):
        _log(f"tier 2 [{i}/{len(scenarios)}] {scenario['id']}")
        runs = []
        for r in range(1, repeats + 1):
            _log(f"tier 2 [{i}/{len(scenarios)}] {scenario['id']} repeat {r}/{repeats}")
            runs.append(_run_tier2_scenario(provider, skill_md, scenario))
        results[scenario["id"]] = runs
    return results


def _run_tier2_scenario(
    provider: Provider, skill_md: str, scenario: dict[str, Any]
) -> Tier2RunResult:
    max_tool_calls = int(scenario.get("max_tool_calls", 6))
    hallucinated_seen = False
    crashed = False
    tool_call_count = 0
    final_text: str | None = None
    manifest: RunManifest | None = None
    pdf_ok = False
    fetch_calls: list[str] = []
    messages: list[Message] = []

    with tempfile.TemporaryDirectory(prefix="wikitrends-eval-") as tmp_name:
        tmp_path = Path(tmp_name)
        log_dir = tmp_path / "logs"
        original_cwd = Path.cwd()
        os.chdir(tmp_path)
        try:
            with (
                patch("wikitrends.workspace.RUNS_DIR_NAME", str(tmp_path / "runs")),
                patch("wikitrends.cli.CACHE_DIR_NAME", str(tmp_path / "cache")),
                fixtures.install_mocks(scenario["fixture"]) as article_fetch_calls,
            ):
                for turn_text in scenario["turns"]:
                    messages.append(Message(role="user", content=turn_text))
                    final_text = None
                    while tool_call_count < max_tool_calls:
                        response = provider.complete(
                            skill_md, messages, tools=[_RUN_WIKITRENDS_TOOL]
                        )
                        if not response.tool_calls:
                            final_text = response.text
                            messages.append(Message(role="assistant", content=response.text or ""))
                            break
                        messages.append(_assistant_turn(response))
                        for call in response.tool_calls:
                            tool_call_count += 1
                            command_text = str(call.arguments.get("command", ""))
                            _log(f"    -> {command_text}")
                            output, halluc, did_crash = _execute_wikitrends_command(
                                command_text, log_dir
                            )
                            hallucinated_seen = hallucinated_seen or halluc
                            crashed = crashed or did_crash
                            messages.append(
                                Message(
                                    role="tool",
                                    content=output,
                                    tool_call_id=call.id,
                                    name=call.name,
                                )
                            )

                run_id = _last_run_id(messages)
                manifest = load_manifest(run_id) if run_id else None
                # Read the PDF's header now, inside the tempdir's lifetime: the directory (and
                # any file under it) is gone the moment this `with` block exits.
                pdfs = list(tmp_path.rglob("*.pdf"))
                pdf_ok = bool(pdfs) and pdfs[0].read_bytes().startswith(b"%PDF")
                fetch_calls = list(article_fetch_calls)
        finally:
            # Release the last command's JSONL FileHandler now: on Windows an open handle
            # keeps the tempdir's log file locked, and the `with` above deletes the whole
            # tempdir the moment this function returns.
            shutdown_logging()
            os.chdir(original_cwd)

    return Tier2RunResult(
        scenario_id=scenario["id"],
        final_text=final_text,
        manifest=manifest,
        pdf_ok=pdf_ok,
        fetch_calls=fetch_calls,
        hallucinated_flag_seen=hallucinated_seen,
        crashed=crashed,
        tool_call_count=tool_call_count,
    )


def _execute_wikitrends_command(command_text: str, log_dir: Path) -> tuple[str, bool, bool]:
    """Run one model-proposed command: ``(stdout_or_error, hallucinated_flag, crashed)``."""
    parsed = parse_cli_command(command_text)
    if not parsed.ok:
        error_json = json.dumps(
            {
                "status": "error",
                "problem": "invalid command line",
                "fix": parsed.error or "check flags against --help",
            }
        )
        return error_json, parsed.hallucinated_flag, False

    sliced = slice_to_subcommand(shlex.split(command_text))
    assert sliced is not None  # parse_cli_command already confirmed a subcommand is present
    argv = ["--log-dir", str(log_dir), *sliced]

    stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            cli.main(argv)
    except SystemExit:
        fallback = json.dumps({"status": "error", "problem": "the CLI rejected these arguments"})
        return stdout.getvalue().strip() or fallback, False, False
    except Exception as exc:  # pragma: no cover - defensive: a real bug in cli.main, not the model
        error_json = json.dumps({"status": "error", "problem": f"unexpected exception: {exc}"})
        return error_json, False, True
    return stdout.getvalue().strip(), False, False


def _last_run_id(messages: list[Message]) -> str | None:
    run_id: str | None = None
    for message in messages:
        if message.role != "tool":
            continue
        with contextlib.suppress(ValueError, AttributeError):
            payload = json.loads(message.content)
            candidate = payload.get("run_id")
            if candidate:
                run_id = candidate
    return run_id
