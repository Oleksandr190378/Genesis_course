"""Run manifest: persistent workspace state for incremental CLI commands.

IMPLEMENTATION_PLAN.md §3.2/§11 step 5: a research run is not one-shot. "Now add German"
must reuse whatever ``resolve``/``fetch`` already did instead of re-running the whole
pipeline. Each run gets a ``run_id`` and a JSON manifest under
``.wikitrends/runs/{run_id}.json`` recording the resolved entity and, per language, its
resolution result, whether pageviews have been fetched, and the last analysis — so
composable subcommands (``resolve``, ``fetch --add-langs``, ``analyze``, ``report``) can
load prior state instead of the caller having to reassemble it.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from wikitrends.config import RUNS_DIR_NAME
from wikitrends.logging import get_logger

logger = get_logger(component="workspace")


@dataclass(frozen=True)
class LanguageRecord:
    """Per-language state accumulated across resolve -> fetch -> analyze."""

    lang: str
    resolution_status: str  # "ok" | "missing"
    title: str | None = None
    is_proxy: bool = False
    proxy_qid: str | None = None
    proxy_label: str | None = None
    fetched: bool = False
    analysis: dict[str, Any] | None = None
    caveats: list[str] = field(default_factory=list)
    series: list[float] | None = None


@dataclass(frozen=True)
class RunManifest:
    """Full state of one research run, persisted as JSON."""

    run_id: str
    topic: str
    months: int
    granularity: str
    entity_qid: str | None = None
    entity_label: str | None = None
    entity_confidence: str | None = None
    languages: dict[str, LanguageRecord] = field(default_factory=dict)
    requested_langs: list[str] = field(default_factory=list)


def new_run_id() -> str:
    """A short, sortable, collision-resistant run identifier."""
    return f"run_{int(time.time())}_{uuid.uuid4().hex[:8]}"


def create_manifest(
    run_id: str,
    topic: str,
    months: int,
    granularity: str,
    requested_langs: list[str] | None = None,
) -> RunManifest:
    """Start a fresh manifest for a new run. Does not touch disk; call :func:`save_manifest`.

    ``requested_langs`` is kept on the manifest so a later disambiguation follow-up
    (``resolve --run <id> --qid <QID>``) can resume without the caller repeating them.
    """
    return RunManifest(
        run_id=run_id,
        topic=topic,
        months=months,
        granularity=granularity,
        requested_langs=requested_langs or [],
    )


def with_entity(manifest: RunManifest, qid: str, label: str, confidence: str) -> RunManifest:
    """Return a copy of ``manifest`` with the resolved Wikidata entity attached."""
    return replace(manifest, entity_qid=qid, entity_label=label, entity_confidence=confidence)


def with_language(manifest: RunManifest, record: LanguageRecord) -> RunManifest:
    """Return a copy of ``manifest`` with ``record`` merged into its per-language state."""
    languages = {**manifest.languages, record.lang: record}
    return replace(manifest, languages=languages)


def mark_fetched(record: LanguageRecord) -> LanguageRecord:
    """Return a copy of ``record`` flagged as fetched, so a later ``fetch`` call skips it."""
    return replace(record, fetched=True)


def _runs_dir(base: Path | None) -> Path:
    path = base or Path(RUNS_DIR_NAME)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _manifest_path(run_id: str, base: Path | None) -> Path:
    return _runs_dir(base) / f"{run_id}.json"


def save_manifest(manifest: RunManifest, base: Path | None = None) -> None:
    """Persist ``manifest`` as JSON, overwriting any previous state for its run_id."""
    payload = {
        **{key: value for key, value in asdict(manifest).items() if key != "languages"},
        "languages": {lang: asdict(record) for lang, record in manifest.languages.items()},
    }
    path = _manifest_path(manifest.run_id, base)
    path.write_text(json.dumps(payload), encoding="utf-8")
    logger.debug("manifest_saved", run_id=manifest.run_id, path=str(path))


def load_manifest(run_id: str, base: Path | None = None) -> RunManifest | None:
    """Load a previously saved manifest, or ``None`` if ``run_id`` is unknown."""
    path = _manifest_path(run_id, base)
    if not path.exists():
        logger.debug("manifest_not_found", run_id=run_id)
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    languages = {lang: LanguageRecord(**data) for lang, data in raw["languages"].items()}
    return RunManifest(
        run_id=raw["run_id"],
        topic=raw["topic"],
        months=raw["months"],
        granularity=raw["granularity"],
        entity_qid=raw.get("entity_qid"),
        entity_label=raw.get("entity_label"),
        entity_confidence=raw.get("entity_confidence"),
        languages=languages,
        requested_langs=raw.get("requested_langs", []),
    )
