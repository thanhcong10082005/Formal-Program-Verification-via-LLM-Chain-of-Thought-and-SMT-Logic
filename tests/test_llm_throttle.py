import sys

import pytest

sys.path.insert(0, "src")

import astverifier.llm as llm


def test_flash_lite_uses_safe_default_request_interval(monkeypatch):
    monkeypatch.delenv(llm.REQUEST_INTERVAL_ENV, raising=False)

    assert llm._request_interval_seconds("gemini-3.5-flash-lite") == pytest.approx(4.2)
    assert llm._request_interval_seconds("gemini-3.5-flash") == 0.0


def test_request_interval_can_be_overridden(monkeypatch):
    monkeypatch.setenv(llm.REQUEST_INTERVAL_ENV, "1.25")
    assert llm._request_interval_seconds("gemini-3.5-flash-lite") == pytest.approx(1.25)

    monkeypatch.setenv(llm.REQUEST_INTERVAL_ENV, "0")
    assert llm._request_interval_seconds("gemini-3.5-flash-lite") == 0.0


def test_request_interval_rejects_invalid_values(monkeypatch):
    monkeypatch.setenv(llm.REQUEST_INTERVAL_ENV, "not-a-number")
    with pytest.raises(ValueError, match="non-negative number"):
        llm._request_interval_seconds("gemini-3.5-flash-lite")

    monkeypatch.setenv(llm.REQUEST_INTERVAL_ENV, "-1")
    with pytest.raises(ValueError, match="non-negative"):
        llm._request_interval_seconds("gemini-3.5-flash-lite")


def test_throttle_reserves_shared_slots(monkeypatch):
    now = [100.0]
    sleeps = []

    monkeypatch.setattr(llm, "_request_interval_seconds", lambda model: 4.2)
    throttle = llm._GeminiRequestThrottle(
        clock=lambda: now[0],
        sleeper=sleeps.append,
    )

    throttle.wait_for_slot("gemini-3.5-flash-lite")
    throttle.wait_for_slot("gemini-3.5-flash-lite")
    throttle.wait_for_slot("gemini-3.5-flash-lite")

    assert sleeps == pytest.approx([4.2, 8.4])
