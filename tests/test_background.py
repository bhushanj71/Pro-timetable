"""
Backoff behaviour of the in-process reminder loop.

A database that rejects every connection used to be retried once a minute
forever, which kept Supabase's pooler circuit breaker tripped and buried the
log in identical stack traces.
"""
import asyncio

import pytest

from app.services import background
from app.services.background import MAX_BACKOFF_SECONDS, _backoff


def test_backoff_grows_and_is_capped():
    assert [_backoff(60, n) for n in range(1, 5)] == [60, 120, 240, 480]
    assert _backoff(60, 20) == MAX_BACKOFF_SECONDS


def test_loop_backs_off_while_the_database_is_down(monkeypatch):
    """Consecutive failures must space out, not hammer once a minute."""
    slept: list[int] = []
    attempts = {"n": 0}

    def always_fails():
        attempts["n"] += 1
        raise RuntimeError("connection failed: ECIRCUITBREAKER")

    async def fake_sleep(seconds):
        slept.append(seconds)
        if len(slept) >= 4:
            raise asyncio.CancelledError

    monkeypatch.setattr(background, "_process_once", always_fails)
    monkeypatch.setattr(background.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(background.settings, "REMINDER_POLL_SECONDS", 60, raising=False)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(background.reminder_loop())

    assert slept == [60, 120, 240, 480]
    assert attempts["n"] == 4


def test_a_success_resets_the_backoff(monkeypatch):
    slept: list[int] = []
    calls = {"n": 0}

    def fails_then_recovers():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("db down")
        return {"sent": 0}

    async def fake_sleep(seconds):
        slept.append(seconds)
        if len(slept) >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(background, "_process_once", fails_then_recovers)
    monkeypatch.setattr(background.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(background.settings, "REMINDER_POLL_SECONDS", 60, raising=False)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(background.reminder_loop())

    assert slept == [60, 120, 60], "recovery must return to the normal interval"
