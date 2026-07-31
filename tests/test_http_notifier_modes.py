"""
test_http_notifier_modes.py
----------------------------
Tests for HTTPNotifier's polling/SSE dual-write leak (#33) and the
SSE queue cleanup leak (#30).

Run:
    python -m pytest tests/test_http_notifier_modes.py -x -q
"""

from __future__ import annotations

import asyncio

import pytest

from core.notifier import HTTPNotifier


class TestPollingOnlyDoesNotLeakQueues:
    @pytest.mark.asyncio
    async def test_send_without_active_stream_does_not_create_queue(self):
        notifier = HTTPNotifier()
        await notifier.send("chat1", "hello")

        assert "chat1" not in notifier._queues
        assert notifier.get_and_clear("chat1") == "hello"

    @pytest.mark.asyncio
    async def test_repeated_polling_sends_never_populate_queues(self):
        notifier = HTTPNotifier()
        for i in range(5):
            await notifier.send("chat1", f"msg{i}")

        assert notifier._queues == {}


class TestSSEOnlyDoesNotLeakBuffers:
    @pytest.mark.asyncio
    async def test_send_during_active_stream_does_not_buffer(self):
        notifier = HTTPNotifier()
        done_event = asyncio.Event()
        notifier.start_stream("chat1")

        async def consume():
            items = []
            async for item in notifier.stream_queue("chat1", done_event):
                items.append(item)
            return items

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0.01)

        await notifier.send("chat1", "hello")
        await notifier.notify_done("chat1", "final")
        done_event.set()

        items = await consumer
        notifier.end_stream("chat1")

        assert ("notification", "hello") in items
        assert ("done", "final") in items
        assert notifier._buffers.get("chat1", []) == []


class TestSSEQueueCleanup:
    @pytest.mark.asyncio
    async def test_end_stream_after_normal_completion_drops_queue(self):
        notifier = HTTPNotifier()
        done_event = asyncio.Event()
        notifier.start_stream("chat1")

        async def consume():
            # No early break: the generator's own while-condition ends the
            # loop once done_event is set and the queue is drained, same as
            # interfaces/http.py's sse_events() does.
            async for _item in notifier.stream_queue("chat1", done_event):
                pass

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0.01)

        await notifier.notify_done("chat1", "final")
        done_event.set()
        await consumer
        notifier.end_stream("chat1")

        assert "chat1" not in notifier._queues
        assert "chat1" not in notifier._streaming

    @pytest.mark.asyncio
    async def test_end_stream_after_task_cancellation_drops_queue(self):
        notifier = HTTPNotifier()
        done_event = asyncio.Event()
        notifier.start_stream("chat1")

        async def consume():
            async for _item in notifier.stream_queue("chat1", done_event):
                pass

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0.01)
        assert "chat1" in notifier._queues

        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer
        notifier.end_stream("chat1")

        assert "chat1" not in notifier._queues
        assert "chat1" not in notifier._streaming

    @pytest.mark.asyncio
    async def test_send_after_end_stream_falls_back_to_buffer(self):
        """Once a stream ends, a late send() (e.g. from a publish_task that
        outlived a client disconnect) must not resurrect the queue — it
        should land in _buffers instead, matching the doc's "reordering
        start_stream/end_stream would silently drop notifications" warning
        in reverse: after end_stream, SSE-mode really is off."""
        notifier = HTTPNotifier()
        notifier.start_stream("chat1")
        notifier.end_stream("chat1")

        await notifier.send("chat1", "late")

        assert "chat1" not in notifier._queues
        assert notifier.get_and_clear("chat1") == "late"

    @pytest.mark.asyncio
    async def test_send_does_not_resurrect_a_dropped_queue(self):
        """Defensive case flagged in review: if _streaming and _queues ever
        end up desynced (chat_id still marked streaming but its queue entry
        already gone), send()/notify_done() must not recreate the queue —
        that queue would have no consumer and leak, and could deliver stale
        messages to a later, unrelated stream for the same chat_id."""
        notifier = HTTPNotifier()
        notifier._streaming.add("chat1")  # desynced on purpose

        await notifier.send("chat1", "hello")
        await notifier.notify_done("chat1", "final")

        assert "chat1" not in notifier._queues
        assert notifier.get_and_clear("chat1") == "hello"


class TestSSEEndpointDisconnectCleanup:
    @pytest.mark.asyncio
    async def test_disconnect_mid_stream_runs_end_stream(self):
        """Mirrors interfaces/http.py's sse_events() shape: start_stream()
        before the loop, end_stream() in a finally around it. A disconnect
        interrupts the wrapper coroutine mid-`async for` (Starlette does
        this via GeneratorExit; a cancelled task hits the same finally) —
        verifies the outer try/finally cleans up even though stream_queue()
        itself has no finally of its own to rely on."""
        notifier = HTTPNotifier()
        entered = asyncio.Event()

        async def sse_events(chat_id: str):
            done_event = asyncio.Event()
            notifier.start_stream(chat_id)
            try:
                async for _msg_type, _msg_text in notifier.stream_queue(
                    chat_id, done_event
                ):
                    yield _msg_type  # pragma: no cover - no data is ever sent
            finally:
                notifier.end_stream(chat_id)

        async def drive():
            gen = sse_events("chat1")
            entered.set()
            async for _ in gen:
                pass

        task = asyncio.create_task(drive())
        await entered.wait()
        await asyncio.sleep(0.01)
        assert "chat1" in notifier._queues

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert "chat1" not in notifier._queues
        assert "chat1" not in notifier._streaming
