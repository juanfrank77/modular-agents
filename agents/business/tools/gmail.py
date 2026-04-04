"""
agents/business/tools/gmail.py
-------------------------------
Gmail integration for the Business agent via Composio.

Usage:
    from core.composio_tool import ComposioTool
    from agents.business.tools.gmail import GmailTool

    composio = ComposioTool(api_key="...", user_id="alice")
    gmail = GmailTool(composio=composio)

    emails = await gmail.list_emails(max_results=5)
    result = await gmail.send_email(to="bob@example.com", subject="Hi", body="Hello!")
    draft  = await gmail.draft_reply(email_id="msg_123", body="Thanks!")
"""

from __future__ import annotations

from core.composio_tool import ComposioTool
from core.logger import get_logger

log = get_logger("gmail_tool")


class GmailTool:
    """Gmail operations powered by Composio.

    Each method maps to a single Composio action slug and returns the raw
    result dict produced by the SDK.  On error the dict will contain an
    ``"error"`` key (handled transparently by :meth:`ComposioTool.execute`).

    Args:
        composio: An initialised :class:`~core.composio_tool.ComposioTool`
                  instance shared across tools.
    """

    def __init__(self, composio: ComposioTool) -> None:
        self._composio = composio
        log.debug("GmailTool initialised", event="gmail_init")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def list_emails(self, max_results: int = 10) -> list[dict]:
        """Fetch recent emails from the user's Gmail inbox.

        Args:
            max_results: Maximum number of email summaries to return.
                         Defaults to 10.

        Returns:
            List of email dicts, or a single-item list containing an error
            dict if the action fails.
        """
        log.debug(
            "list_emails",
            event="gmail_list_emails",
            max_results=max_results,
        )
        result = await self._composio.execute(
            "GMAIL_FETCH_EMAILS",
            max_results=max_results,
        )
        if "error" in result:
            log.warning(
                "list_emails failed",
                event="gmail_list_emails_error",
                error=result["error"],
            )
            return [result]
        messages: list[dict] = result.get("messages", result.get("data", [result]))
        log.debug(
            "list_emails complete",
            event="gmail_list_emails_done",
            count=len(messages),
        )
        return messages

    async def send_email(self, to: str, subject: str, body: str) -> dict:
        """Send an email via Gmail.

        Args:
            to: Recipient email address.
            subject: Email subject line.
            body: Plain-text email body.

        Returns:
            Result dict from Composio (contains ``"messageId"`` on success,
            or ``"error"`` on failure).
        """
        if not to.strip() or not subject.strip() or not body.strip():
            raise ValueError("send_email requires non-empty to, subject, and body")
        log.debug(
            "send_email",
            event="gmail_send_email",
            to=to,
            subject=subject,
        )
        result = await self._composio.execute(
            "GMAIL_SEND_EMAIL",
            to=to,
            subject=subject,
            body=body,
        )
        if "error" in result:
            log.warning(
                "send_email failed",
                event="gmail_send_email_error",
                to=to,
                error=result["error"],
            )
        else:
            log.debug(
                "send_email complete",
                event="gmail_send_email_done",
                to=to,
            )
        return result

    async def draft_reply(self, email_id: str, body: str) -> dict:
        """Create a draft reply to an existing email thread.

        Args:
            email_id: The Gmail message ID to reply to.
            body: Plain-text body for the draft reply.

        Returns:
            Result dict from Composio (contains ``"draftId"`` on success,
            or ``"error"`` on failure).
        """
        log.debug(
            "draft_reply",
            event="gmail_draft_reply",
            email_id=email_id,
        )
        result = await self._composio.execute(
            "GMAIL_CREATE_EMAIL_DRAFT",
            message_id=email_id,
            body=body,
        )
        if "error" in result:
            log.warning(
                "draft_reply failed",
                event="gmail_draft_reply_error",
                email_id=email_id,
                error=result["error"],
            )
        else:
            log.debug(
                "draft_reply complete",
                event="gmail_draft_reply_done",
                email_id=email_id,
            )
        return result
