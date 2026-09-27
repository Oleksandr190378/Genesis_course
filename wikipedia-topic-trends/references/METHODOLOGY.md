# Methodology

Full detail behind the trend/trust numbers `wikitrends` reports. `SKILL.md` only says trust is
computed automatically; this file is where "how" lives, for review or for tuning thresholds.
Every number below is produced in `src/wikitrends/analyze.py` / `caveats.py` — nothing here is
decided by an LLM (R7).

## 1. Normalization: relative share

Cross-language comparison never uses raw view counts. Every series is first converted to
**relative share**:

```
share[t] = article_views[t] / project_wide_views[t]
```

(`analyze.relative_share`, called from `cli._cmd_analyze` after `fetch.align_by_timestamp` pairs
article and aggregate buckets by timestamp). This is the single most important correctness
decision in the pipeline: `pl.wikipedia` and `cs.wikipedia` differ by roughly an order of
magnitude in total traffic, so comparing raw view counts across languages measures audience size,
not topic interest. `project_wide_views` comes from the Wikimedia pageviews **aggregate** endpoint
for the same project/period (see `API_REFERENCE.md`).

Trend direction, significance, and the `series[]` charted in the PDF are all computed on this
share series. Two exceptions, both intentional:

- **The trust volume floor** (`MIN_MEDIAN_VIEWS_FLOOR`, §3) is checked against the article's
  **raw** monthly views, not its share — a share threshold would be meaningless, since shares are
  always small fractions. `cli._cmd_analyze` passes the raw article-view series into
  `analyze_series(..., raw_views=article_values)` specifically for this check, and the resulting
  `median_views` in the analysis output is a raw view count, not a share.
- **Sen's slope** (§2) is reported in share-units-per-year — still on the share scale, not a
  percentage.

## 2. Trend estimation

Three complementary numbers, from `analyze.py`:

- **Mann-Kendall test** (`mann_kendall`) — a non-parametric test for monotonic direction
  (`increasing`/`decreasing`/`no trend`) and significance (`p_value`), robust to the spikes that
  dominate pageview data. Ties are corrected for in the variance term.
- **Sen's slope** (`sens_slope_per_bucket`, annualized as `sens_slope_per_year`) — the median of
  all pairwise slopes; a robust magnitude estimate that isn't dragged around by outliers the way
  an OLS slope would be.
- **OLS-on-log** (`ols_log_trend`) — fits `log(share) = a + b * month` and converts `b` into an
  interpretable `percent_per_year = (exp(12b) - 1) * 100`, with `r_squared` as fit quality.

  **This uses a genuine `log`, not `log1p`.** `log1p(x) ≈ x` (additive, not logarithmic) for
  `x ≪ 1`, and relative-share values are always in that regime (~1e-4 to 1e-6), so `log1p` would
  silently turn a real double-digit percent change into a near-zero, meaningless number — a real
  bug caught and fixed while writing this document (see `PROGRESS.md`). A true `log` is invariant
  to rescaling (`log(c·x) = log(c) + log(x)`, a constant shift that does not affect the fitted
  slope), so `percent_per_year` comes out correct whether the series happens to be raw views or a
  relative share. Exact zeros are floored to half the smallest positive value in the series so the
  log stays finite without one bucket dominating the fit.

## 3. Trust score

`compute_trust` applies these rules **in order**; the first match wins:

