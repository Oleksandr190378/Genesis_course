---
name: wikipedia-topic-trends
description: Analyze Wikipedia pageview trends across language editions to gauge demand for a topic and compare interest across languages, for B2C product decisions (which topic/course to build, which language market to launch in). Produces trend direction, a machine-computed trust score, and a one-page PDF report grounded in fetched pageview data. Not for summarizing what a Wikipedia article says, answering static population/demographic facts, editing Wikipedia, or plotting arbitrary user-supplied data.
---

# wikipedia-topic-trends

Use this skill whenever a user wants to know **whether interest in a topic is growing**, in one
language or compared **across several languages**, to inform a product/investment decision — e.g.
"is X trending in language Y", "compare interest in X between Y and Z", "which language should we
launch in next", "how much can we trust this growth". It fetches real Wikipedia pageview data,
computes trend/trust in code (never guessed by the model), and can render a one-page PDF.

**Do not use it for:** summarizing or explaining what a Wikipedia article says (content, not
demand); static facts like population or "how many X are there" (no time series involved);
editing a Wikipedia page; or charting/plotting a CSV or dataset the user already has.

## Quick start (the common case: one tool call)

```bash
uv run wikitrends research --topic "astronomy" --langs uk --months 24 --out report.pdf
uv run wikitrends research --topic "intermittent fasting" --langs pl,cs --out report.pdf
uv run wikitrends research --topic "English language" --langs uk,pl,es --out report.pdf
```

`research` resolves the topic, fetches pageviews, computes trend/trust for every requested
language, and (with `--out`) renders the PDF — all in one call. `--months` defaults to 24 (the
minimum for a seasonality check); only pass the language codes you actually need.

Every command prints exactly one line of **compact JSON to stdout** and human-readable logs to
stderr. Always read `next_steps` in the JSON to decide what to do next — it is written for you
and does not require re-reading this file.

## Composable commands (refinements without re-fetching)

State persists in a workspace keyed by `run_id`. Reuse it instead of starting over:

| Command | Purpose | Required flags | Key optional flags |
|---|---|---|---|
| `resolve` | Topic -> Wikidata entity -> per-language title | `--topic`, `--langs` (both optional if `--run` reuses a run whose `ambiguous` response you're resolving) | `--qid` (pick a candidate after `ambiguous`), `--run` (reuse a run) |
| `fetch` | Pull pageviews for a run's languages | `--run` | `--add-langs` (add languages, cache-aware) |
| `analyze` | Compute trend/trust from fetched data | `--run` | — |
| `report` | Render the run's analysis to a PDF | `--run`, `--out` | — |

Example: user asks "now add German" after a `research` run — call
`wikitrends fetch --run <run_id> --add-langs de`, then `analyze` and `report` again;
already-fetched languages are skipped (cache hit), so this is cheap.

## Reading the response

Every response carries `status`, `run_id`, `caveats[]`, `next_steps[]`.

| `status` | Meaning | What to do |
|---|---|---|
| `ok` | Succeeded | Follow `next_steps` |
| `ambiguous` | Top Wikidata candidates are close in score | Show `candidates[]` to the user, re-run `resolve --run <id> --qid <chosen QID>` |
| `no_match` | No Wikidata entity found | Retry `resolve` with a more specific/different `--topic` |
| `error` | Something failed | Read `problem` and `fix`; do not retry blindly |

Inside `languages{}`, a language can be **missing an article** (`resolution_status: "missing"`,
`title: null`) — this is a **first-class result, not a failure**: it means no direct measurement
exists for that language, which is itself a real product signal (a content/measurement gap, not
zero interest). When a related **proxy** article exists in that language, `is_proxy: true` and
`title`/`proxy_label` describe it; proxy trust is always capped `low`. See
`references/TROUBLESHOOTING.md` for the exact recipe.

## Trust, not just a slope

Every analyzed language gets `trust.level` (`high`/`medium`/`low`) with `trust.reasons[]`,
computed entirely in code from the fetched series — never asserted by the model. Never present a
`low`-trust growth figure as a confident recommendation; phrase it as indicative only, per the
`reasons` given. Full rubric, thresholds, and formulas: `references/METHODOLOGY.md`.

## Reference material

- `references/METHODOLOGY.md` — normalization, trend/trust formulas and thresholds, standing
  caveats, worked real-data examples.
- `references/API_REFERENCE.md` — CLI flags/defaults, JSON contract, Wikimedia/Wikidata endpoints.
- `references/TROUBLESHOOTING.md` — missing-article/proxy, ambiguous entity, rate limits, partial
  months, unknown run_id.

Read a `references/*.md` file only when you hit a case this file doesn't resolve (an `ambiguous`
response, an `error` status, or an unexpected number) — the happy path above is enough for a
first `research` call.
