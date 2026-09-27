"""Rule-based caveat generation (IMPLEMENTATION_PLAN.md §4, R7).

Every caveat shown in the report is produced here from explicit rules over the analysis
output — never improvised by the LLM, so a caveat can always be traced back to the
analysis flag that triggered it.
"""

from __future__ import annotations

from wikitrends.analyze import AnalysisResult
from wikitrends.config import MIN_SERIES_BUCKETS, ZERO_RUN_BUCKETS_FLAG

STANDING_CAVEATS = [
    "Pageviews measure attention, not willingness to pay.",
    "Bot filtering (agent=user) is imperfect; some residual automated traffic may remain.",
    "Wikipedia's reader demographics are not the same as an app's buyer demographics.",
    "Article scope can differ across languages even for the same underlying concept "
    "(Wikidata QID).",
]


def build_caveats(
    result: AnalysisResult | None,
    *,
    is_proxy: bool = False,
    is_missing: bool = False,
    truncated_leading: int = 0,
    truncated_trailing: int = 0,
) -> list[str]:
    """Build the full caveats list for one article's analysis, standing caveats last.

    ``is_missing`` short-circuits everything else: a missing article has no series to
    analyze, so only the missing-article caveat applies and ``result`` may be ``None``
    (there is no analysis yet at the point resolve.py discovers a language is missing).
    """
    if is_missing:
        return [
            "No article exists for this concept in this language edition; demand cannot "
            "be measured directly here. This is not evidence of zero interest."
        ]
    if result is None:
        raise ValueError("result is required unless is_missing=True")

    caveats: list[str] = []

    if is_proxy:
        caveats.append(
            "This concept has no direct article; the figures below are for a related "
            "proxy article and are a weaker signal of demand."
        )

    if truncated_leading:
        caveats.append("The first month of the requested range was incomplete and was dropped.")
    if truncated_trailing:
        caveats.append("The last month of the requested range was incomplete and was dropped.")

    if not result.seasonality_checked:
        caveats.append(
            f"Series has only {result.n_buckets} monthly buckets (<{MIN_SERIES_BUCKETS}): "
            "seasonality could not be ruled out."
        )

    if result.spike_dominant:
        caveats.append(
            "A small number of outlier months account for most of the change; treat this "
            "as a one-off event rather than a sustained trend."
        )

    if result.zero_run_max >= ZERO_RUN_BUCKETS_FLAG:
        caveats.append(
            f"{result.zero_run_max} consecutive months with zero views suggest a data "
            "integrity issue (article creation, rename, or redirect breakage), not zero interest."
        )

    if result.trust.level == "low":
        caveats.append("Trust in this trend is low: treat any growth figure as indicative only.")

    caveats.extend(STANDING_CAVEATS)
    return caveats
