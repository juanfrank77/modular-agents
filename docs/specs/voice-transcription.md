# Spec: Telegram Voice Message Transcription (self-hosted)

Status: draft — awaiting approval
Date: 2026-09-12

## Summary

Voice notes sent to the bot on Telegram are transcribed locally with
faster-whisper (in-process, no external API) and injected into the existing
message bus as text. No new service, port, or OpenAI dependency.

## Design

### core/transcription.py (new module)

- `transcribe(path: str) -> str` — synchronous function; faster-whisper
  `WhisperModel` loaded lazily on first call (keeps startup fast), cached
  module-level afterwards.
- Model: `base`, forced `language="en"` (skips auto-detect pass).
  Env-configurable: `WHISPER_MODEL` (default `base`),
  `WHISPER_DEVICE` (default `cpu`), `WHISPER_COMPUTE_TYPE` (default `int8`).
- faster-whisper accepts the ffmpeg-converted wav directly.
- On failure (missing ffmpeg, corrupt audio, model load error): raise a
  typed `TranscriptionError`; the interface catches it and replies with a
  short user-facing error instead of crashing the handler.

### interfaces/telegram.py (changes)

- Register a `MessageHandler(filters.VOICE)` in `_register_handlers()`
  (group 0, same group as the existing text handler).
- Handler flow:
  1. Download the .ogg via `file.download_to_drive()` to a temp dir.
  2. Convert with ffmpeg (subprocess): ogg/opus → 16 kHz mono wav.
  3. Run `transcription.transcribe()` in the default executor
     (CPU-bound; must not block the event loop).
  4. Insert the transcript into the bus as a normal text message, prefixed
     `[voice] `, same chat_id/user pairing as a typed message.
- Note: voice notes bypass Telegram's `filters.TEXT`, so there is no
  double-handling risk; the existing duplicate-handler lesson (§6) does not
  apply but tests will assert single dispatch.
- Rate limiting: consumes from the same per-chat `RateLimiter` bucket as
  text messages (one voice note = one message).

### Routing / agents (no changes)

- Transcript enters the normal pipeline: explicit `@tag` → classifier →
  sticky → first-registered. During rollout, voice notes tagged `@projects`
  go to the Projects agent; no feature flag needed.
- `BaseAgent.reply()` prefixing (`[projects]` etc.) works unchanged, so the
  user still sees which agent answered.

### Dependencies

- `faster-whisper` added to requirements (pulls ctranslate2).
- `ffmpeg` must exist on the host — add a startup warning if `shutil.which("ffmpeg")`
  is None; voice messages then fail with the friendly error instead of a
  stack trace.

## Out of scope (for now)

- Spanish / auto language detection (env knob already covers switching to
  `small` + removing `language="en"` later).
- Bot commands sent as voice ("/status" spoken aloud) — transcripts are
  treated as plain text, not commands.
- Streaming/partial transcription.

## Tests (mirroring existing per-module pattern)

- `tests/test_transcription.py`: model-env parsing, lazy-load, error typing;
  mocked faster-whisper (no model download in CI).
- `tests/test_telegram_voice.py`: handler registered exactly once; ffmpeg
  conversion invoked with expected args; transcript delivered to bus with
  `[voice] ` prefix and correct chat_id; TranscriptionError → user-friendly
  reply, no crash; voice message consumes one rate-limit unit.
