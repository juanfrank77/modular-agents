"""
test_http_port_busy.py
----------------------
Port-conflict degradation for the HTTP interface.

Regression tests for the 2026-10-23 systemd crash loop: a second instance
held port 8080, uvicorn responded to the Errno 98 bind failure with
sys.exit(1), and that SystemExit — a BaseException — escaped
_run_http_safe's `except Exception` in main.py and killed the whole system
instead of degrading gracefully as designed.

Covers:
  1. _port_is_free probe: True on a free port, False on a bound one.
  2. HTTPInterface.run() returns cleanly (no raise, no uvicorn) when the
     port is already bound.
  3. main._run_http_safe logs a warning instead of propagating SystemExit.

Run:
    python -m pytest tests/test_http_port_busy.py -x -q
"""

from __future__ import annotations

import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from interfaces.http import HTTPInterface, _port_is_free


def _settings(host: str = "127.0.0.1", port: int = 0) -> SimpleNamespace:
    # Only the attributes run() and __init__ touch.
    return SimpleNamespace(
        http_host=host,
        http_port=port,
        http_pair_rate_limit_rpm=10,
        http_admin_rate_limit_rpm=10,
    )


def _interface(host: str = "127.0.0.1", port: int = 0) -> HTTPInterface:
    return HTTPInterface(
        bus=MagicMock(),
        safety=MagicMock(),
        creator=MagicMock(),
        notifier=MagicMock(),
        settings=_settings(host, port),
    )


async def test_port_is_free_on_unused_port() -> None:
    # Bind-and-release an ephemeral port: once released it is free again.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert _port_is_free("127.0.0.1", port) is True


async def test_port_is_not_free_when_bound() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        assert _port_is_free("127.0.0.1", port) is False


async def test_run_returns_cleanly_when_port_busy() -> None:
    # Occupy a port, then point the interface at it: run() must return
    # without raising and without starting uvicorn (no server coroutine).
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        intf = _interface(port=port)
        await asyncio_wait_for(intf.run(), timeout=5)


async def test_run_http_safe_swallows_system_exit() -> None:
    # The live-incident path: interface.run() raises SystemExit(1) (uvicorn's
    # bind-failure behaviour). _run_http_safe must log, not propagate.
    import main

    interface = MagicMock()
    interface.run = AsyncMock(side_effect=SystemExit(1))
    # Must not raise:
    await main._run_http_safe(interface)


async def test_run_http_safe_reraises_cancelled() -> None:
    # CancelledError is the shutdown signal — it must still propagate.
    import asyncio

    import main

    interface = MagicMock()
    interface.run = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await main._run_http_safe(interface)


async def asyncio_wait_for(coro, timeout: float) -> None:
    import asyncio

    await asyncio.wait_for(coro, timeout=timeout)
