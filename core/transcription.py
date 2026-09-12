"""
core/transcription.py
----------------------
Self-hosted speech-to-text for Telegram voice messages, via faster-whisper
(CTranslate2). No OpenAI dependency: the model runs in-process on CPU.

The WhisperModel is loaded lazily on the first transcribe() call so the
bot's startup stays fast; it is cached module-level for the process
lifetime. Model/device/compute-type are env-configurable:

    WHISPER_MODEL          (default "base")
    WHISPER_DEVICE         (default "cpu")
    WHISPER_COMPUTE_TYPE   (default "int8")

Language is forced to English (language="en"), which skips the
auto-detect pass. To support other languages later, change the constant
below and/or switch WHISPER_MODEL to "small".
"""

from __future__ import annotations

import os
from typing import Optional

from core.logger import get_logger

log = get_logger("transcription")

# Voice messages are English-only for now (spec: docs/specs/voice-transcription.md).
FORCED_LANGUAGE = "en"


class TranscriptionError(Exception):
    """Raised when audio cannot be transcribed (missing file, model load
    failure, decode error). Interfaces catch this and reply with a
    user-friendly message."""


def model_name() -> str:
    return os.getenv("WHISPER_MODEL", "base")


def device() -> str:
    return os.getenv("WHISPER_DEVICE", "cpu")


def compute_type() -> str:
    return os.getenv("WHISPER_COMPUTE_TYPE", "int8")


_model = None
_load_error: Optional[Exception] = None


def is_loaded() -> bool:
    return _model is not None


def _load_model():
    from faster_whisper import WhisperModel

    log.info(
        "Loading whisper model",
        event="whisper_load",
        model=model_name(),
        device=device(),
        compute_type=compute_type(),
    )
    return WhisperModel(model_name(), device=device(), compute_type=compute_type())


def _get_model():
    global _model, _load_error
    if _model is None:
        if _load_error is not None:
            # Re-raise a fresh TranscriptionError per call; the underlying
            # exception was a load failure, so retrying the load is pointless.
            raise TranscriptionError(f"Whisper model unavailable: {_load_error}")
        try:
            _model = _load_model()
        except Exception as exc:  # noqa: BLE001 - wrap any loader failure
            _load_error = exc
            _model = None
            raise TranscriptionError(f"Failed to load whisper model: {exc}") from exc
    return _model


def transcribe(path: str) -> str:
    """Transcribe a 16 kHz wav file to text. Raises TranscriptionError on
    any failure. Synchronous and CPU-bound — callers must not run it on
    the event loop."""
    if not os.path.isfile(path):
        raise TranscriptionError(f"Audio file not found: {path}")

    try:
        model = _get_model()
        segments, _info = model.transcribe(path, language=FORCED_LANGUAGE)
        text = "".join(segment.text for segment in segments).strip()
    except TranscriptionError:
        raise
    except Exception as exc:  # noqa: BLE001 - wrap any whisper failure
        raise TranscriptionError(f"Transcription failed: {exc}") from exc

    log.info("Transcribed audio", event="transcribed", path=path, chars=len(text))
    return text


def _reset_for_tests() -> None:
    """Clear the cached model between tests. Not for production use."""
    global _model, _load_error
    _model = None
    _load_error = None
