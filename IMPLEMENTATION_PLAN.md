# `wikipedia-topic-trends` — Task Analysis & Implementation Plan

Status: approved for implementation · Date: 2026-09-23
Task spec: [`TASK.md`](TASK.md) · Supersedes the earlier `plan.md` (deleted; its useful parts are merged here)

---

## 1. Task analysis

Requirements R1–R9 and agreed constraints are in [`TASK.md`](TASK.md). Mapping to this plan:
resolve/fetch/analyze/report pipeline §3, trust engine §4, cheap-model efficiency §5,
logging §6, evaluation §8, growth roadmap §9.

### 1.1 What is actually being evaluated

The Wikimedia API is trivial. The difficulty — and therefore the grading surface — is elsewhere:

1. **Skill design under a token budget.** Does `SKILL.md` stay small and delegate to code, or does it try to teach the model statistics inline?
2. **Epistemic honesty.** Example query 2 asks *"and how much can this growth be trusted?"* — trust scoring is a **first-class deliverable**, not decoration. A skill that reports a slope without a confidence assessment fails the core ask.
3. **Handling messy reality.** Cross-language comparison is genuinely hard (see §2). A skill that pretends it is easy is wrong.
4. **Conversational durability.** "Users may refine queries and change assumptions after the first answer." Requires cached, resumable, composable state — not a one-shot script.
5. **Product framing.** The user is a B2C founder, not an analyst. Output must answer *"where do we invest?"*, not *"here is a slope coefficient."*

### 1.2 Success criteria

- All three example queries answered end-to-end, including the **Polish case that has no article** (§2.2).
- A follow-up like "now add German" reuses cache and does not re-run the whole pipeline.
- Cheap models complete a scenario in ≤ 6 tool calls with no argument errors, at 3/3 across repeats.
- Every number in the PDF is traceable to a fetched data point; every caveat is machine-generated, not improvised by the LLM.

---

## 2. API reconnaissance — findings (verified live, 2026-09-23)

These were probed with real requests before planning. They drive the design.

### 2.1 Endpoints confirmed working

- **Per-article:** `GET https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{project}/{access}/{agent}/{title}/{granularity}/{start}/{end}` → `items[] {project, article, granularity, timestamp, access, agent, views}`. Verified on `uk.wikipedia / Астрономія`.
- **Project aggregate:** `.../pageviews/aggregate/{project}/{access}/{agent}/{granularity}/{start}/{end}`. Verified on `pl.wikipedia` (~258M views, Jan 2024). **This is the denominator that makes cross-language comparison legitimate.**
- **Resolution:** Wikidata `wbsearchentities` → QID → `wbgetentities&props=sitelinks` gives per-language titles. Verified.
- Missing article → HTTP **404** (not an empty `items` list). Must be caught per-language.

### 2.2 Critical finding — the task's own first example is a trap

Query 1 asks to compare **intermittent fasting in Polish vs Czech** Wikipedia.

```
Q1666254 "intermittent fasting"
  cswiki -> "Přerušovaný půst"          OK
  ukwiki -> "Інтервальне голодування"   OK
  dewiki -> "Intermittierendes Fasten"  OK
  plwiki -> MISSING
```

Confirmed against `pl.wikipedia` search directly: **no Polish article exists.** A naive skill returns a 404 and dies on the flagship example.

**Design consequence — "no article in this language" is a first-class result, not an error.** It is also a genuine product signal, and the skill must say so:
> *No Polish article exists for this concept. This means demand cannot be measured directly here. It is not evidence of zero interest — it usually indicates the topic is under-covered locally or is expressed through a different concept. For a language-learning or health app this is simultaneously a content gap and a measurement gap.*

The skill then offers **proxy measurement**: nearest related concepts that *do* exist in `pl` (e.g. fasting, dieting, insulin resistance) with an explicit `proxy: true` flag, clearly marked as a weaker signal.

### 2.3 Ambiguous entity resolution

