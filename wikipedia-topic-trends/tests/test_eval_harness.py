"""Offline test for evals/harness.py's Tier 2 scenario runner, with a scripted provider.

``harness._run_tier2_scenario`` normally drives a real Anthropic/OpenAI model, so its own
correctness (as opposed to the model's behavior) is otherwise only exercised manually via
``uv run python -m evals.run_evals --tier 2``. That path is expensive and non-deterministic,
which makes it a poor way to isolate a harness-only bug from real model variance. A
:class:`ScriptedProvider` that returns one fixed tool call and then stops removes the model
from the picture entirely, so a failure here can only be the harness's own tempdir/patch
plumbing, or a real bug in the CLI/logging pipeline it drives -- exactly how this test caught
the ``cache_logger_on_first_use`` bug (see ``tests/test_logging.py`` for the isolated
regression test of that bug itself).
"""

from __future__ import annotations

from collections.abc import Sequence

from evals import harness
from evals.providers import ChatResult, Message, ToolCall, ToolSpec


class ScriptedProvider:
    """Returns ``command`` as a single tool call, then stops with no further tool calls."""

    name = "scripted"
    model = "scripted"

    def __init__(self, command: str) -> None:
        self._command = command
        self._calls = 0

    def complete(
        self, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> ChatResult:
        self._calls += 1
        if self._calls == 1:
            call = ToolCall(
                id="call_1", name="run_wikitrends", arguments={"command": self._command}
            )
            return ChatResult(text=None, tool_calls=[call])
        return ChatResult(text="Done.", tool_calls=[])


def test_one_shot_research_with_out_persists_languages_to_the_on_disk_manifest() -> None:
    provider = ScriptedProvider(
        'research --topic "astronomy" --langs uk --months 24 --out report.pdf'
    )
    scenario = {
        "id": "diagnostic_astronomy_uk",
        "fixture": "astronomy_uk",
        "turns": ["Is astronomy interest growing on Ukrainian Wikipedia? Give me a PDF."],
        "max_tool_calls": 6,
    }

    result = harness._run_tier2_scenario(provider, skill_md="", scenario=scenario)

    assert result.pdf_ok is True
    assert result.manifest is not None
    assert "uk" in result.manifest.languages, (
        f"manifest.languages was {result.manifest.languages!r} -- pdf_ok alone does not "
        "prove resolve/fetch/analyze persisted anything (report.py renders a placeholder "
        "for an empty manifest without erroring)."
    )
    assert result.manifest.languages["uk"].analysis is not None
