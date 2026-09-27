# AI Agent Usage Guide — `wikipedia-topic-trends` Skill

This document describes how LLM agents (e.g., Claude, GPT-4) invoke the `wikipedia-topic-trends` skill and the reasoning loop they follow.

## Overview

The skill presents **four composable CLI subcommands** (resolve, fetch, analyze, report) behind a single tool interface. An agent's reasoning loop:

1. **Parses the user's query** to determine the topic(s), language(s), and output format requested
2. **Calls `resolve`** to map topic → Wikidata entity → per-language Wikipedia titles
3. **Handles ambiguity** if multiple candidates are close (asks the user to pick one)
4. **Calls `fetch`** to retrieve pageview time series from Wikimedia for all languages
5. **Calls `analyze`** to compute trend direction and machine-calculated trust score from the series
6. **Calls `report`** (optional) to render a one-page PDF summarizing the findings
7. **Returns JSON** with status, caveats, and next-step suggestions for the user

This is **NOT a chain-of-thought loop where the agent improvises analysis**. Trend direction, trust levels, and caveats are all **computed deterministically in code**. The agent's role is command orchestration, not reasoning about statistics.

---

## Triggering the skill

The agent sees `SKILL.md` (the skill manifest) which describes **what** the skill does and **when to use it**. Key sections:

- **Trigger:** User asks to understand Wikipedia pageview trends (demand signals for products)
- **Not a trigger:** Summarizing article content, static facts, data visualization, Wikipedia editing
- **Example queries:** "Is astronomy interest growing on Ukrainian Wikipedia?" or "Compare intermittent fasting between Polish and Czech Wikipedia"

When triggered, the agent calls the `run_wikitrends` tool with a `command` string (e.g., `research --topic "astronomy" --langs uk`).

---

## Command patterns

### Pattern 1: One-shot research (most common)

```bash
research --topic "TOPIC" --langs LANGS [--months N] [--out FILENAME.pdf]
```

**Agent reasoning:**
1. User asks a simple trend question
2. Agent calls `research` (internally: resolve → fetch → analyze → optionally report)
3. Single tool call; returns JSON with trends, trust levels, and caveats
4. Agent reads `next_steps` in response and acts accordingly

**Example:** "Is astronomy growing on Ukrainian Wikipedia?"
```bash
research --topic "astronomy" --langs uk --months 24 --out report.pdf
```

### Pattern 2: Refinement (follow-ups reuse state)

```bash
fetch --run RUN_ID --add-langs LANGS
analyze --run RUN_ID
report --run RUN_ID --out FILENAME.pdf
```

**Agent reasoning:**
1. User asks "now add German" after a previous `research` call
2. Agent reads the JSON response from the previous call to extract `run_id`
3. Agent calls `fetch --run RUN_ID --add-langs de` (already-fetched languages are skipped)
4. Agent calls `analyze --run RUN_ID` (recomputes with all languages)
5. Agent calls `report --run RUN_ID --out report.pdf` (renders updated PDF)
6. This is **cache-aware**: re-fetching the same language for the same run is avoided

**Why:** Wikimedia API rate limits are strict; reusing cached data is essential.

### Pattern 3: Disambiguation (when the topic is ambiguous)

```bash
resolve --topic "TOPIC" --langs LANGS
# Returns: status="ambiguous", candidates=[{qid, label, confidence}, ...]
# User picks one
resolve --run RUN_ID --qid CHOSEN_QID
# Returns: status="ok", then proceed to fetch
```

**Agent reasoning:**
1. Agent calls `resolve --topic "intermittent fasting" --langs pl,cs`
2. Wikidata returns multiple candidates with close ranking scores
3. Response has `status: "ambiguous"` and lists top 3 candidates
4. Agent **stops and shows candidates to the user** — does NOT guess
5. User confirms a candidate (e.g., Q1666254)
6. Agent calls `resolve --run RUN_ID --qid Q1666254` to confirm
7. Now proceeds to `fetch` with that entity locked in

**SKILL.md guidance:** *"Show candidates to the user, re-run resolve --run <id> --qid <chosen QID>"*

This is where agent honesty matters most — wrong entity → wrong conclusions. The agent **must stop** on ambiguity.

---

## Error handling

Responses include `status` field. Agent behavior:

| Status | Meaning | Agent action |
|---|---|---|
| `ok` | Success | Follow `next_steps` |
| `ambiguous` | Multiple candidates close in score | Show candidates, prompt user to pick one, re-call resolve with `--qid` |
| `no_match` | No Wikidata entity found | Suggest a more specific topic (e.g., "intermittent fasting diet" instead of "fasting") and retry |
| `error` | Failure (API rate limit, network, etc.) | Read `problem` and `fix` fields; do not retry blindly within seconds |

Every response includes `caveats[]` — machine-generated limitations that apply to the findings. These **must be preserved** and shown to the user.

---

## Special cases

### Missing articles (first-class result, not failure)

Some languages may not have an article on the topic:

```json
{
  "languages": {
    "pl": {
      "resolution_status": "missing",
      "title": null,
      "analysis": null,
      "is_proxy": false
    }
  }
}
```