`wbsearchentities "intermittent fasting"` returns, in order:
1. `Q1666254` — the concept ✅
2. `Q112575736` — *a magazine article about it*
3. `Q63574657` — *a clinical trial*

Top-hit-wins would frequently pick a paper instead of a concept. **Mitigation:** score candidates by sitelink count and `instance of`, prefer entities with many sitelinks, and surface the top 3 with confidence so the agent can confirm ambiguous cases with the user instead of silently guessing.

### 2.4 Partial-bucket trap

Requesting monthly data with `end=20240401` returned an April bucket of **72 views** versus ~3,000/month before it — because April contained one day. Fed into a regression this manufactures a fake collapse.

**Mitigation:** the fetch layer drops any leading/trailing bucket not fully covered by the requested range, and records the truncation in `caveats`.

### 2.5 Other constraints to honor

- Descriptive `User-Agent` is mandatory by Wikimedia policy; requests without one may be refused. Contact string goes in a config constant, not hardcoded personal data.
- Pageview data lags roughly 1–2 days — never treat the last days as final.
- Use `agent=user` to exclude bots/spiders. `all-agents` inflates counts and is unusable for demand analysis.
- Titles need URL-encoding and `_` for spaces; non-Latin titles work fine (verified with Cyrillic).
- Be a polite client: cache aggressively, throttle, retry with backoff on 429/5xx. Exact published limits to be re-confirmed at implementation time; design assumes we stay far below any threshold.

---

## 3. Architecture

### 3.1 What I keep from the earlier `plan.md`

Sound and retained: four-stage pipeline (resolve → fetch → analyze → report); relative-share normalization; regression + trust flags computed in code, never by the LLM; matplotlib-only PDF; synthetic-series unit tests written before touching real data; progressive disclosure into `references/`; the Phase 2/3 roadmap shape.

### 3.2 What I change, and why

| Change | Rationale |
|---|---|
| **One CLI with subcommands**, not four loose scripts | A Haiku-class model picks the wrong script and mis-orders arguments. One binary, one `--help` surface, uniform JSON. |
| **Add a `research` one-shot command** running the entire pipeline | The common case becomes *one tool call*. Subcommands stay available for refinements. Biggest single win for R5. |
| **Persistent workspace + run manifest** (`.wikitrends/`) | Makes "now add German" incremental. The old plan had caching but no run state, so follow-ups had to be reassembled by the model. |
| **`missing`/`proxy` as first-class states** | Required by §2.2; the old plan only had a `low_confidence` flag, which is not enough. |
| **Mann–Kendall + Sen's slope** alongside OLS | Non-parametric, robust to the spikes that dominate pageview data. OLS-on-log alone overstates significance. |
| **Machine-generated `caveats[]` list** | R7 demands explicit limits. Generating them in code prevents the LLM from inventing or omitting them. |
| **Drop `scipy`** | `numpy` + a small hand-written Mann–Kendall keeps install light and reproducible. |

### 3.3 Directory layout

Everything lives inside the single skill directory, per R4.

