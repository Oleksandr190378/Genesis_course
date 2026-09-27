"""Provider abstraction: Anthropic + OpenAI behind one tool-calling interface.

IMPLEMENTATION_PLAN.md §3.3/§8.1: eval cases are written once against :class:`Provider` so the
same harness runs unmodified against both a Claude Haiku model and a cheap OpenAI model. Keys
come from ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY``; a provider with no key configured is
omitted by :func:`available_providers` so the repo stays usable by anyone cloning it without
both keys.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

# Model ids under test (IMPLEMENTATION_PLAN.md §8.1), overridable without a code change since
# provider lineups/pricing shift faster than this file does.
ANTHROPIC_MODEL = os.environ.get("WIKITRENDS_EVAL_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
OPENAI_MODEL = os.environ.get("WIKITRENDS_EVAL_OPENAI_MODEL", "gpt-5-mini")
JUDGE_ANTHROPIC_MODEL = os.environ.get("WIKITRENDS_EVAL_JUDGE_MODEL", "claude-sonnet-5")


@dataclass(frozen=True)
class ToolSpec:
    """One callable tool offered to the model, in provider-neutral JSON-schema form."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """One tool invocation the model asked for."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ChatResult:
    """One turn of model output: free text and/or tool calls."""

    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass(frozen=True)
class Message:
    """One turn in the conversation. ``role`` is ``user`` | ``assistant`` | ``tool``.

    ``tool_calls`` (assistant turns only) must carry the exact :class:`ToolCall` objects a
    prior :meth:`Provider.complete` returned -- both APIs require the tool-use block that
    requested a call to still be present, verbatim, before they will accept the matching
    ``tool`` reply. ``tool_call_id``/``name`` (tool turns only) tie a reply back to one of
    those calls.
    """

    role: str
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None


class Provider(Protocol):
    """A chat-completion backend that can be driven with tool calling."""

    name: str
    model: str

    def complete(
        self, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> ChatResult: ...


class AnthropicProvider:
    """Anthropic Messages API, tool calling via the ``tools`` param."""

    name = "anthropic"

    def __init__(self, model: str = ANTHROPIC_MODEL) -> None:
        import anthropic

        self.model = model
        self._client = anthropic.Anthropic()

    def complete(
        self, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> ChatResult:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system,
            messages=[_to_anthropic_message(m) for m in messages],  # type: ignore[misc]
            tools=[_to_anthropic_tool(t) for t in tools],  # type: ignore[misc]
        )
        text_parts = [block.text for block in response.content if block.type == "text"]
        tool_calls = [
            ToolCall(id=block.id, name=block.name, arguments=block.input)
            for block in response.content
            if block.type == "tool_use"
        ]
        return ChatResult(text="\n".join(text_parts) or None, tool_calls=tool_calls)


class OpenAIProvider:
    """OpenAI Chat Completions API, tool calling via the ``tools`` param."""

    name = "openai"

    def __init__(self, model: str = OPENAI_MODEL) -> None:
        import openai

        self.model = model
        self._client = openai.OpenAI()

    def complete(
        self, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> ChatResult:
        openai_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        openai_messages.extend(_to_openai_message(m) for m in messages)
        openai_tools = [_to_openai_tool(t) for t in tools] if tools else None
        response = self._client.chat.completions.create(
            model=self.model,
            messages=openai_messages,  # type: ignore[arg-type]
            tools=openai_tools,  # type: ignore[arg-type]
        )
        choice = response.choices[0].message
        tool_calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=json.loads(tc.function.arguments))
            for tc in (choice.tool_calls or [])
            if tc.type == "function"
        ]
        return ChatResult(text=choice.content, tool_calls=tool_calls)


def _to_anthropic_message(message: Message) -> dict[str, Any]:
    if message.role == "tool":
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": message.content,
                }
            ],
        }
    if message.role == "assistant" and message.tool_calls:
        blocks: list[dict[str, Any]] = []
        if message.content:
            blocks.append({"type": "text", "text": message.content})
        blocks.extend(
            {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
            for tc in message.tool_calls
        )
        return {"role": "assistant", "content": blocks}
    return {"role": message.role, "content": message.content}


def _to_anthropic_tool(tool: ToolSpec) -> dict[str, Any]:
    return {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}


def _to_openai_message(message: Message) -> dict[str, Any]:
    if message.role == "tool":
        return {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
    if message.role == "assistant" and message.tool_calls:
        return {
            "role": "assistant",
            "content": message.content or None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in message.tool_calls
            ],
        }
    return {"role": message.role, "content": message.content}


def _to_openai_tool(tool: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def available_providers() -> list[Provider]:
    """Every provider with an API key set; missing keys are reported on stderr, not raised."""
    providers: list[Provider] = []
    if os.environ.get("ANTHROPIC_API_KEY"):
        providers.append(AnthropicProvider())
    else:
        print("evals: ANTHROPIC_API_KEY not set, skipping Anthropic provider", file=sys.stderr)
    if os.environ.get("OPENAI_API_KEY"):
        providers.append(OpenAIProvider())
    else:
        print("evals: OPENAI_API_KEY not set, skipping OpenAI provider", file=sys.stderr)
    return providers


def judge_provider() -> Provider | None:
    """The stronger model used only by the Tier 3 LLM-as-judge (IMPLEMENTATION_PLAN.md §8)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicProvider(model=JUDGE_ANTHROPIC_MODEL)
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAIProvider(model="gpt-5")
    return None
