"""
interfaces/http.py
------------------
HTTP REST API interface. Exposes the agent bus over FastAPI.

Pairing flow:
  1. POST /pair with the code printed at startup → receive a session token (UUID)
  2. Use the token in Authorization: Bearer <token> for all other requests

Endpoints:
  POST /pair             — exchange pairing code for session token
  DELETE /session        — revoke session token (logout)
  POST /message          — send a message to an agent (returns JSON response)
  POST /message/stream   — stream agent responses via Server-Sent Events
  POST /approve          — resolve an approval request (requires session token)
  GET  /agents           — list registered agents
  GET  /health           — system health (no auth required)
  GET  /model            — current per-chat model override and global default
  POST /model            — set per-chat model override for this session
  DELETE /model          — clear per-chat model override for this session
  POST /admin/unlock     — unlock a locked-out chat_id (requires pairing code)
  GET  /admin/sessions   — list active HTTP sessions (requires pairing code)
  DELETE /admin/sessions/{token} — revoke a specific session (requires pairing code)
  DELETE /admin/sessions — revoke all HTTP sessions (requires pairing code)

Session tokens expire after SESSION_TTL_HOURS (default: 24). The total
number of concurrent sessions is capped by MAX_HTTP_SESSIONS (default: 10).
Pairing is rate-limited per client IP by HTTP_PAIR_RATE_LIMIT_RPM.
Sessions persist across restarts via StateStore, with expired tokens pruned
on access.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.logger import get_logger
from core.protocols import AgentEvent, EventType
from core.routing import parse_agent_tag
from core.safety import RateLimiter

if TYPE_CHECKING:
    from core.bus import MessageBus
    from core.config import Settings
    from core.notifier import HTTPNotifier
    from core.safety import Safety
    from core.agent_creator import AgentCreator
    from core.state_store import StateStore

log = get_logger("http_interface")

_bearer = HTTPBearer(auto_error=False)


class PairRequest(BaseModel):
    code: str


class UnlockRequest(BaseModel):
    code: str
    chat_id: str


class MessageRequest(BaseModel):
    text: str
    agent: str = ""


class ApproveRequest(BaseModel):
    approval_id: str
    approved: bool


class ModelRequest(BaseModel):
    model: str


class HTTPInterface:
    def __init__(
        self,
        bus: "MessageBus",
        safety: "Safety",
        creator: "AgentCreator",
        notifier: "HTTPNotifier",
        settings: "Settings",
        state_store: "StateStore | None" = None,
        on_chat_paired: "Callable[[str], None] | None" = None,
        on_chat_revoked: "Callable[[str], None] | None" = None,
    ) -> None:
        self._bus = bus
        self._safety = safety
        self._creator = creator
        self._notifier = notifier
        self._settings = settings
        self._state_store = state_store
        self._sessions: dict[str, tuple[str, float]] = {}  # token → (chat_id, created_at_ts)
        self._pair_rate_limiter = RateLimiter(rpm=self._settings.http_pair_rate_limit_rpm)
        # Notifies a RouterNotifier (if any) which concrete notifier serves a
        # chat_id, so delivery dispatch doesn't need to re-derive it from
        # chat_id's "http_..." prefix — see core/notifier.py's #45 fix.
        # Optional and decoupled from RouterNotifier's concrete type so
        # HTTPInterface stays constructible without one (tests, non-router
        # setups). Must be synchronous — called from both sync methods
        # (_prune_expired_sessions, _is_session_valid) and async endpoint
        # handlers; an async callback here would just hand back an
        # unawaited coroutine that's silently dropped.
        self._on_chat_paired = on_chat_paired
        self._on_chat_revoked = on_chat_revoked
        self.app = self._build_app()

    def _notify_paired(self, chat_id: str) -> None:
        if self._on_chat_paired is not None:
            self._on_chat_paired(chat_id)

    def _notify_revoked(self, chat_id: str) -> None:
        if self._on_chat_revoked is not None:
            self._on_chat_revoked(chat_id)

    async def load_sessions(self) -> None:
        """Rehydrate session tokens from the state store, dropping any past
        their TTL. Called once at startup, after construction."""
        if not self._state_store:
            return
        ttl_seconds = self._settings.session_ttl_hours * 3600
        now = datetime.now(timezone.utc).timestamp()
        loaded = await self._state_store.load_http_sessions()
        for token, (chat_id, created_at) in loaded.items():
            if (now - created_at) < ttl_seconds:
                self._sessions[token] = (chat_id, created_at)
                # PairingManager._trusted_interfaces (see core/safety.py's
                # #45 fix) is process-memory-only, not its own state_store
                # table — re-pairing here rebuilds it for sessions that
                # survive a restart, same as the initial /pair call does.
                await self._safety.pairing.pair_directly(
                    chat_id, trusted_interface=False
                )
                self._notify_paired(chat_id)
            else:
                await self._state_store.delete_http_session(token)
        log.info(
            "HTTP sessions loaded",
            event="http_sessions_loaded",
            count=len(self._sessions),
        )

    def _prune_expired_sessions(self) -> None:
        """Remove expired session tokens from memory and state store."""
        ttl_seconds = self._settings.session_ttl_hours * 3600
        now = datetime.now(timezone.utc).timestamp()
        expired = [
            (token, chat_id) for token, (chat_id, created_at) in self._sessions.items()
            if (now - created_at) >= ttl_seconds
        ]
        for token, chat_id in expired:
            del self._sessions[token]
            self._notify_revoked(chat_id)
        if expired and self._state_store:
            for token, _chat_id in expired:
                asyncio.create_task(self._state_store.delete_http_session(token))

    def _is_session_valid(self, token: str) -> bool:
        """Check if token exists and hasn't expired. Also prunes expired tokens."""
        if token not in self._sessions:
            return False
        ttl_seconds = self._settings.session_ttl_hours * 3600
        chat_id, created_at = self._sessions[token]
        if (datetime.now(timezone.utc).timestamp() - created_at) >= ttl_seconds:
            del self._sessions[token]
            self._notify_revoked(chat_id)
            if self._state_store:
                asyncio.create_task(self._state_store.delete_http_session(token))
            return False
        return True

    def _require_admin_code(self, code: str) -> None:
        """Raise 403 if the supplied code does not match the current pairing code.

        Uses the constant-time verifier to avoid leaking timing information.
        """
        if not self._safety.pairing.verify_code(code):
            raise HTTPException(status_code=403, detail="invalid admin code")

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="modular-agents HTTP API", docs_url=None, redoc_url=None)

        @app.post("/pair")
        async def pair(req: PairRequest, request: Request):
            # Rate-limit pairing by client IP BEFORE validating the code so wrong-code
            # brute-force attempts consume the bucket too.
            client_host = request.client.host if request.client else "unknown"
            pair_limit_msg = self._pair_rate_limiter.check(f"pair:{client_host}")
            if pair_limit_msg:
                raise HTTPException(status_code=429, detail=pair_limit_msg)

            # Use a stable synthetic chat_id for the pairing attempt so lockout
            # and failed-attempt counting apply even though the real HTTP
            # session chat_id is minted only after the code is accepted.
            pair_chat_id = f"http_pair:{client_host}"
            if not await self._safety.pairing.verify_code_with_lockout(
                pair_chat_id, req.code
            ):
                if self._safety.pairing.is_locked(pair_chat_id):
                    detail = "Too many failed pairing attempts. Pairing is locked."
                else:
                    remaining = self._safety.pairing.attempts_remaining(pair_chat_id)
                    detail = f"Invalid code. {remaining} attempts remaining."
                raise HTTPException(status_code=403, detail=detail)

            # Enforce a hard cap on total active HTTP sessions so one leaked code
            # cannot mint tokens forever.
            self._prune_expired_sessions()
            if len(self._sessions) >= self._settings.max_http_sessions:
                log.warning(
                    "HTTP session cap reached",
                    event="http_session_cap_reached",
                    active_sessions=len(self._sessions),
                    max_sessions=self._settings.max_http_sessions,
                    client_host=client_host,
                )
                raise HTTPException(
                    status_code=503,
                    detail="Maximum number of active HTTP sessions reached. "
                    "Revoke an existing session before creating a new one.",
                )

            token = str(uuid.uuid4())
            chat_id = f"http_{token[:8]}"
            created_at = datetime.now(timezone.utc).timestamp()
            self._sessions[token] = (chat_id, created_at)
            if self._state_store:
                await self._state_store.save_http_session(token, chat_id, created_at)
            # HTTP sessions are explicitly *not* trusted for approvals: the HTTP
            # interface now has its own callback path (POST /approve), so
            # supervised actions must wait for explicit approval.
            await self._safety.pairing.pair_directly(
                chat_id, trusted_interface=False
            )
            self._notify_paired(chat_id)
            log.info(
                "HTTP session paired",
                event="http_paired",
                chat_id=chat_id,
                client_host=client_host,
            )
            return {"token": token}

        @app.delete("/session")
        async def delete_session(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)):
            """Revoke the current session token (logout)."""
            if creds is None or creds.credentials not in self._sessions:
                raise HTTPException(status_code=401, detail="unauthorized")
            chat_id, _created_at = self._sessions[creds.credentials]
            del self._sessions[creds.credentials]
            self._notify_revoked(chat_id)
            if self._state_store:
                await self._state_store.delete_http_session(creds.credentials)
            log.info("HTTP session deleted", event="http_session_deleted")
            return {"status": "revoked"}

        @app.post("/admin/unlock")
        async def admin_unlock(req: UnlockRequest):
            """Admin endpoint: unlock a chat_id locked out from pairing (requires pairing code)."""
            self._require_admin_code(req.code)
            if not self._safety.pairing.is_locked(req.chat_id):
                raise HTTPException(status_code=400, detail="chat not locked")
            self._safety.pairing.unlock(req.chat_id)
            log.info("Admin unlocked chat", event="admin_unlock", chat_id=req.chat_id)
            return {"status": "unlocked", "chat_id": req.chat_id}

        @app.get("/admin/sessions")
        async def admin_list_sessions(code: str):
            """Admin endpoint: list active HTTP sessions (requires pairing code).

            Tokens are masked to keep the endpoint read-only safe.
            """
            self._require_admin_code(code)
            self._prune_expired_sessions()
            now = datetime.now(timezone.utc).timestamp()
            ttl = self._settings.session_ttl_hours * 3600
            sessions = []
            for token, (chat_id, created_at) in self._sessions.items():
                sessions.append(
                    {
                        "token_prefix": token[:8],
                        "chat_id": chat_id,
                        "created_at": created_at,
                        "expires_at": created_at + ttl,
                        "remaining_seconds": max(0, int(created_at + ttl - now)),
                    }
                )
            return {"sessions": sessions, "count": len(sessions)}

        @app.delete("/admin/sessions/{token}")
        async def admin_revoke_session(token: str, code: str):
            """Admin endpoint: revoke a specific HTTP session (requires pairing code)."""
            self._require_admin_code(code)
            if token not in self._sessions:
                raise HTTPException(status_code=404, detail="session not found")
            chat_id, _created_at = self._sessions[token]
            del self._sessions[token]
            self._notify_revoked(chat_id)
            if self._state_store:
                await self._state_store.delete_http_session(token)
            log.info("Admin revoked session", event="admin_revoke_session", token_prefix=token[:8])
            return {"status": "revoked", "token_prefix": token[:8]}

        @app.delete("/admin/sessions")
        async def admin_revoke_all_sessions(code: str):
            """Admin endpoint: revoke every HTTP session (requires pairing code)."""
            self._require_admin_code(code)
            count = len(self._sessions)
            for chat_id, _created_at in self._sessions.values():
                self._notify_revoked(chat_id)
            self._sessions.clear()
            if self._state_store:
                await self._state_store.clear_http_sessions()
            log.info("Admin revoked all sessions", event="admin_revoke_all_sessions", count=count)
            return {"status": "revoked_all", "count": count}

        def _get_chat_id(
            creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
        ) -> str:
            if creds is None or not self._is_session_valid(creds.credentials):
                raise HTTPException(status_code=401, detail="unauthorized")
            return self._sessions[creds.credentials][0]

        @app.get("/model")
        async def get_model(chat_id: str = Depends(_get_chat_id)):
            """Return this session's per-chat model override and the global default."""
            override = self._bus.get_chat_model(chat_id)
            return {
                "override": override,
                "default": self._settings.default_model,
            }

        @app.post("/model")
        async def set_model(
            req: ModelRequest,
            chat_id: str = Depends(_get_chat_id),
        ):
            """Set a per-chat model override for this session."""
            new_model = req.model.strip()
            if not new_model:
                raise HTTPException(status_code=400, detail="model cannot be empty")
            await self._bus.set_chat_model(chat_id, new_model)
            log.info(
                "HTTP model override set",
                event="http_model_set",
                chat_id=chat_id,
                model=new_model,
            )
            return {"model": new_model, "status": "set"}

        @app.delete("/model")
        async def clear_model(chat_id: str = Depends(_get_chat_id)):
            """Clear this session's per-chat model override."""
            await self._bus.clear_chat_model(chat_id)
            log.info(
                "HTTP model override cleared",
                event="http_model_clear",
                chat_id=chat_id,
            )
            return {"status": "cleared"}

        @app.post("/message")
        async def message(
            req: MessageRequest,
            chat_id: str = Depends(_get_chat_id),
        ):
            # Rate limit check
            rate_limit_msg = self._safety.rate_limiter.check(chat_id)
            if rate_limit_msg:
                raise HTTPException(status_code=429, detail=rate_limit_msg)

            if self._creator and self._creator.is_active(chat_id):
                response_text = await self._creator.handle(chat_id, req.text)
                # Drain any progress notifications sent via notifier.send()
                # during handle() so they don't leak into the next /message
                # call for this chat_id (#30-style buffer race).
                extra = await self._notifier.get_and_clear(chat_id)
                if extra:
                    response_text = f"{response_text}\n\n{extra}"
                return {"response": response_text, "agent": "creator", "success": True}

            agent_name = req.agent
            text = req.text
            if not agent_name:
                agent_name, text = parse_agent_tag(req.text, self._bus.registered_agents)

            event = AgentEvent(
                type=EventType.USER_MESSAGE,
                agent_name=agent_name,
                chat_id=chat_id,
                text=text,
            )
            response = await self._bus.publish(event)

            # Collect any extra messages sent via notifier.send() directly
            extra = await self._notifier.get_and_clear(chat_id)

            if response:
                text = response.text or extra or ""
                return {
                    "response": text,
                    "agent": response.agent_name,
                    "success": response.success,
                }
            return {"response": extra or "No response", "agent": "", "success": False}

        @app.post("/message/stream")
        async def message_stream(
            req: MessageRequest,
            chat_id: str = Depends(_get_chat_id),
        ):
            """Stream agent responses via Server-Sent Events (SSE)."""
            # Rate limit check (same pattern as /message)
            rate_limit_msg = self._safety.rate_limiter.check(chat_id)
            if rate_limit_msg:
                raise HTTPException(status_code=429, detail=rate_limit_msg)

            if self._creator and self._creator.is_active(chat_id):

                async def creator_events():
                    done_event = asyncio.Event()
                    self._notifier.start_stream(chat_id)
                    try:
                        yield f"data: {json.dumps({'type': 'notification', 'text': 'Creating agent...'})}\n\n"

                        async def publish_task():
                            try:
                                response_text = await self._creator.handle(
                                    chat_id, req.text
                                )
                            except Exception:
                                log.exception("Creator handler failed")
                                response_text = "An error occurred while creating the agent."
                            finally:
                                await self._notifier.notify_done(chat_id, response_text)
                                done_event.set()

                        task = asyncio.create_task(publish_task())
                        async for msg_type, msg_text in self._notifier.stream_queue(
                            chat_id, done_event
                        ):
                            if msg_type == "done":
                                yield f"data: {json.dumps({'type': 'response', 'text': msg_text, 'agent': 'creator', 'success': True})}\n\n"
                                yield "data: {\"type\": \"done\"}\n\n"
                            else:
                                yield f"data: {json.dumps({'type': msg_type, 'text': msg_text})}\n\n"
                        await task
                    finally:
                        self._notifier.end_stream(chat_id)

                return StreamingResponse(creator_events(), media_type="text/event-stream")

            agent_name = req.agent
            text = req.text
            if not agent_name:
                agent_name, text = parse_agent_tag(req.text, self._bus.registered_agents)

            event = AgentEvent(
                type=EventType.USER_MESSAGE,
                agent_name=agent_name,
                chat_id=chat_id,
                text=text,
            )

            async def sse_events():
                done_event = asyncio.Event()
                result: dict[str, Any] = {}

                # Mark this chat_id as SSE-mode before anything can call
                # notifier.send()/notify_done() for it, and guarantee the
                # matching cleanup runs even on client disconnect — Starlette
                # closes this generator via GeneratorExit, and try/finally on
                # *this* frame is honored even though the nested stream_queue()
                # generator's own finally would not be (see notifier.py).
                self._notifier.start_stream(chat_id)
                try:
                    # Run publish in background while we stream events. The
                    # completion signal MUST fire even if publish() raises —
                    # otherwise stream_queue()'s `while not done_event.is_set()`
                    # spins on its 0.5s timeout forever and the client hangs.
                    async def publish_task():
                        text = ""
                        try:
                            response = await self._bus.publish(event)
                            result["response"] = response
                            text = response.text if response else ""
                        except Exception as e:
                            log.error(
                                "SSE publish_task failed",
                                event="sse_publish_error",
                                chat_id=chat_id,
                                error=str(e),
                            )
                            # Full exception detail stays server-side in the
                            # log above — the client only sees a generic
                            # message, matching /message's behavior (an
                            # uncaught publish() exception there surfaces as
                            # a bare 500, not the exception text).
                            text = "An error occurred while processing your request."
                        finally:
                            await self._notifier.notify_done(chat_id, text)
                            done_event.set()

                    task = asyncio.create_task(publish_task())

                    async for msg_type, msg_text in self._notifier.stream_queue(
                        chat_id, done_event
                    ):
                        if msg_type == "done":
                            # This is the final response from notify_done
                            response = result.get("response")
                            yield f"data: {json.dumps({'type': 'response', 'text': msg_text, 'agent': response.agent_name if response else '', 'success': response.success if response else False})}\n\n"
                            yield "data: {\"type\": \"done\"}\n\n"
                        else:
                            yield f"data: {json.dumps({'type': msg_type, 'text': msg_text})}\n\n"
                    await task  # Clean up the task
                finally:
                    self._notifier.end_stream(chat_id)

            return StreamingResponse(sse_events(), media_type="text/event-stream")

        @app.post("/approve")
        async def approve(
            req: ApproveRequest,
            chat_id: str = Depends(_get_chat_id),
        ):
            """Resolve an approval request that was sent to this session.

            Supervised actions over HTTP do not auto-approve; the agent waits
            until this endpoint is called with the `approval_id` shown in the
            approval message.
            """
            if not self._safety.gate.resolve(
                req.approval_id, chat_id, req.approved
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Approval request not found, expired, or not owned by this session.",
                )
            log.info(
                "HTTP approval resolved",
                event="http_approval_resolved",
                chat_id=chat_id,
                approval_id=req.approval_id,
                approved=req.approved,
            )
            return {
                "status": "approved" if req.approved else "denied",
                "approval_id": req.approval_id,
            }

        @app.get("/agents")
        async def agents(chat_id: str = Depends(_get_chat_id)):
            return {"agents": self._bus.registered_agents}

        @app.get("/health")
        async def health():
            agent_health = await self._bus.health_check_all()
            return {"status": "ok", "agents": agent_health}

        return app

    async def run(self) -> None:
        config = uvicorn.Config(
            self.app,
            host=self._settings.http_host,
            port=self._settings.http_port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        log.info(
            "HTTP interface starting",
            event="http_start",
            host=self._settings.http_host,
            port=self._settings.http_port,
        )
        await server.serve()