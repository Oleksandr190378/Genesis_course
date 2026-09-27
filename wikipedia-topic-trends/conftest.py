"""Ensures the project root is importable as ``evals.*`` regardless of pytest's import mode.

``evals/`` is a plain package (has ``__init__.py``) but is not built into the ``wikitrends``
wheel (see ``[tool.hatch.build.targets.wheel]`` in pyproject.toml) -- it is eval tooling, not
skill content. Pytest's default "prepend" import mode only guarantees the *test file's own*
directory ends up on ``sys.path``, not the project root, so without this, running
``tests/test_eval_judge.py`` (which does ``from evals import judge``) would fail depending on
how pytest is invoked.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