| # | Condition | Verdict | Reason text (verbatim) |
|---|---|---|---|
| 1 | `is_proxy` | `low` | "proxy-measured concept: capped at low trust" |
| 2 | raw median monthly views `< 100` (`MIN_MEDIAN_VIEWS_FLOOR`) | `low` | "median views (X) below the noise floor (100)" |
| 3 | longest zero-view run `>= 3` months (`ZERO_RUN_BUCKETS_FLAG`) | `low` | "N consecutive zero-view buckets suggest a data integrity issue (creation, rename, or redirect breakage)" |
| 4 | spike-dominant (§4) | `low` | "a few outlier buckets account for most of the change: this is an event, not a sustained trend" |
| 5 | series `< 24` months (`MIN_SERIES_BUCKETS`) | `medium` if MK significant, else `low` | bucket count + significance note |
| 6 | MK significant (`p < 0.05`, `MK_SIGNIFICANCE_ALPHA`) and `r_squared >= 0.3` (`OLS_MIN_R_SQUARED`) | `high` | MK p-value + R² |
| 7 | MK significant but `r_squared < 0.3` | `medium` | MK p-value + R² |
| 8 | otherwise (MK not significant) | `low` | MK p-value |

`trust.reasons[]` always states which rule fired, in the exact wording the CLI returns — quote it
rather than paraphrasing when explaining a verdict to a user.

## 4. Spikes, zero-runs, seasonality

- **Spikes** (`detect_spikes`) — a MAD-based modified z-score
  (`0.6745 * |v - median| / MAD`) flags any bucket over `SPIKE_MODIFIED_Z_THRESHOLD = 3.5`.
- **Spike-dominance** (`is_spike_dominant`) — true when the flagged buckets alone account for
  `>= SPIKE_DOMINANCE_SHARE` (50%) of the series' total. This distinguishes a real sustained trend
  from a one-off event (a news cycle, a viral link) and is surfaced as a caveat rather than folded
  silently into the slope.
- **Zero-runs** (`max_zero_run`) — the longest consecutive run of exactly-zero buckets;
  `>= ZERO_RUN_BUCKETS_FLAG` (3) months suggests article creation, a rename, or redirect breakage,
  not zero interest, and caps trust `low`.
- **Seasonality** (`seasonality_checked`) — a year-over-year comparison is only considered
  meaningful with `>= MIN_SERIES_BUCKETS` (24) monthly buckets; below that, a caveat says
  seasonality could not be ruled out. `--months 24` (the CLI default) is exactly the minimum for
  this check to fire.

## 5. Caveats (`caveats.py`)

Every caveat shown to the user is generated here from the analysis output — never improvised by
the LLM, so each one is traceable to the flag that triggered it (R7). `build_caveats`
short-circuits to a single message when `is_missing=True` (no article exists — see
`TROUBLESHOOTING.md`); otherwise it appends, in order: proxy warning, partial-bucket truncation
(leading/trailing), seasonality-not-checked, spike-dominance, zero-run, low-trust, then the
**standing caveats**, always present regardless of the run:

1. "Pageviews measure attention, not willingness to pay."
2. "Bot filtering (agent=user) is imperfect; some residual automated traffic may remain."
3. "Wikipedia's reader demographics are not the same as an app's buyer demographics."
4. "Article scope can differ across languages even for the same underlying concept (Wikidata
   QID)."

## 6. Worked examples (real runs, 2026-09-24, `--months 24`)

| Topic | Lang | Resolution | Direction | %/year | Trust | Why |
|---|---|---|---|---|---|---|
| astronomy | uk | ok | decreasing | -51.6% | **high** | MK p<0.001, R²=0.51 |
| intermittent fasting | pl | **missing -> proxy** "fasting" (Q44602) | decreasing | -9.6% | **low** | proxy cap (rule 1) |
| intermittent fasting | cs | ok | decreasing | -45.7% | **high** | MK p<0.001, R²=0.44 |
| English language | uk | ok | decreasing | -15.7% | **high** | MK p<0.001, R²=0.68 |
| English language | pl | ok | decreasing | -10.1% | **high** | MK p=0.001, R²=0.47 |
| English language | es | ok | decreasing | -10.7% | **high** | MK p=0.002, R²=0.42 |

All six show a real declining trend over the last 24 months. This is a useful reminder: this skill
firing correctly does not imply the answer will show growth — a founder asking "is X trending" may
correctly be told no, with a `high`-trust number backing that answer.