```
wikipedia-topic-trends/
├── SKILL.md                      # <150 lines: when to use, 4 commands, decision rules
├── LICENSE.txt
├── pyproject.toml                # uv-managed
├── uv.lock
├── src/wikitrends/
│   ├── cli.py                    # argparse subcommands; JSON in / JSON out
│   ├── config.py                 # User-Agent, endpoints, thresholds
│   ├── logging.py                # structlog setup (§6)
│   ├── resolve.py                # topic -> QID -> per-language titles (+ proxies)
│   ├── fetch.py                  # pageviews + project aggregate; partial-bucket guard
│   ├── cache.py                  # keyed on (project, article, start, end, granularity)
│   ├── workspace.py              # run manifest, incremental re-runs
│   ├── analyze.py                # trend, trust score, spikes, seasonality, share
│   ├── caveats.py                # rule-based limitation generator
│   └── report.py                 # matplotlib -> single-page PDF
├── references/
│   ├── METHODOLOGY.md            # exact formulas, thresholds, trust rubric
│   ├── API_REFERENCE.md          # endpoints, params, language codes, error codes
│   └── TROUBLESHOOTING.md        # 404 / missing article / rate limit / rename recipes
├── tests/                        # pytest, offline (Tier 4)
│   ├── test_analyze.py           # synthetic series with known answers
│   ├── test_fetch.py             # partial buckets, 404, cache hits
│   ├── test_resolve.py           # candidate ranking, missing sitelinks
│   └── fixtures/                 # recorded API responses
├── evals/                        # agent-level evaluation (§8)
│   ├── cases/
│   │   ├── trigger.yaml          # Tier 0: positives + hard/easy negatives
│   │   ├── activation.yaml       # Tier 1: expected command + args
│   │   └── e2e.yaml              # Tier 2/3: full scenarios + rubrics
│   ├── providers.py              # Anthropic + OpenAI behind one interface
│   ├── harness.py                # agent loop, 3 repeats, metric collection
│   ├── judge.py                  # deterministic checks + LLM-as-judge rubric
│   └── run_evals.py              # CLI entry; writes reports
└── docs/
    ├── AI_USAGE.md               # R9
    └── EVAL_RESULTS.md           # generated eval report
```

`.wikitrends/` (cache, run state, logs) is gitignored — runtime data, not skill content.

### 3.4 CLI contract

```bash
# One-shot — the default path for a simple request
wikitrends research --topic "astronomy" --langs uk --months 36 --out report.pdf

# Composable path — refinements reuse the workspace
wikitrends resolve --topic "intermittent fasting" --langs pl,cs
wikitrends fetch   --run RUN_ID --add-langs de
wikitrends analyze --run RUN_ID
wikitrends report  --run RUN_ID --out report.pdf
```

Every command prints **compact JSON** to stdout (no pretty-printing — token cost) and human diagnostics to stderr. Every response carries `status`, `run_id`, `caveats[]`, and `next_steps[]`. `next_steps` tells a weak model what to do next without re-reading `SKILL.md`.

---

## 4. Methodology — trend & trust

Full detail lives in `references/METHODOLOGY.md`; `SKILL.md` only states that trust is computed automatically.

**Normalization.** Cross-language comparison uses **relative share** = article views ÷ project-wide views for the same period. Absolute views are reported only for scale context. This is the single most important correctness decision: `pl.wikipedia` and `cs.wikipedia` differ by roughly an order of magnitude in total traffic, so raw counts compare audience size, not topic interest.

**Trend estimation.** Mann–Kendall test for direction and significance; Sen's slope for magnitude; OLS on `log1p(views)` for an interpretable %/year figure with R². Growth is reported as a percentage change with an interval, never as a bare point estimate.

**Trust score** (`high` / `medium` / `low`) from explicit, documented rules:
- volume floor — below a minimum median daily views the series is noise;
- series length — fewer than 24 months cannot separate trend from seasonality;
- MK significance and OLS R²;
- spike dominance — if a few MAD-based outlier buckets carry most of the growth, it is an event, not a trend, and is reported separately;
- data integrity — zero-runs signalling article creation, rename, or redirect breakage;
- proxy penalty — proxy-measured concepts are capped at `low`.

**Seasonality.** Year-over-year same-month comparison when ≥24 months are available; otherwise a caveat is emitted saying seasonality could not be ruled out.

**Standing caveats always present in the PDF:** pageviews measure attention, not willingness to pay; bot filtering is imperfect; Wikipedia demographics are not app-buyer demographics; article scope differs across languages even for the same QID.

---

## 5. Optimizing for a cheap model

