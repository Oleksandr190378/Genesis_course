"""Synthetic-fixture tests for resolve.py's deterministic ranking and parsing logic.

Real network calls (wbsearchentities/wbgetentities) are not exercised here — recorded HTTP
fixtures for resolve.py are deferred, same as tests/test_fetch.py (see PROGRESS.md). These
tests cover the parts that do not need a network call: candidate scoring/ranking and the
Wikidata JSON parsing helpers, fed hand-built dicts shaped like real API responses.
"""

from __future__ import annotations

from wikitrends.resolve import (
    NON_CONCEPT_SCORE_PENALTY,
    EntityCandidate,
    claim_target_qids,
    confidence_level,
    parse_label,
    parse_sitelinks,
    rank_candidates,
    related_qids_from_claims,
    score_candidate,
)

# IMPLEMENTATION_PLAN.md §2.3: wbsearchentities "intermittent fasting" returns the concept,
# a magazine article about it, and a clinical trial, in that order — top-hit-wins would pick
# the wrong one two times out of three unless sitelink count re-ranks them.
_CONCEPT_QID = "Q1666254"
_MAGAZINE_ARTICLE_QID = "Q112575736"
_CLINICAL_TRIAL_QID = "Q63574657"

_SEARCH_HITS = [
    {"id": _CONCEPT_QID, "label": "intermittent fasting", "description": "eating pattern"},
    {
        "id": _MAGAZINE_ARTICLE_QID,
        "label": "Intermittent Fasting",
        "description": "magazine article",
    },
    {
        "id": _CLINICAL_TRIAL_QID,
        "label": "Intermittent fasting trial",
        "description": "clinical trial",
    },
]

# The concept has many sitelinks and no non-concept instance-of claim; the magazine article
# and clinical trial each have exactly one sitelink (their own enwiki page) and an
# instance-of claim from NON_CONCEPT_INSTANCE_QIDS, matching the real-world shape from §2.3.
_ENTITIES = {
    _CONCEPT_QID: {
        "sitelinks": {f"{lang}wiki": {"title": "x"} for lang in ["en", "cs", "uk", "de"]},
        "claims": {},
    },
    _MAGAZINE_ARTICLE_QID: {
        "sitelinks": {"enwiki": {"title": "Intermittent Fasting (magazine article)"}},
        "claims": {
            "P31": [
                {"mainsnak": {"datavalue": {"value": {"id": "Q191067"}}}},  # article
            ]
        },
    },
    _CLINICAL_TRIAL_QID: {
        "sitelinks": {"enwiki": {"title": "Intermittent fasting trial"}},
        "claims": {
            "P31": [
                {"mainsnak": {"datavalue": {"value": {"id": "Q30612"}}}},  # clinical trial
            ]
        },
    },
}


def test_rank_candidates_puts_the_concept_first() -> None:
    ranked = rank_candidates(_SEARCH_HITS, _ENTITIES)

    assert [c.qid for c in ranked] == [_CONCEPT_QID, _MAGAZINE_ARTICLE_QID, _CLINICAL_TRIAL_QID]
    assert ranked[0].score == 4.0
    # Both non-concept candidates have 1 sitelink, demoted by the same penalty factor.
    assert ranked[1].score == ranked[2].score == 1.0 * NON_CONCEPT_SCORE_PENALTY


def test_rank_candidates_is_a_clear_win_for_the_concept() -> None:
    ranked = rank_candidates(_SEARCH_HITS, _ENTITIES)
    assert confidence_level(ranked) == "high"


def test_score_candidate_penalizes_non_concept_instance_of() -> None:
    plain = score_candidate(sitelink_count=1, instance_of=[])
    demoted = score_candidate(sitelink_count=1, instance_of=["Q30612"])

    assert plain == 1.0
    assert demoted == 1.0 * NON_CONCEPT_SCORE_PENALTY
    assert demoted < plain


def test_confidence_level_is_low_on_a_close_call() -> None:
    close_candidates = [
        EntityCandidate(
            qid="Q1", label="a", description="", sitelink_count=5, instance_of=[], score=5.0
        ),
        EntityCandidate(
            qid="Q2", label="b", description="", sitelink_count=4, instance_of=[], score=4.0
        ),
    ]
    assert confidence_level(close_candidates) == "low"


def test_confidence_level_is_low_with_no_candidates() -> None:
    assert confidence_level([]) == "low"


def test_confidence_level_is_high_with_a_single_candidate() -> None:
    single = [
        EntityCandidate(
            qid="Q1", label="a", description="", sitelink_count=3, instance_of=[], score=3.0
        )
    ]
    assert confidence_level(single) == "high"


def test_claim_target_qids_skips_statements_without_a_value() -> None:
    claims = {
        "P31": [
            {"mainsnak": {"datavalue": {"value": {"id": "Q5"}}}},
            {"mainsnak": {"snaktype": "novalue"}},  # no datavalue at all
        ]
    }
    assert claim_target_qids(claims, "P31") == ["Q5"]


def test_related_qids_from_claims_prioritizes_subclass_then_part_of_and_dedupes() -> None:
    claims = {
        "P279": [{"mainsnak": {"datavalue": {"value": {"id": "Q10"}}}}],
        "P361": [
            {"mainsnak": {"datavalue": {"value": {"id": "Q10"}}}},  # duplicate, dropped
            {"mainsnak": {"datavalue": {"value": {"id": "Q20"}}}},
        ],
    }
    assert related_qids_from_claims(claims) == ["Q10", "Q20"]


def test_parse_sitelinks_maps_site_to_title() -> None:
    entity = {"sitelinks": {"plwiki": {"title": "Post przerywany", "badges": []}}}
    assert parse_sitelinks(entity) == {"plwiki": "Post przerywany"}


def test_parse_sitelinks_empty_when_absent() -> None:
    assert parse_sitelinks({}) == {}


def test_parse_label_returns_value_for_requested_language() -> None:
    entity = {"labels": {"en": {"language": "en", "value": "fasting"}}}
    assert parse_label(entity) == "fasting"


def test_parse_label_none_when_absent() -> None:
    assert parse_label({}) is None
