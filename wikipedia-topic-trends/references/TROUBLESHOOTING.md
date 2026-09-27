# Troubleshooting

Recipes for the non-`ok` cases you'll actually hit. Each `next_steps[]` in the JSON already tells
you the immediate action — this file is for the *why*, and for cases that need more than one step.

## "No article exists in this language" (missing / proxy)

**This is a first-class result, not an error** — a missing article is itself a real product
signal (a content gap, a measurement gap, or the concept being expressed differently locally), not
evidence of zero interest. Do not treat `resolution_status: "missing"` as a failure.

Real example: `wikitrends research --topic "intermittent fasting" --langs pl,cs` — no Polish
article exists for the concept (confirmed live against `pl.wikipedia`). The response for `pl`:

```json
{"resolution_status": "missing", "title": "Post", "is_proxy": true,
 "proxy_qid": "Q44602", "proxy_label": "fasting"}
```

`resolve_language` (`resolve.py`) automatically looked for a nearby broader/related concept
(`subclass of` / `part of` claims on the entity) and found one with a `pl` article — "fasting" —
using it as a **proxy**, flagged `is_proxy: true`. The report and JSON both say so explicitly, and
`analyze.py` hard-caps proxy trust at `low` regardless of what the trend looks like (see
`METHODOLOGY.md` §3) — a proxy measures a related concept, not the topic itself, so its number
should never be read as confident.

If `is_proxy` is `false` and `title` is `null`, no proxy was found either — say so plainly to the
user; do not substitute a different, unrelated article yourself.

**What to tell the user:** name the language with no direct article, explain that it reflects a
content/measurement gap as much as (or more than) a demand gap, and if a proxy exists, present its
figure as a weaker signal alongside languages with a direct article — never merge it into the same
trend line without the caveat.

## `ambiguous` — top Wikidata candidates are close

Triggered when the top-ranked entity doesn't clearly beat the runner-up (score ratio below the
confidence margin). Example shape:

```json
{"status": "ambiguous", "candidates": [
  {"qid": "Q1666254", "label": "intermittent fasting", "score": 31.0, "...": "..."},
  {"qid": "Q...", "label": "...", "score": 18.0, "...": "..."}
]}
```

Show the top 3 candidates (`label` + `description`) to the user and ask which one is right, then
re-run: `wikitrends resolve --run <run_id> --qid <chosen QID>` (`--topic`/`--langs` are not
needed again -- the run already has them on file). Do not guess; confidence is `low`
specifically because a silent pick is unsafe here (a search for a health topic can surface a
clinical-trial or magazine-article entity ahead of the actual concept).

## `no_match` — no Wikidata entity found

Retry `resolve` / `research` with a more specific or differently-worded `--topic`. This is common
for very new or extremely niche topics with no Wikidata item at all yet.

## `error` — unknown `run_id`

`{"status": "error", "problem": "unknown run_id: ...", "fix": "Call resolve first..."}`. Run IDs
are created by `resolve` / `research` and persisted under `.wikitrends/runs/{run_id}.json`; a
composable command (`fetch` / `analyze` / `report`) needs one to already exist. Don't invent a
`run_id` — always take it from a prior response.

## `error` — this run has no resolved entity yet

`fetch` / `report` was called on a `run_id` that exists but was never successfully `resolve`d
(e.g. the initial `resolve` returned `ambiguous` / `no_match` and was never completed). Call
`resolve --run <run_id>` with a `--qid` or a corrected `--topic` first.

## Rate limiting / transient failures

Both the Wikimedia and Wikidata clients retry `{429, 500, 502, 503, 504}` and transport errors up
to `MAX_RETRIES = 3` times with linear backoff, automatically — you will not normally see these
unless retries are exhausted, in which case the command returns a generic `error` with `problem`
set to the exception message and `fix` pointing at `.wikitrends/logs/{run_id}.jsonl` for the full
traceback. If this happens repeatedly, wait and retry the same command — the workspace/cache means
only the failed step re-runs, not the whole pipeline.

## "The last month is missing" / partial-bucket caveat

`"The last month of the requested range was incomplete and was dropped."` — Wikimedia pageview
data lags roughly 1-2 days, so a request ending "today" always has a partial current-month bucket;
`fetch.py` drops any leading/trailing bucket not fully covered by `[start, end]` rather than let it
manufacture a fake collapse in the trend (a bucket covering 1 day out of a 30-day month can be off
by 30x). This is expected on essentially every run and is not a bug — it just means the series
covers slightly less than the requested `--months`.

## Low trust / near-zero traffic

If `trust.level` is `low` with reason `"median views (X) below the noise floor (100)"`, the
article's typical **raw** monthly views are below 100 — there isn't enough signal to say anything
about a trend, direct or proxy. This is unrelated to how large the language edition's overall
audience is; it is specifically about this article's own traffic.

## Debugging beyond the JSON

Every run's full structured log is at `.wikitrends/logs/{run_id}.jsonl` (always `DEBUG`, one line
per event: HTTP requests, cache hits/misses, resolution candidate scoring, every trust-rule
evaluation, every caveat as it's emitted). `--log-level` / `--log-dir` (or `WIKITRENDS_LOG_LEVEL`)
control the human-readable stderr sink; the JSONL file is unaffected by `--log-level` and is the
right place to look first for "why did it pick this trust level / this entity".
