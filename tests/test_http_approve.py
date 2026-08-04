"""
test_http_approve.py
--------------------
Tests for the POST /approve endpoint and the HTTPNotifier approval callback
path: supervised actions over HTTP must wait for explicit approval via
/approve instead of auto-approving.

Run:
    python -m pytest tests/test_http_approve.py -x -q
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from core.notifier import HTTPNotifier
from core.safety import ActionType, Safety


class _FakeResponse:
    def __init__(self, text: str, agent_name: str, success: bool = True):
        self.text = text
        self.agent_name = agent_name
        self.success = success


def _interface(safety, notifier=None):
    from interfaces.http import HTTPInterface

    bus = MagicMock()
    bus.registered_agents = ["business"]
    settings = MagicMock()
    settings.session_ttl_hours = 24
    settings.max_http_sessions = 10
    settings.http_pair_rate_limit_rpm = 100
    settings.http_admin_rate_limit_rpm = 100
    creator = MagicMock()
    creator.is_active.return_value = False

    return HTTPInterface(
        bus=bus,
        safety=safety,
        creator=creator,
        notifier=notifier or HTTPNotifier(),
        settings=settings,
        state_store=None,
    )


def _paired_client(interface):
    client = TestClient(interface.app)
    token = client.post("/pair", json={"code": interface._safety.pairing.code}).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client, token


class TestApproveEndpoint:
    @pytest.mark.asyncio
    async def test_approve_endpoint_resolves_pending_approval(self):
        notifier = HTTPNotifier()
        safety = Safety(notifier=notifier, allowed_ids=[])
        interface = _interface(safety, notifier)
        client, _token = _paired_client(interface)

        # Simulate an agent that requires approval in the background.
        approval_id_container = {}

        async def agent_task():
            approved = await safety.gate.request_approval(
                chat_id=interface._sessions[_token][0],
                description="Deploy to production",
                action_type=ActionType.WRITE_HIGH,
            )
            return approved

        task = asyncio.create_task(agent_task())
        # Wait for the approval request to be buffered.
        await asyncio.sleep(0.05)
        text = await notifier.get_and_clear(interface._sessions[_token][0])
        assert "approval_id" in text
        # Extract the approval_id from the instructions.
        approval_id = text.split('approval_id": "')[1].split('"')[0]
        approval_id_container["id"] = approval_id

        r = client.post("/approve", json={"approval_id": approval_id, "approved": True})
        assert r.status_code == 200
        assert r.json()["status"] == "approved"

        approved = await asyncio.wait_for(task, timeout=1.0)
        assert approved is True

    @pytest.mark.asyncio
    async def test_approve_endpoint_can_deny(self):
        notifier = HTTPNotifier()
        safety = Safety(notifier=notifier, allowed_ids=[])
        interface = _interface(safety, notifier)
        client, _token = _paired_client(interface)

        async def agent_task():
            return await safety.gate.request_approval(
                chat_id=interface._sessions[_token][0],
                description="Delete database",
                action_type=ActionType.DESTRUCTIVE,
            )

        task = asyncio.create_task(agent_task())
        await asyncio.sleep(0.05)
        text = await notifier.get_and_clear(interface._sessions[_token][0])
        approval_id = text.split('approval_id": "')[1].split('"')[0]

        r = client.post("/approve", json={"approval_id": approval_id, "approved": False})
        assert r.status_code == 200
        assert r.json()["status"] == "denied"

        approved = await asyncio.wait_for(task, timeout=1.0)
        assert approved is False

    @pytest.mark.asyncio
    async def test_approve_endpoint_rejects_wrong_session(self):
        notifier = HTTPNotifier()
        safety = Safety(notifier=notifier, allowed_ids=[])
        interface = _interface(safety, notifier)
        client1, token1 = _paired_client(interface)
        client2, token2 = _paired_client(interface)

        async def agent_task():
            return await safety.gate.request_approval(
                chat_id=interface._sessions[token1][0],
                description="Deploy to production",
                action_type=ActionType.WRITE_HIGH,
            )

        task = asyncio.create_task(agent_task())
        await asyncio.sleep(0.05)
        text = await notifier.get_and_clear(interface._sessions[token1][0])
        approval_id = text.split('approval_id": "')[1].split('"')[0]

        # Session 2 must not be able to resolve session 1's approval.
        r = client2.post("/approve", json={"approval_id": approval_id, "approved": True})
        assert r.status_code == 400

        # The original request should still be pending; resolve it with the
        # correct session to clean up the task.
        r = client1.post("/approve", json={"approval_id": approval_id, "approved": True})
        assert r.status_code == 200
        await asyncio.wait_for(task, timeout=1.0)

    @pytest.mark.asyncio
    async def test_approve_endpoint_rejects_unknown_or_expired_approval(self):
        notifier = HTTPNotifier()
        safety = Safety(notifier=notifier, allowed_ids=[])
        interface = _interface(safety, notifier)
        client, _token = _paired_client(interface)

        r = client.post("/approve", json={"approval_id": "nosuchid", "approved": True})
        assert r.status_code == 400

    def test_approve_endpoint_requires_auth(self):
        notifier = HTTPNotifier()
        safety = Safety(notifier=notifier, allowed_ids=[])
        interface = _interface(safety, notifier)
        client = TestClient(interface.app)

        r = client.post("/approve", json={"approval_id": "x", "approved": True})
        assert r.status_code == 401


