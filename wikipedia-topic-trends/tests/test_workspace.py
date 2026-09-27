"""Tests for the run manifest: creation, mutation helpers, and JSON round-trip."""

from __future__ import annotations

from pathlib import Path

from wikitrends.workspace import (
    LanguageRecord,
    create_manifest,
    load_manifest,
    mark_fetched,
    new_run_id,
    save_manifest,
    with_entity,
    with_language,
)


def test_new_run_id_is_unique() -> None:
    assert new_run_id() != new_run_id()


def test_create_manifest_starts_with_no_entity_or_languages() -> None:
    manifest = create_manifest("run_1", "intermittent fasting", 24, "monthly")

    assert manifest.entity_qid is None
    assert manifest.languages == {}
    assert manifest.requested_langs == []


def test_create_manifest_keeps_requested_langs_for_a_later_disambiguation_followup() -> None:
    manifest = create_manifest("run_1", "intermittent fasting", 24, "monthly", ["pl", "cs"])

    assert manifest.requested_langs == ["pl", "cs"]


def test_with_entity_returns_a_new_manifest_without_mutating_the_original() -> None:
    original = create_manifest("run_1", "topic", 24, "monthly")
    updated = with_entity(original, qid="Q1666254", label="intermittent fasting", confidence="high")

    assert original.entity_qid is None
    assert updated.entity_qid == "Q1666254"
    assert updated.entity_confidence == "high"


def test_with_language_adds_and_replaces_by_lang() -> None:
    manifest = create_manifest("run_1", "topic", 24, "monthly")
    manifest = with_language(manifest, LanguageRecord(lang="pl", resolution_status="missing"))
    manifest = with_language(
        manifest, LanguageRecord(lang="pl", resolution_status="ok", title="Post przerywany")
    )

    assert len(manifest.languages) == 1
    assert manifest.languages["pl"].title == "Post przerywany"


def test_mark_fetched_does_not_mutate_the_original_record() -> None:
    record = LanguageRecord(lang="uk", resolution_status="ok", title="x")
    fetched = mark_fetched(record)

    assert record.fetched is False
    assert fetched.fetched is True


def test_load_manifest_returns_none_for_an_unknown_run(tmp_path: Path) -> None:
    assert load_manifest("does_not_exist", base=tmp_path) is None


def test_save_and_load_manifest_round_trips_every_field(tmp_path: Path) -> None:
    manifest = create_manifest("run_1", "intermittent fasting", 36, "monthly", ["pl", "cs"])
    manifest = with_entity(
        manifest, qid="Q1666254", label="intermittent fasting", confidence="high"
    )
    manifest = with_language(
        manifest,
        LanguageRecord(
            lang="pl",
            resolution_status="missing",
            is_proxy=True,
            proxy_qid="Q10",
            proxy_label="fasting",
            title="Post",
            fetched=True,
            analysis={"n_buckets": 36},
            caveats=["some caveat"],
        ),
    )

    save_manifest(manifest, base=tmp_path)
    loaded = load_manifest("run_1", base=tmp_path)

    assert loaded == manifest
