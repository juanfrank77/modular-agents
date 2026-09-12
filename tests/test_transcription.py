"""
test_transcription.py
---------------------
Tests for core/transcription.py — env parsing, lazy model load, error
typing. faster-whisper is mocked throughout: no model download in CI.

Run:
    python -m pytest tests/test_transcription.py -x -q
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import core.transcription as transcription
from core.transcription import TranscriptionError, transcribe


@pytest.fixture(autouse=True)
def _reset_cache():
    transcription._reset_for_tests()
    yield
    transcription._reset_for_tests()


class TestEnvParsing:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("WHISPER_MODEL", raising=False)
        monkeypatch.delenv("WHISPER_DEVICE", raising=False)
        monkeypatch.delenv("WHISPER_COMPUTE_TYPE", raising=False)
        assert transcription.model_name() == "base"
        assert transcription.device() == "cpu"
        assert transcription.compute_type() == "int8"

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("WHISPER_MODEL", "small")
        monkeypatch.setenv("WHISPER_DEVICE", "cuda")
        monkeypatch.setenv("WHISPER_COMPUTE_TYPE", "float16")
        assert transcription.model_name() == "small"
        assert transcription.device() == "cuda"
        assert transcription.compute_type() == "float16"


class TestLazyLoad:
    def test_model_not_loaded_at_import(self):
        assert not transcription.is_loaded()

    def test_transcribe_loads_model_on_first_call(self, monkeypatch, tmp_path):
        wav = tmp_path / "fake.wav"
        wav.write_bytes(b"RIFF")
        fake = MagicMock()
        fake.transcribe.return_value = (iter([SimpleNamespace(text="hello")]), None)
        with patch.object(transcription, "_get_model", return_value=fake):
            result = transcribe(str(wav))
        assert result == "hello"

    def test_segments_are_joined(self, tmp_path):
        wav = tmp_path / "fake.wav"
        wav.write_bytes(b"RIFF")
        fake = MagicMock()
        fake.transcribe.return_value = (
            iter([SimpleNamespace(text="hello "), SimpleNamespace(text="world")]),
            None,
        )
        with patch.object(transcription, "_get_model", return_value=fake):
            assert transcribe(str(wav)) == "hello world"


class TestTranscribeOptions:
    def test_language_is_forced_to_english(self, tmp_path):
        wav = tmp_path / "fake.wav"
        wav.write_bytes(b"RIFF")
        fake = MagicMock()
        fake.transcribe.return_value = (iter([]), None)
        with patch.object(transcription, "_get_model", return_value=fake):
            transcribe(str(wav))
        _, kwargs = fake.transcribe.call_args
        assert kwargs.get("language") == "en"


class TestErrors:
    def test_missing_file_raises_transcription_error(self):
        with pytest.raises(TranscriptionError):
            transcribe("/nonexistent/path/audio.wav")

    def test_model_load_failure_is_wrapped(self):
        with patch.object(
            transcription,
            "_load_model",
            side_effect=RuntimeError("ctranslate2 boom"),
        ):
            with pytest.raises(TranscriptionError):
                transcribe("/tmp/fake.wav")

    def test_transcribe_failure_is_wrapped(self):
        fake = MagicMock()
        fake.transcribe.side_effect = RuntimeError("decode fail")
        with patch.object(transcription, "_get_model", return_value=fake):
            with pytest.raises(TranscriptionError):
                transcribe("/tmp/fake.wav")


class TestGetModel:
    def test_get_model_loads_once_and_caches(self, monkeypatch):
        monkeypatch.setenv("WHISPER_MODEL", "base")
        monkeypatch.setenv("WHISPER_DEVICE", "cpu")
        monkeypatch.setenv("WHISPER_COMPUTE_TYPE", "int8")

        fake_whisper = MagicMock()
        fake_instance = MagicMock()
        fake_whisper.WhisperModel.return_value = fake_instance

        with patch.dict(sys.modules, {"faster_whisper": fake_whisper}):
            first = transcription._get_model()
            second = transcription._get_model()

        assert first is second
        fake_whisper.WhisperModel.assert_called_once_with(
            "base", device="cpu", compute_type="int8"
        )
