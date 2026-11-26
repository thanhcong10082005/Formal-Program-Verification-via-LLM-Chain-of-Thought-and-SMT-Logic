"""Shared pytest fixtures and suite markers for the astverifier test suite.

Unit and contract tests must remain completely deterministic and offline even
when a developer keeps a real ``GEMINI_API_KEY`` in ``.env`` or their shell
environment.  This file provides:

1. An autouse fixture that disables live Gemini API resolution during tests.
2. Automatic suite markers (``pipeline``, ``baseline``, ``dataset``) inferred
   from the test subdirectory so each layer can be invoked independently via
   ``pytest -m pipeline``, ``pytest -m baseline``, ``pytest -m dataset``, or
   all together via ``pytest``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_DIR_TO_MARKER = {
    "pipeline": pytest.mark.pipeline,
    "baselines": pytest.mark.baseline,
    "dataset": pytest.mark.dataset,
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Attach suite markers automatically based on the test subdirectory."""
    tests_root = Path(__file__).resolve().parent
    for item in items:
        try:
            rel_parts = Path(str(item.path)).resolve().relative_to(tests_root).parts
        except ValueError:
            continue
        if rel_parts and rel_parts[0] in _DIR_TO_MARKER:
            item.add_marker(_DIR_TO_MARKER[rel_parts[0]])


@pytest.fixture(autouse=True)
def _disable_live_gemini_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent tests from silently making live Gemini API calls via ``.env``."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    import astverifier.core.pipeline as pipeline_mod
    import astverifier.llm.gemini as gemini_mod

    monkeypatch.setattr(gemini_mod, "resolve_api_key", lambda explicit_key=None: None)
    monkeypatch.setattr(
        pipeline_mod, "resolve_api_key", lambda explicit_key=None: None
    )
