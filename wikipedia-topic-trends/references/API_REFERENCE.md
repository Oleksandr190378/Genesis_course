# API reference

## CLI

`wikitrends <command> [flags]`. Global flags (before the subcommand): `--log-level` (overrides
`WIKITRENDS_LOG_LEVEL`), `--log-dir` (overrides the default `.wikitrends/logs`).

Every invocation prints **exactly one line of compact JSON to stdout** (no pretty-printing) and
human-readable diagnostics to stderr; nothing else touches stdout, so it is always safe to parse
the last line. Exit code is `1` when `status` is `"error"`, `0` otherwise (including `no_match`
and `ambiguous`, which are expected outcomes, not failures).

### `research` — one-shot pipeline

```
wikitrends research --topic TOPIC --langs LANG[,LANG...] [--months N] [--out PATH]
```

| Flag | Required | Default | Notes |
|---|---|---|---|
| `--topic` | yes | — | Free-text; resolved via Wikidata search |
| `--langs` | yes | — | Comma-separated wiki language codes, e.g. `uk,de` |
| `--months` | no | `24` | Lookback window; `24` is the minimum for a seasonality check |
| `--out` | no | none | If given, also renders the PDF (equivalent to a trailing `report` call) |

Runs `resolve -> fetch -> analyze -> (report if --out)` and stops early, returning that step's
response, on `no_match` / `ambiguous` / `error`.

### `resolve` — topic -> entity -> per-language titles

```
wikitrends resolve --topic TOPIC --langs LANG[,...] [--months N] [--qid QID] [--run RUN_ID]
```

`--qid` skips the Wikidata search and uses the given entity directly — the disambiguation
follow-up after an `ambiguous` response. `--run` reuses an existing run's workspace instead of
starting a new one (its `months`/`granularity` are preserved from the original run); when
reusing a run, `--topic`/`--langs` may be omitted and fall back to that run's original request
(they are otherwise required).

### `fetch` — pageviews for a run

```
wikitrends fetch --run RUN_ID [--add-langs LANG[,...]]
```

Fetches every language already resolved on `RUN_ID`, plus any given in `--add-langs` (resolved on
the fly). Already-`fetched` languages are skipped — this is what makes "now add German" cheap.

### `analyze` — trend/trust for a run

```
wikitrends analyze --run RUN_ID
```

Recomputes relative share, trend, and trust for every language with a resolved title, reading
pageview data from cache (no new network calls beyond what `fetch` already made).

### `report` — render the PDF

```
wikitrends report --run RUN_ID --out PATH
```

Renders a single-page PDF from the run's stored analysis — no network access or re-analysis; every
figure in the PDF is read from the manifest `analyze` already wrote.

## JSON response contract

Every response is a flat JSON object with at least:

```json
{"status": "...", "run_id": "...", "caveats": [...], "next_steps": [...]}
```

`status` values: `ok`, `no_match` (resolve/research only), `ambiguous` (resolve/research only),
`error`. On `error`, `problem` (what went wrong) and `fix` (a concrete next action) are also
present; `caveats`/`next_steps` stay present but empty.

Command-specific extra fields:

- `resolve` / `research` (`ok`): `entity {qid, label, confidence}`, `languages {lang: record}`.
- `resolve` / `research` (`ambiguous`): `candidates[]`, each `{qid, label, description,
  sitelink_count, instance_of[], score}`.
- `fetch` / `analyze` (`ok`): `languages {lang: record}`.
- `report` / `research` (`ok`, with `--out`): `out` (the PDF path written).

A per-language `record`: `lang`, `resolution_status` (`ok`|`missing`), `title` (or `null`),
`is_proxy`, `proxy_qid`, `proxy_label`, `fetched`, `analysis` (or `null` before `analyze`),
`caveats[]`, `series[]` (the relative-share values charted, or `null` before `analyze`).

An `analysis` object: `n_buckets`, `median_views` (**raw** article views, not share — see
`METHODOLOGY.md` §1), `mk {direction, s, z, p_value}`, `sens_slope_per_year`, `ols
{percent_per_year, r_squared}`, `spike_indices[]`, `spike_dominant`, `zero_run_max`,
`seasonality_checked`, `trust {level, reasons[]}`.

## Wikimedia / Wikidata endpoints

| Purpose | Endpoint |
|---|---|
| Per-article pageviews | `GET https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{project}/{access}/{agent}/{article}/{granularity}/{start}/{end}` |
| Project-wide aggregate pageviews | `GET https://wikimedia.org/api/rest_v1/metrics/pageviews/aggregate/{project}/{access}/{agent}/{granularity}/{start}/{end}` |
| Entity search | `GET https://www.wikidata.org/w/api.php?action=wbsearchentities&search=...&language=en&type=item&format=json&limit=10` |
| Entity detail | `GET https://www.wikidata.org/w/api.php?action=wbgetentities&ids=...&props=sitelinks\|claims&format=json` |

Fixed parameters (`config.py`): `access=all-access`, `agent=user` (excludes bots — `all-agents`
would inflate counts and is unusable for demand analysis), `granularity=monthly`. `project` is
`{lang}.wikipedia` (e.g. `uk.wikipedia`); article titles are taken verbatim from the Wikidata
sitelink and URL-encoded. `start`/`end` are `YYYYMMDD00` (day fixed at the 1st for `start` / today
for `end`, hour always `00`).

Error handling: the pageviews REST API returns **HTTP 404** for a missing article (not an empty
`items[]`) — treated as `FetchResult(status="missing")`, a first-class result, not an exception.
Wikidata always answers 200; a missing entity shows up as `{"missing": ""}` inside the JSON body,
so there is no 404 short-circuit on that client. Both clients retry transport errors and
`{429, 500, 502, 503, 504}` with linear backoff (`RETRY_BACKOFF_BASE_SECONDS * attempt`) up to
`MAX_RETRIES = 3` before raising.

`User-Agent` is always `wikipedia-topic-trends/0.1 (<contact URL>)` — Wikimedia policy requires a
descriptive, non-personal contact string; override the URL via the `WIKITRENDS_CONTACT`
environment variable.

## Defaults (`config.py`)

`DEFAULT_MONTHS = 24`, `DEFAULT_GRANULARITY = "monthly"`, `DEFAULT_ACCESS = "all-access"`,
`DEFAULT_AGENT = "user"`, `HTTP_TIMEOUT_SECONDS = 30.0`, `MAX_RETRIES = 3`,
`RETRY_BACKOFF_BASE_SECONDS = 1.0`.

## Cache and run state

- `.wikitrends/cache/` — raw API responses, keyed on `(kind, project, article, start, end,
  granularity)`. Never expires within a checkout; re-running the same query is free.
- `.wikitrends/runs/{run_id}.json` — the run manifest (entity + per-language state). This is what
  makes `--run` refinements possible.
- `.wikitrends/logs/{run_id}.jsonl` — structured debug log for the run (see
  `TROUBLESHOOTING.md`).

All three are gitignored runtime data, not skill content.