1. **`SKILL.md` under 150 lines.** Trigger conditions, the 4 commands, a short decision table, pointers to `references/`. No statistics explained inline.
2. **One-shot `research` command** — the happy path is a single tool call.
3. **Self-describing output.** `next_steps[]` and `caveats[]` in every payload mean the model transcribes rather than reasons.
4. **Fail loudly and instructively.** Errors return `{"status":"error","problem":...,"fix":...}` so the model corrects itself instead of improvising.
5. **Compact JSON, capped arrays.** Raw time series stay on disk; stdout gets aggregates plus a file path.
6. **Sensible defaults for everything** (`--months 24`, `--granularity monthly`, `agent=user`, `all-access`) so omitted arguments never break a run.

---

## 6. Environment & logging

### 6.1 Environment — done

`uv 0.12.18` installed; project scaffolded at `wikipedia-topic-trends/` with a pinned **Python 3.12** virtualenv (`.venv`). All commands run as `uv run ...`; `uv.lock` is committed for reproducibility (R3).

Runtime deps: `httpx`, `numpy`, `matplotlib`, `structlog`, `PyYAML`.
Dev/eval deps: `pytest`, `ruff`, `mypy`, `anthropic`, `openai`.
Eval dependencies go in an optional `[dependency-groups] evals` group so the skill itself stays light for users who only run it.

Ruff + mypy configured in `pyproject.toml`: 4-space indent, double quotes, full type hints, PEP 257 docstrings.

### 6.2 Structured logging (mandatory)

`structlog`, configured once in `src/wikitrends/logging.py`, with **two sinks**:

- **stderr** — human-readable, colored, `INFO` by default. Never stdout: stdout carries the JSON contract the agent parses, so any log leak there corrupts the tool output.
- **`.wikitrends/logs/{run_id}.jsonl`** — machine-readable JSON lines, always `DEBUG`.

Every event is bound to a `run_id` (and `lang` / `article` where applicable) so one research run is a single filterable stream. Logged events include: resolution candidates and the score that picked the winner, each HTTP request with status/duration/retry count, cache hit vs miss, partial-bucket truncation, every trust-rule evaluation and its verdict, and each caveat as it is emitted.

Two design points make this more than boilerplate:

1. **Logs are the audit trail for R7.** "Recommendations must be grounded in data" is verifiable only if you can replay how a number was produced. The trust verdict is reconstructable from the log alone.
2. **Logs are the eval instrumentation.** Tool-call counts, latencies, retries and cache efficiency in §8 are parsed from these JSONL files rather than measured by separate ad-hoc code. One mechanism, two uses.

`--log-level` and `--log-file` are global CLI flags; `WIKITRENDS_LOG_LEVEL` overrides.

---

## 7. Deterministic testing (Tier 4)

Offline and fully deterministic, run on every change:

1. **Unit tests.** Synthetic series with known answers: pure growth, plateau, noise-only, single spike, seasonal, short series, zero-run. Plus partial-bucket truncation, 404 handling, cache hit/miss, candidate ranking, caveat generation. No network — recorded fixtures only.
2. **Ground-truth sanity check.** Cross-check 2–3 articles against the official Wikimedia Pageviews Analysis tool to confirm we read the API correctly. Done once manually, recorded in `docs/AI_USAGE.md`.
3. **Gates.** `uv run ruff check`, `uv run mypy`, `uv run pytest` must all pass before anything is called done.

---

## 8. Agent evaluation (Tiers 0–3)

Agent behaviour is **non-deterministic**, so every case runs **3 times** and is scored as a pass
rate `k/3`, never as a boolean. A case is green only at 3/3; 2/3 is a warning that gets
investigated. This is standard practice for skill evals and the reason a single happy-path demo
proves nothing.

The tiers mirror the three stages of progressive disclosure defined by the Agent Skills spec
(Discovery → Activation → Execution), plus output quality. Each tier is independently runnable —
`uv run python evals/run_evals.py --tier 0` — because Tier 0 is cheap and should run far more
often than Tier 2.

### Tier 0 — Discovery / trigger precision

