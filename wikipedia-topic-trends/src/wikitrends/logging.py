"""Structured logging setup: stderr (human) + .wikitrends/logs/{run_id}.jsonl (machine).

Two independent sinks share the same event stream but render and filter differently:

- stderr: human-readable console output, level controlled by ``log_level`` /
  ``WIKITRENDS_LOG_LEVEL`` (default INFO). Never stdout — stdout carries the JSON contract
  the calling agent parses, and a log line leaking there would corrupt tool output.
- ``.wikitrends/logs/{run_id}.jsonl``: machine-readable JSON lines, always DEBUG. This file
  is the audit trail for every resolution, fetch, and trust-rule decision (see
  IMPLEMENTATION_PLAN.md §6.2) and doubles as the eval harness's instrumentation source, so
  it must never be filtered by ``log_level``.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

import structlog

_DEFAULT_LEVEL = "INFO"
_ENV_LEVEL_VAR = "WIKITRENDS_LOG_LEVEL"


def configure_logging(
    run_id: str,
    log_level: str | None = None,
    log_dir: Path | None = None,
) -> Path:
    """Configure dual-sink structured logging for one run and return the JSONL log path.

    Idempotent: safe to call once per process/run. Binds ``run_id`` into every subsequent
    log event via structlog's contextvars, so callers do not need to pass it explicitly.
    """
    level_name = log_level or os.environ.get(_ENV_LEVEL_VAR, _DEFAULT_LEVEL)
    level = getattr(logging, level_name.upper(), logging.INFO)

    resolved_log_dir = log_dir or Path(".wikitrends") / "logs"
    resolved_log_dir.mkdir(parents=True, exist_ok=True)
    log_path = resolved_log_dir / f"{run_id}.jsonl"

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        # NOT cache_logger_on_first_use: every module in this package does
        # `logger = get_logger(...)` at import time, i.e. before this function has ever run.
        # Caching would permanently bind those loggers to structlog's *unconfigured* default
        # (a PrintLogger writing straight to sys.stdout, bypassing level filtering and this
        # module's handlers entirely) the first time each one is used, and no later call to
        # this function could ever undo it.
        cache_logger_on_first_use=False,
    )

    console_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
        ],
    )
    json_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(level)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(json_formatter)
    file_handler.setLevel(logging.DEBUG)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    for old_handler in root_logger.handlers:
        old_handler.close()
    root_logger.handlers = [console_handler, file_handler]

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(run_id=run_id)

    return log_path


def shutdown_logging() -> None:
    """Close and detach every handler on the root logger.

    ``configure_logging`` only closes a *previous* handler when a new one replaces it, so the
    most recent run's ``FileHandler`` stays open -- and, on Windows, its file stays locked --
    until something calls this. Callers that delete a run's log file or its parent directory
    right after finishing (e.g. the eval harness's per-scenario tempdir) must call this first.
    """
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.close()
    root_logger.handlers = []


def get_logger(**initial_values: object) -> Any:
    """Return a structlog logger bound with the given initial key/value context.

    Every module in this package calls this at *import* time, i.e. before
    ``configure_logging`` has ever run. ``structlog.get_logger(**initial_values)`` -- as
    opposed to ``structlog.get_logger().bind(**initial_values)`` -- stays a lazy proxy rather
    than eagerly resolving against structlog's global config: ``BoundLoggerLazyProxy.bind()``
    (see structlog's own ``_config.py``) immediately builds a concrete logger from whatever
    ``structlog.configure()`` last set, and calling it before that first call happens would
    permanently freeze the logger onto structlog's *built-in* defaults -- a ``PrintLogger``
    that writes straight to ``sys.stdout`` with no level filtering, bypassing this module's
    handlers entirely for the rest of the process. Returning the lazy proxy defers that
    resolution to the first real ``.debug()``/``.info()`` call, by which point
    ``configure_logging`` has run. The return type is ``Any`` to match, matching
    ``structlog.get_logger``'s own public signature.
    """
    return structlog.get_logger(**initial_values)
