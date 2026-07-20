"""
test_rate_limiter.py
----------------------
Tests for core.safety.RateLimiter — confirms the window/wait-time math
still works and that the clock source is time.monotonic(), not the
wall-clock time.time() (which is vulnerable to NTP jumps / manual clock
changes mid-window).

Run:
    python -m pytest tests/test_rate_limiter.py -x -q
"""

from __future__ import annotations

from unittest.mock import patch

from core.safety import RateLimiter


class TestRateLimiterUsesMonotonicClock:
    def test_is_allowed_does_not_call_time_time(self):
        with patch("time.time") as mock_time_time:
            limiter = RateLimiter(rpm=3)
            limiter.is_allowed("chat")
            mock_time_time.assert_not_called()

    def test_wait_time_does_not_call_time_time(self):
        with patch("time.time") as mock_time_time:
            limiter = RateLimiter(rpm=3)
            limiter.is_allowed("chat")
            limiter.wait_time("chat")
            mock_time_time.assert_not_called()

    def test_is_allowed_uses_monotonic(self):
        with patch("time.monotonic", return_value=1000.0) as mock_monotonic:
            limiter = RateLimiter(rpm=3)
            limiter.is_allowed("chat")
            assert mock_monotonic.called


class TestRateLimiterWindowLogic:
    def test_allows_up_to_rpm_then_blocks(self):
        with patch("time.monotonic", return_value=1000.0):
            limiter = RateLimiter(rpm=3)
            assert limiter.is_allowed("chat") is True
            assert limiter.is_allowed("chat") is True
            assert limiter.is_allowed("chat") is True
            assert limiter.is_allowed("chat") is False

    def test_allows_again_after_window_expires(self):
        with patch("time.monotonic", return_value=1000.0):
            limiter = RateLimiter(rpm=1)
            assert limiter.is_allowed("chat") is True
            assert limiter.is_allowed("chat") is False

        with patch("time.monotonic", return_value=1061.0):
            assert limiter.is_allowed("chat") is True

    def test_wait_time_reflects_remaining_window(self):
        with patch("time.monotonic", return_value=1000.0):
            limiter = RateLimiter(rpm=1)
            limiter.is_allowed("chat")

        with patch("time.monotonic", return_value=1010.0):
            assert limiter.wait_time("chat") == 50

    def test_wait_time_is_zero_with_no_history(self):
        limiter = RateLimiter(rpm=3)
        assert limiter.wait_time("never_seen") == 0