**The highest-leverage tier, and the one usually skipped.** Only skill `name` + `description` are
in context; the skill is never executed. The model is given a query plus a catalogue of decoy
skill descriptions and asked which skill applies.

Corpus (~30 labelled queries in `evals/cases/trigger.yaml`):
- **Positives** — the three task examples plus paraphrases, other languages, indirect phrasings
  ("is anyone actually interested in X in Spain?").
- **Hard negatives** — the important ones, topically adjacent but wrong: *"summarize the Wikipedia
  article on astronomy"* (content, not demand), *"plot this CSV"* (charting, not research),
  *"how many people live in Poland?"* (facts, not trends), *"edit a Wikipedia page"*.
- **Easy negatives** — unrelated dev tasks.

Metrics: **trigger precision, recall, F1**, and per-query trigger rate over 3 runs. A skill that
never fires is worthless; one that fires on everything is harmful. This tier tunes the
`description` string — the single highest-value string in the whole skill — and the results are
recorded so the tuning is evidence-based rather than intuition.

### Tier 1 — Activation / command selection

`SKILL.md` is loaded, tools are **mocked** (no network, no cost beyond the model call). Does the
model choose the right command, with valid arguments, on the first attempt?

Metrics: correct command, argument validity against the CLI schema, hallucinated-flag rate, number
of turns to first valid call, whether it wastes calls reading `references/` when it did not need to.

### Tier 2 — Execution / end-to-end

The real pipeline against **recorded HTTP fixtures** for determinism, plus a smaller live-network
suite run manually. Scenarios: the three task examples — including the Polish missing-article case
from §2.2 — plus two refinements ("now add German", "switch to weekly") that must hit cache and
skip redundant work.

Metrics (parsed from the structlog JSONL): task success, tool calls per scenario, total turns,
tokens in/out, wall time, cost, cache hit ratio on refinements, and error-recovery rate when the
skill returns a structured error.

### Tier 3 — Output quality

Two complementary checks, because neither alone is trustworthy:

- **Deterministic assertions** (cheap, objective): the PDF exists and is exactly one page; the
  assumptions/limitations block is present; every figure in the report matches the analysis JSON;
  `caveats[]` is non-empty; a `low` trust verdict is never described as a confident recommendation.
- **LLM-as-judge** (rubric-scored 1–5, with the reference analysis JSON supplied as ground truth):
  does the answer address the founder's actual decision? Are caveats preserved rather than dropped?
  Is there a concrete recommendation? Are unsupported claims absent?

The judge runs on a **stronger** model than the one under test, and its rubric scores are spot-checked
by hand — a judge is a measuring instrument and needs calibrating like any other.

### 8.1 Models under test

Two providers, same cases, run side by side — cross-provider agreement is a much stronger signal
than one model passing:

| Role | Model |
|---|---|
| Primary cheap model | `claude-haiku-4-5-20251001` (Anthropic) |
| Second cheap model | a low-cost OpenAI model (exact id confirmed at implementation time) |
| Judge (Tier 3) | a stronger model from either provider |

`evals/providers.py` hides both behind one tool-calling interface so cases are written once. Keys
come from `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` environment variables — never committed, never
logged. A tier is skipped with a clear message if its key is absent, so the repo stays usable by
anyone cloning it.

### 8.2 Output

`run_evals.py` writes `docs/EVAL_RESULTS.md`: per-tier pass rates `k/3`, the precision/recall table,
cost and token totals per model, and a diff against the previous run so `SKILL.md` changes can be
judged by their measured effect. **This is the feedback loop the task is really asking for** —
`SKILL.md` gets tuned against these numbers, and the before/after is the evidence that the tuning
worked.

---

## 9. Iterative roadmap (R8)

**Phase 2 — harder research.** Multi-topic comparison in one run; automatic discovery of adjacent topics via Wikidata categories and series correlation; change-point detection (PELT/BOCPD) instead of a single global slope; cross-language *lag* analysis (does interest hit `en` before `uk`?); a `--calibrate` mode that back-tests whether past Wikipedia trends actually predicted known product outcomes.

