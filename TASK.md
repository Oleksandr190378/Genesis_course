# Task specification (source of truth)

Condensed from the original assignment. Kept so future sessions need only this file + `PLAN.md`.

## Context

B2C product teams constantly decide what to invest in next: add a new course, cover another
topic, or localize into another language. Wikimedia publishes per-language pageview statistics,
which can reveal how interest in a topic changes over time. Interest in an article does not mean
willingness to pay, but it helps choose a direction worth validating.

## Deliverable

A **self-contained Agent Skill** that lets an agent analyze Wikipedia pageview data, generate
charts, and produce a short shareable report (e.g. a one-page PDF), so B2C founders can decide
which topics to develop and which languages to launch in.

## Example queries (illustrative, not exhaustive)

1. Compare the growth of interest in intermittent fasting in Polish vs Czech Wikipedia over the
   last two years.
2. We're considering adding an astronomy course. Is interest in this topic growing in Ukrainian
   Wikipedia, and how much can that growth be trusted?
3. We're building a language-learning app. Compare interest in learning English across our chosen
   language editions and prepare a short report: which audiences should we research next, and why?

Users will bring their own topics, languages, and criteria. They may refine queries and change
assumptions after the first answer.

## Requirements

| # | Requirement |
|---|---|
| R1 | No mandated stack (Python/TypeScript/Go/other) |
| R2 | Must contain `SKILL.md` **and** own code doing the substantive data work — a Markdown file alone, or instructions telling the agent to write the code each time, is insufficient |
| R3 | No compiled executables; dependencies and environment setup must be reproducible |
| R4 | All own materials and code must live in the skill directory |
| R5 | Must be convenient and efficient for an agent on a **fast, cheap model** (e.g. Claude Haiku 4.5 or equivalent with tool support). Verify the full scenario on such a model |
| R6 | Design how the skill helps the agent evaluate results, validate conclusions, and handle repeat/related queries efficiently |
| R7 | Recommendations and reports must be grounded in data, with assumptions and limitations made clear |
| R8 | Once basic queries work, explain how to iteratively grow it toward harder research and larger data volumes |
| R9 | Use AI tools during development and be ready to explain how their output was verified |

## Own constraints (agreed with the user)

- Output language: **English** — code, `SKILL.md`, docs, generated reports.
- Package/env management: **`uv`** exclusively, with a project virtualenv.
- Code style per `CLAUDE.md`: SOLID/DRY/KISS, Ruff, full type hints, mypy, PEP 257, pytest.
- **Structured logging is mandatory** (`structlog`).
- **Evaluation at every stage**, starting with whether the agent even opens the skill on relevant
  queries and correctly skips it on irrelevant ones.
- Every eval case runs **3 times** (agent behaviour is non-deterministic).
- Eval models: Anthropic (Haiku class) **and** a cheap OpenAI model — both keys available.
- Destination: GitHub.