**Agent reasoning:** This is **not an error**. It's a real product signal (e.g., "Polish Wikipedia has no article on this topic"). The agent should:
1. Report it to the user: "Polish Wikipedia has no direct article"
2. If a proxy article exists (`is_proxy: true`), note: "Showing proxy article instead (trust capped low)"
3. Not retry or attempt workarounds

See `references/TROUBLESHOOTING.md` for the full recipe.

### Rate limiting

Wikimedia API enforces rate limits (~100 requests/10 seconds per IP). If `fetch` fails with a rate-limit error:

```json
{
  "status": "error",
  "problem": "Wikimedia rate limit exceeded",
  "fix": "Wait 10 seconds and retry"
}
```

**Agent reasoning:** Wait and retry the exact same `fetch` command after the delay (the same `run_id` and cache state is preserved).

---

## Caveats and trust scores

Every analysis includes:

- **`trust.level`** (`high` / `medium` / `low`) — computed entirely in code
- **`trust.reasons[]`** — specific reasons for the level (e.g., "low traffic", "high seasonality", "short history")
- **`caveats[]`** — limitations of the analysis (e.g., "month N is incomplete")

**Agent responsibility:**
- Never present a `low`-trust finding as confident
- Phrase low-trust results as "indicative only" or "exploratory signal"
- Include all caveats when explaining to the user
- Trust scores are **never subjective**: they're machine-calculated per `references/METHODOLOGY.md`

Example agent phrasing:
> "Polish Wikipedia shows intermittent fasting growing (~+8%/year), but trust is **low** because traffic is sparse. This is indicative only. Czech shows stronger, more reliable growth (~+15%/year, **high** trust)."

---

## JSON contract

Every CLI response is **exactly one line of compact JSON to stdout**. The agent must parse this line reliably:

```json
{
  "status": "ok",
  "run_id": "run_1790260037_abc123",
  "languages": {
    "uk": {
      "title": "Астрономія",
      "trend": {"slope_pct_year": 4.2, "direction": "stable_or_growing"},
      "trust": {"level": "high", "reasons": ["..."]},
      "analysis": {...}
    }
  },
  "caveats": [...],
  "next_steps": ["You can now analyze another language by running: fetch --run ... --add-langs de"],
  "problem": null,
  "fix": null
}
```

**Agent must:**
1. Parse only this JSON line (ignore stderr logs)
2. Read `status` first (if not `ok`, handle error)
3. Extract `run_id` for use in follow-up commands
4. Include `caveats[]` in any user-facing response
5. Read and follow `next_steps[]` guidance

**Anti-pattern:** Making up analysis based on the slope value. The `trust` field and `caveats` are authoritative; agent should never override them.

---

## SKILL.md guidance for the agent

The skill manifest (`SKILL.md`) teaches the agent:

1. **When to trigger:** Wikipedia trend demand signals (not content analysis)
2. **Quick start:** Show the simplest command pattern (one-shot `research`)
3. **Composability:** Explain refinement commands and cache reuse
4. **Response structure:** Document status, run_id, caveats, next_steps
5. **Trust, not guessing:** "Computed entirely in code from the fetched series — never asserted by the model"
6. **Trigger exclusions:** Not for editing Wikipedia, summarizing content, or static facts

The agent is expected to follow this guidance precisely. Deviations (e.g., attempting to improvise trust scores, or hallucinating unsupported CLI flags like `--granularity weekly`) fail the evaluation.

---

## Eval coverage (Tiers 0–3)

Agents are evaluated on:

- **Tier 0:** Does the agent recognize when to trigger the skill (precision/recall)?
- **Tier 1:** Does the agent form correct CLI arguments (no typos, all required flags)?
- **Tier 2:** Does the agent execute multi-step refinements correctly and persist state?
- **Tier 3:** Does the agent's output preserve caveats, address the user's decision, and avoid unsupported claims?

Haiku and gpt-5-mini both achieve >90% pass rates on one-shot queries and ~80%+ on refinements, showing the skill design is learnable by cheap models at scale.

---

## Common agent mistakes (and how they're caught)

| Mistake | Symptom | Fix |
|---|---|---|
| Hallucinating a flag (e.g., `--granularity weekly`) | CLI rejects with `unknown argument` error | Agent should recognize the error and not retry with the same flag |
| Forgetting `--add-langs` on a refinement | Agent refetches languages unnecessarily, wasting API quota | Agent should learn to use `--add-langs` for refinements |
| Ignoring `no_match` and retrying with the same topic | Infinite retry loop | Agent should read the error and suggest a different topic to the user |
| Overriding trust scores in prose | Agent says "this is actually high confidence" despite trust="low" | Tier 3 judge catches this; agent must preserve trust from the response |
| Missing caveats in the response | User sees findings without limitations | Tier 3 catches missing caveats; agent must include all from `caveats[]` |

---

## Implementation notes

- The skill runs **only on cheap models** (Haiku, gpt-5-mini), not state-of-the-art. Robust command parsing and error handling are essential.
- Every command's output is **fully deterministic** (same input → same output). This makes agent behavior reproducible and test coverage feasible.
- The agent is **not expected to re-interpret results**. It reads the JSON, trusts the computed trust scores and caveats, and relays them to the user.