**Phase 3 — scale.** Replace per-article REST with monthly Wikimedia **clickstream/pageview dumps** loaded into DuckDB, enabling whole-category sweeps instead of hand-picked articles; async rate-limit-aware batch fetch; a materialized per-language topic index so repeated research is a local query; optional cross-signals (Google Trends, app-store keyword volume) to address the "attention ≠ willingness to pay" gap head-on.

**Phase 4 — product loop.** Persist past recommendations and outcomes so the skill can report which of its own past calls were right — turning it from a reporting tool into a calibrated forecaster.

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| Wrong Wikidata entity chosen silently | Candidate ranking + top-3 surfaced + confidence flag; agent confirms when ambiguous |
| Missing article kills a comparison | First-class `missing` state + proxy articles + explicit narrative (§2.2) |
| Cross-language comparison is apples-to-oranges | Relative share as the primary metric; article-scope divergence in standing caveats |
| Cheap model ignores caveats | Caveats generated in code and rendered into the PDF by code, not by the LLM; Tier 3 asserts they survive |
| Matplotlib font gaps on non-Latin titles | Reports are English-only (decided); original titles transliterated or shown with a fallback font |
| Eval cost/flakiness at 3 runs × 2 models × 4 tiers | Tier 0/1 are cheap and run often; Tier 2/3 use recorded fixtures and run on demand |
| LLM judge is itself unreliable | Deterministic assertions carry the verdict; judge scores are advisory and hand-calibrated |
| Over-scoping | Phase 1 scope frozen at §3.3; Phase 2+ stays documentation |

---

## 11. Implementation order

0. ~~Install `uv`, scaffold project, create venv, `git init`~~ — **done**.
1. `pyproject.toml` deps + ruff/mypy/pytest config + `logging.py`. Logging lands first so every later step is observable.
2. `config.py` + `cache.py` + `fetch.py` — partial-bucket guard and 404 handling. Record fixtures.
3. `analyze.py` + `caveats.py` — **unit tests on synthetic series first**, before any real data.
4. `resolve.py` — candidate ranking, sitelinks, missing/proxy states.
5. `workspace.py` + `cli.py` — subcommands, `research` one-shot, JSON contract.
6. `report.py` — single-page PDF (chart + verdict bullets + assumptions block).
7. `SKILL.md` + `references/*` — written last, once real command output exists to describe.
8. `evals/` harness + case corpora; **Tier 0 first** — it needs only `SKILL.md` frontmatter and immediately tunes the `description`.
9. Full eval run on both providers; tune `SKILL.md`; regenerate `EVAL_RESULTS.md`; write `AI_USAGE.md`.
10. Final `ruff` / `mypy` / `pytest` pass, README, push to GitHub.

Review checkpoints: after step 3 (is the methodology right?), after step 6 (does the PDF actually persuade a founder?), after step 9 (does it hold up on cheap models?).

---

## 12. Decisions log

| Decision | Choice | Source |
|---|---|---|
| Output language | English only (code, `SKILL.md`, PDF) | user |
| Phase 1 scope | Solid v1: 4 stages + tests + trust metrics | user |
| Logging | `structlog`, mandatory, dual sink, doubles as eval instrumentation | user |
| Evaluation | 4 tiers mirroring Discovery → Activation → Execution + output quality | user |
| Eval repetition | 3 runs per case, scored `k/3` | user / spec practice |
| Eval models | `claude-haiku-4-5` + a cheap OpenAI model; stronger model as judge | user |
| Python / env | 3.12 pinned, `uv` + `.venv`, lockfile committed | user + CLAUDE.md |
| PDF engine | matplotlib `PdfPages`, no reportlab | old plan, retained |
| CLI shape | single entry point with subcommands + one-shot `research` | changed from old plan |
| Statistics | Mann–Kendall + Sen's slope + OLS-on-log; no scipy | changed from old plan |
