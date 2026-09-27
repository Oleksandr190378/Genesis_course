"""Live smoke test: a real OpenAI model drives the wikitrends skill against the real
Wikimedia/Wikidata APIs -- no mocked network, no evals.fixtures. This is the "does this
actually work end-to-end" check that evals/harness.py deliberately does not cover: its
Tier 2 patches the network boundary for determinism (see evals/fixtures.py's docstring).

Run from the project root (wikipedia-topic-trends/), so `evals`/`wikitrends` resolve as
top-level packages:

    uv run python -m evals.live_demo --topic "astronomy" --langs uk --out report.pdf
    uv run python -m evals.live_demo --query \\
        "Compare intermittent fasting interest between Polish and Czech Wikipedia"

Requires OPENAI_API_KEY (already in .env). Uses the same WIKITRENDS_EVAL_OPENAI_MODEL env
var as evals/providers.py, defaulting to gpt-5-mini, so a live run and a full eval run
always target the same model unless overridden.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shlex
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from dotenv import load_dotenv

from evals.harness import load_skill_md
from evals.judge import slice_to_subcommand
from evals.providers import Message, OpenAIProvider, ToolSpec
from wikitrends import cli
from wikitrends.logging import shutdown_logging

MAX_TOOL_CALLS = 8

_RUN_WIKITRENDS_TOOL = ToolSpec(
    name="run_wikitrends",
    description=(
        "Execute one `wikitrends ...` command for real and get back its single-line JSON "
        "stdout (or a structured error if the command line was invalid). Call it as many "
        "times as needed to fulfil the user's request, then reply in plain text with no "
        "further tool call."
    ),
    input_schema={
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
)


def _run_command(command_text: str, log_dir: Path) -> str:
    """Execute one model-proposed `wikitrends ...` command against the real network.

    Uses evals.judge.slice_to_subcommand -- the same invocation-prefix stripping
    harness.py's Tier 2 relies on -- so a hallucinated `uv run wikitrends ...` prefix
    doesn't burn a tool call/API round-trip here that it wouldn't burn in the eval suite.
    """
    sliced = slice_to_subcommand(shlex.split(command_text))
    if sliced is None:
        return json.dumps(
            {"status": "error", "problem": "no recognized wikitrends subcommand in the command"}
        )
    argv = ["--log-dir", str(log_dir), *sliced]

    stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            cli.main(argv)
    except SystemExit:
        return stdout.getvalue().strip() or json.dumps(
            {"status": "error", "problem": "the CLI rejected these arguments"}
        )
    return stdout.getvalue().strip()


def run_live_query(user_message: str, model: str | None = None) -> str | None:
    """Drive an OpenAI provider through a real tool-calling loop until it stops calling tools."""
    provider = OpenAIProvider(model=model) if model else OpenAIProvider()
    skill_md = load_skill_md()
    messages = [Message(role="user", content=user_message)]

    with tempfile.TemporaryDirectory(prefix="wikitrends-live-") as tmp_name:
        tmp_path = Path(tmp_name)
        log_dir = tmp_path / "logs"
        try:
            with (
                patch("wikitrends.workspace.RUNS_DIR_NAME", str(tmp_path / "runs")),
                patch("wikitrends.cli.CACHE_DIR_NAME", str(tmp_path / "cache")),
            ):
                for _ in range(MAX_TOOL_CALLS):
                    response = provider.complete(skill_md, messages, tools=[_RUN_WIKITRENDS_TOOL])
                    if not response.tool_calls:
                        return response.text
                    messages.append(
                        Message(
                            role="assistant",
                            content=response.text or "",
                            tool_calls=tuple(response.tool_calls),
                        )
                    )
                    for call in response.tool_calls:
                        command_text = str(call.arguments.get("command", ""))
                        print(f"  -> {command_text}", file=sys.stderr)
                        output = _run_command(command_text, log_dir)
                        print(f"  <- {output}", file=sys.stderr)
                        messages.append(
                            Message(
                                role="tool", content=output, tool_call_id=call.id, name=call.name
                            )
                        )
            msg = f"stopped after {MAX_TOOL_CALLS} tool calls without a final answer"
            print(msg, file=sys.stderr)
            return None
        finally:
            shutdown_logging()


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Real OpenAI agent drives the wikitrends skill against live Wikipedia data."
    )
    parser.add_argument("--query", default=None, help="Natural-language request for the agent.")
    parser.add_argument("--topic", default="astronomy", help="Used to build --query if omitted.")
    parser.add_argument("--langs", default="uk", help="Used to build --query if omitted.")
    parser.add_argument("--out", default=None, help="PDF path to ask the agent to render.")
    parser.add_argument("--model", default=None, help="Override WIKITRENDS_EVAL_OPENAI_MODEL.")
    args = parser.parse_args(argv)

    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set (check .env in the project root).", file=sys.stderr)
        return 1

    query = args.query or (
        f'Is "{args.topic}" trending on {args.langs} Wikipedia?'
        + (f" Render a PDF report to {args.out}." if args.out else "")
    )
    print(f"User: {query}", file=sys.stderr)
    final_text = run_live_query(query, model=args.model)
    print("\n=== Agent's final answer ===")
    print(final_text or "(agent produced no final text -- see stderr for the tool-call trace)")
    return 0 if final_text else 1


if __name__ == "__main__":
    raise SystemExit(main())
