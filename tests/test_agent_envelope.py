"""
test_agent_envelope.py
----------------------
Tests for bounded AgentEvent envelopes (10.5).

Run:
    python3 -m pytest tests/test_agent_envelope.py -x -q
"""
from __future__ import annotations

from core.protocols import AgentEvent, EventType, _MAX_ENVELOPE_BYTES


def _make_event(text="", data=None, **kwargs):
    return AgentEvent(
        type=EventType.USER_MESSAGE,
        agent_name="test",
        chat_id="123",
        text=text,
        data=data or {},
        **kwargs,
    )


class TestAgentEventPriority:
    def test_default_priority_is_zero(self):
        event = _make_event()
        assert event.priority == 0

    def test_priority_can_be_set(self):
        event = _make_event(priority=5)
        assert event.priority == 5

    def test_negative_priority_allowed(self):
        event = _make_event(priority=-1)
        assert event.priority == -1


class TestAgentEventEnvelopeBounds:
    def test_small_event_unchanged(self):
        event = _make_event(text="hello", data={"key": "value"})
        assert event.text == "hello"
        assert event.data == {"key": "value"}

    def test_oversized_text_truncated(self):
        big_text = "x" * (_MAX_ENVELOPE_BYTES // 2 + 1)
        event = _make_event(text=big_text)
        assert len(event.text.encode("utf-8")) <= _MAX_ENVELOPE_BYTES // 2

    def test_oversized_data_value_truncated(self):
        big_value = "y" * (_MAX_ENVELOPE_BYTES // 4 + 1)
        event = _make_event(data={"payload": big_value})
        assert len(event.data["payload"].encode("utf-8")) <= _MAX_ENVELOPE_BYTES // 4

    def test_non_string_data_value_truncated(self):
        big_list = ["x" * 100] * 1000
        event = _make_event(data={"payload": big_list})
        assert len(str(event.data["payload"]).encode("utf-8")) <= _MAX_ENVELOPE_BYTES // 4

    def test_multibyte_text_truncated_in_bytes(self):
        big_text = "é" * (_MAX_ENVELOPE_BYTES // 2 + 1)
        event = _make_event(text=big_text)
        assert len(event.text.encode("utf-8")) <= _MAX_ENVELOPE_BYTES // 2

    def test_too_many_data_keys_capped(self):
        data = {f"key{i}": f"value{i}" for i in range(100)}
        event = _make_event(data=data)
        assert len(event.data) == 50

    def test_dataclasses_replace_preserves_bounds(self):
        event = _make_event(text="hello")
        replaced = AgentEvent(
            type=event.type,
            agent_name=event.agent_name,
            chat_id=event.chat_id,
            text=event.text,
            data=event.data,
        )
        assert replaced.text == "hello"

    def test_priority_survives_dataclasses_replace(self):
        event = _make_event(priority=7)
        replaced = AgentEvent(
            type=event.type,
            agent_name=event.agent_name,
            chat_id=event.chat_id,
            priority=event.priority,
        )
        assert replaced.priority == 7

    def test_nested_non_string_data_values_preserved(self):
        event = _make_event(data={"count": 42, "flag": True, "items": [1, 2, 3]})
        assert event.data["count"] == 42
        assert event.data["flag"] is True
        assert event.data["items"] == [1, 2, 3]
