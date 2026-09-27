"""Topic -> Wikidata entity -> per-language article titles, with proxies.

Two hard cases from IMPLEMENTATION_PLAN.md §2.2-2.3 drive this module:

- **Ambiguous entities** (§2.3): ``wbsearchentities "intermittent fasting"`` returns the
  concept, a magazine article about it, and a clinical trial, in that order. Top-hit-wins
  is wrong, so candidates are scored by sitelink count (a real concept usually has far more
  sitelinks than a specific paper or trial) with an ``instance of`` penalty as a secondary
  signal, and the top 3 are surfaced with a confidence flag rather than the top pick being
  silently trusted.
- **Missing articles** (§2.2): no Polish article exists for "intermittent fasting". This is
  a first-class ``"missing"`` result, not an error, and a proxy article (a related concept
  that does exist in the target language) is suggested and flagged ``is_proxy=True`` so it
  feeds straight into ``analyze_series(..., is_proxy=True)`` / ``build_caveats(...)``.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from wikitrends.config import (
    MAX_RETRIES,
    RETRY_BACKOFF_BASE_SECONDS,
    RETRYABLE_STATUS_CODES,
    WIKIDATA_API_URL,
)
from wikitrends.logging import get_logger

logger = get_logger(component="resolve")

# Candidates fetched from wbsearchentities before scoring. Wide enough that the correct
# concept is unlikely to be pushed out by spurious literature/trial entries (§2.3), small
# enough to keep the follow-up wbgetentities batch call cheap.
SEARCH_LIMIT = 10

# How many ranked candidates are surfaced to the caller, per §2.3's "surface top 3" mitigation.
TOP_CANDIDATES_SURFACED = 3

# The top-ranked candidate must score at least this many times the runner-up to be treated
# as unambiguous. Below this margin, `confidence_level` returns "low" so the caller (agent
# or CLI) confirms with the user instead of silently picking a close call.
CONFIDENCE_MARGIN_RATIO = 2.0

# Instance-of types that showed up ahead of the real concept in §2.3's worked example
# (a magazine article and a clinical trial outranking the concept on a naive top-hit search).
# Sitelink count already does most of the ranking work, so these demote sharply rather than
# exclude outright: a genuine match misclassified this way is still reachable, just penalized.
NON_CONCEPT_INSTANCE_QIDS = frozenset(
    {
        "Q13442814",  # scholarly article
        "Q30612",  # clinical trial
        "Q191067",  # article (written work published in a periodical)
    }
)
NON_CONCEPT_SCORE_PENALTY = 0.01

# Wikidata properties, in priority order, used to find a nearby concept when the target
# language has no direct article. "subclass of" and "part of" point to the closest broader
# or containing concept (e.g. fasting, dieting) rather than an unrelated topic.
RELATED_CLAIM_PROPERTIES = ("P279", "P361")

# Related candidates tried, in order, before giving up on a proxy. Bounds the number of
# extra API calls the missing-article path can make.
MAX_PROXY_CANDIDATES_TRIED = 5


@dataclass(frozen=True)
class EntityCandidate:
    """One scored Wikidata entity candidate for a searched topic."""

    qid: str
    label: str
    description: str
    sitelink_count: int
    instance_of: list[str]
    score: float


@dataclass(frozen=True)
class EntityResolution:
    """Outcome of resolving a topic string to a ranked set of Wikidata entities."""

    status: str  # "ok" | "no_match"
    candidates: list[EntityCandidate] = field(default_factory=list)
    confidence: str = "low"  # "high" | "low"


@dataclass(frozen=True)
class LanguageResolution:
    """Outcome of resolving one Wikidata entity to an article title in one language."""

    status: str  # "ok" | "missing"
    lang: str
    title: str | None = None
    is_proxy: bool = False
    proxy_qid: str | None = None
    proxy_label: str | None = None


def score_candidate(sitelink_count: int, instance_of: Sequence[str]) -> float:
    """Score one candidate: sitelink count, demoted if it looks like a non-concept entity."""
    score = float(sitelink_count)
    if set(instance_of) & NON_CONCEPT_INSTANCE_QIDS:
        score *= NON_CONCEPT_SCORE_PENALTY
    return score


def confidence_level(candidates: Sequence[EntityCandidate]) -> str:
    """Whether the top-ranked candidate is a clear winner or a close, ambiguous call."""
    if not candidates:
        return "low"
    if candidates[0].score <= 0:
        return "low"
    if len(candidates) == 1:
        return "high"
    runner_up = candidates[1].score
    if runner_up <= 0:
        return "high"
    return "high" if candidates[0].score >= CONFIDENCE_MARGIN_RATIO * runner_up else "low"


def claim_target_qids(claims: dict[str, Any], prop: str) -> list[str]:
    """Extract target-entity QIDs from a Wikidata ``claims`` block for one property.

    Skips statements without a concrete entity value (``novalue``/``somevalue`` snaks).
    """
    qids: list[str] = []
    for statement in claims.get(prop, []):
        datavalue = statement.get("mainsnak", {}).get("datavalue")
        if datavalue is None:
            continue
        value = datavalue.get("value")
        if isinstance(value, dict) and "id" in value:
            qids.append(value["id"])
    return qids


def related_qids_from_claims(claims: dict[str, Any]) -> list[str]:
    """Nearby concepts for proxy lookup: ``subclass of`` then ``part of``, deduped, capped."""
    related: list[str] = []
    seen: set[str] = set()
    for prop in RELATED_CLAIM_PROPERTIES:
        for qid in claim_target_qids(claims, prop):
            if qid not in seen:
                seen.add(qid)
                related.append(qid)
    return related[:MAX_PROXY_CANDIDATES_TRIED]


def parse_sitelinks(entity: dict[str, Any]) -> dict[str, str]:
    """Map ``{site: title}`` from a wbgetentities entity, e.g. ``{"plwiki": "Post przerywany"}``."""
    sitelinks = entity.get("sitelinks", {})
    return {site: data["title"] for site, data in sitelinks.items()}


def parse_label(entity: dict[str, Any], lang: str = "en") -> str | None:
    """Extract the label in ``lang`` from a wbgetentities entity, if present."""
    label = entity.get("labels", {}).get(lang)
    return label["value"] if label else None


def rank_candidates(
    hits: Sequence[dict[str, Any]], entities: dict[str, Any]
) -> list[EntityCandidate]:
    """Score and rank wbsearchentities ``hits`` using sitelink/claim data from ``entities``."""
    candidates = []
    for hit in hits:
        qid = hit["id"]
        entity = entities.get(qid, {})
        sitelinks = entity.get("sitelinks", {})
        instance_of = claim_target_qids(entity.get("claims", {}), "P31")
        candidates.append(
            EntityCandidate(
                qid=qid,
                label=hit.get("label", qid),
                description=hit.get("description", ""),
                sitelink_count=len(sitelinks),
                instance_of=instance_of,
                score=score_candidate(len(sitelinks), instance_of),
            )
        )
    return sorted(candidates, key=lambda c: c.score, reverse=True)


def resolve_entity(client: httpx.Client, topic: str) -> EntityResolution:
    """Search Wikidata for ``topic`` and return the top ranked candidates with confidence."""
    logger.debug("search_entities", topic=topic)
    search_response = _get_json(
        client,
        {
            "action": "wbsearchentities",
            "search": topic,
            "language": "en",
            "type": "item",
            "format": "json",
            "limit": str(SEARCH_LIMIT),
        },
    )
    hits: list[dict[str, Any]] = search_response.get("search", [])
    if not hits:
        logger.info("entity_no_match", topic=topic)
        return EntityResolution(status="no_match")

    entities = _wbgetentities(client, [hit["id"] for hit in hits], props="sitelinks|claims")
    ranked = rank_candidates(hits, entities)
    top = ranked[:TOP_CANDIDATES_SURFACED]
    confidence = confidence_level(top)
    logger.info(
        "entity_resolved",
        topic=topic,
        top_qid=top[0].qid,
        top_score=top[0].score,
        confidence=confidence,
        candidate_count=len(top),
    )
    return EntityResolution(status="ok", candidates=top, confidence=confidence)


def resolve_language(client: httpx.Client, qid: str, lang: str) -> LanguageResolution:
    """Resolve one entity to an article title in ``lang``, falling back to a proxy article.

    A missing article is a first-class ``"missing"`` result (§2.2), not an error. When no
    direct article exists, nearby concepts (``related_qids_from_claims``) are tried in order
    and the first one with an article in ``lang`` is returned as an explicit ``is_proxy=True``
    result; if none has one either, the result stays ``"missing"`` with no proxy.
    """
    wiki_db = f"{lang}wiki"
    entity = _wbgetentities(client, [qid], props="sitelinks|claims").get(qid, {})
    sitelinks = parse_sitelinks(entity)
    if wiki_db in sitelinks:
        logger.info("language_resolved", lang=lang, qid=qid, title=sitelinks[wiki_db])
        return LanguageResolution(status="ok", lang=lang, title=sitelinks[wiki_db])

    logger.info("language_missing", lang=lang, qid=qid)
    for related_qid in related_qids_from_claims(entity.get("claims", {})):
        related_entity = _wbgetentities(client, [related_qid], props="sitelinks|labels").get(
            related_qid, {}
        )
        related_sitelinks = parse_sitelinks(related_entity)
        if wiki_db in related_sitelinks:
            proxy_label = parse_label(related_entity) or related_qid
            logger.info(
                "proxy_found",
                lang=lang,
                qid=qid,
                proxy_qid=related_qid,
                proxy_label=proxy_label,
            )
            return LanguageResolution(
                status="missing",
                lang=lang,
                is_proxy=True,
                title=related_sitelinks[wiki_db],
                proxy_qid=related_qid,
                proxy_label=proxy_label,
            )

    logger.info("no_proxy_found", lang=lang, qid=qid)
    return LanguageResolution(status="missing", lang=lang)


def _wbgetentities(client: httpx.Client, ids: Sequence[str], props: str) -> dict[str, Any]:
    data = _get_json(
        client,
        {
            "action": "wbgetentities",
            "ids": "|".join(ids),
            "props": props,
            "languages": "en",
            "format": "json",
        },
    )
    entities: dict[str, Any] = data.get("entities", {})
    return entities


def _get_json(client: httpx.Client, params: dict[str, str]) -> dict[str, Any]:
    """GET the Wikidata API, retrying transport errors and 429/5xx.

    Unlike the pageviews REST API (fetch.py), Wikidata always answers 200 with a JSON body
    (a missing entity is ``{"missing": ""}`` inside it, not an HTTP 404), so there is no
    404 short-circuit here.
    """
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.get(WIKIDATA_API_URL, params=params)
        except httpx.TransportError as exc:
            last_exc = exc
            logger.warning("http_transport_error", params=params, attempt=attempt, error=str(exc))
            time.sleep(RETRY_BACKOFF_BASE_SECONDS * attempt)
            continue

        if response.status_code in RETRYABLE_STATUS_CODES:
            logger.warning(
                "http_retryable_status",
                params=params,
                status=response.status_code,
                attempt=attempt,
            )
            time.sleep(RETRY_BACKOFF_BASE_SECONDS * attempt)
            continue

        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data

    raise RuntimeError(
        f"exhausted {MAX_RETRIES} retries calling the Wikidata API: {params}"
    ) from last_exc
