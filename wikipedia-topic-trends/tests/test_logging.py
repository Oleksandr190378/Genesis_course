"""Tests for dual-sink logging setup, in particular handler lifecycle across runs."""

from __future__ import annotations

import contextlib
import io
import logging
from pathlib import Path

from wikitrends import workspace
from wikitrends.logging import configure_logging


def test_configure_logging_closes_the_previous_file_handler(tmp_path: Path) -> None:
    """Windows keeps an exclusive lock on an open file handle.

    A second call must close the first run's ``FileHandler`` (not just drop the reference),
    or the first run's log file stays locked and un-deletable for the life of the process --
    exactly what broke tempdir cleanup in the eval harness on Windows.
    """
    first_log_path = configure_logging("run_a", log_dir=tmp_path)
    first_handler = next(
        h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)
    )

    configure_logging("run_b", log_dir=tmp_path)

    assert first_handler.stream is None or first_handler.stream.closed
    first_log_path.unlink()  # would raise PermissionError on Windows if still locked


def test_a_logger_bound_before_configure_logging_still_respects_it(tmp_path: Path) -> None:
    """Every module does ``logger = get_logger(...)`` at import time -- before this process
    has ever called ``configure_logging`` even once.

    ``wikitrends.workspace.logger`` is exactly that: already bound, long before this test
    runs. With ``cache_logger_on_first_use=True`` (the bug), that first bind would have
    permanently locked it onto structlog's *unconfigured* default -- a ``PrintLogger`` that
    writes straight to ``sys.stdout`` with no level filtering -- and no later call to
    ``configure_logging`` could ever fix it. That's how a DEBUG-level `workspace.py` log line
    ended up inside the CLI's stdout JSON contract and silently broke a caller trying to
    parse it (``evals/harness.py``'s Tier 2 run).
    """
    configure_logging("run_a", log_dir=tmp_path, log_level="INFO")

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        workspace.logger.debug("some_debug_event", detail="should never reach stdout")

    assert captured.getvalue() == ""
