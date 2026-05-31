"""
interfaces/http.py
------------------
HTTP REST API interface. Exposes the agent bus over FastAPI.

Pairing flow:
  1. POST /pair with the code printed at startup → receive a session token (UUID)
  2. Use the token in Authorization: Bearer <token> for all other requests

Endpoints:
  POST /pair          — exchange pairing code for session token
  POST /message       — send a message to an agent
  GET  /agents        — list registered agents
  GET  /health        — system health (no auth required)

Session tokens are in-memory only and cleared on restart.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from core.logger import get_logger
from core.protocols import AgentEvent, EventType

if TYPE_CHECKING:
    from core.bus import MessageBus
    from core.config import Settings
    from core.notifier import HTTPNotifier
    from core.safety import Safety
    from core.agent_creator import AgentCreator

log = get_logger("http_interface")

_bearer = HTTPBearer(auto_error=False)


class PairRequest(BaseModel):
    code: str


class MessageRequest(BaseModel):
    text: str
    agent: str = ""


class HTTPInterface:
    def __init__(
        self,
        bus: "MessageBus",
        safety: "Safety",
        creator: "AgentCreator",
        notifier: "HTTPNotifier",
        settings: "Settings",
    ) -> None:
        self._bus = bus
        self._safety = safety
        self._creator = creator
        self._notifier = notifier
        self._settings = settings
        self._sessions: dict[str, str] = {}  # token → chat_id
        self.app = self._build_app()

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="modular-agents HTTP API", docs_url=None, redoc_url=None)

        @app.post("/pair")
        async def pair(req: PairRequest):
            if req.code.strip() != self._safety.pairing.code:
                raise HTTPException(status_code=403, detail="invalid code")

            token = str(uuid.uuid4())
            chat_id = f"http_{token[:8]}"
            self._sessions[token] = chat_id
            self._safety.pairing.pair_directly(chat_id)
            log.info("HTTP session paired", event="http_paired", chat_id=chat_id)
            return {"token": token}

        def _get_chat_id(
            creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
        ) -> str:
            if creds is None or creds.credentials not in self._sessions:
                raise HTTPException(status_code=401, detail="unauthorized")
            return self._sessions[creds.credentials]

        @app.post("/message")
        async def message(
            req: MessageRequest,
            chat_id: str = Depends(_get_chat_id),
        ):
            if self._creator and self._creator.is_active(chat_id):
                response_text = await self._creator.handle(chat_id, req.text)
                return {"response": response_text, "agent": "creator", "success": True}

            event = AgentEvent(
                type=EventType.USER_MESSAGE,
                agent_name=req.agent,
                chat_id=chat_id,
                text=req.text,
            )
            response = await self._bus.publish(event)

            # Collect any extra messages sent via notifier.send() directly
            extra = self._notifier.get_and_clear(chat_id)

            if response:
                text = response.text or extra or ""
                return {
                    "response": text,
                    "agent": response.agent_name,
                    "success": response.success,
                }
            return {"response": extra or "No response", "agent": "", "success": False}

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