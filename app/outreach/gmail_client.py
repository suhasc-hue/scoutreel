"""Gmail integration — OAuth2 installed-app flow, send, reply detection.

Scopes: gmail.send + gmail.readonly. Token stored locally in token.json
(gitignored). Sends always go through guardrails.assert_can_send first —
callers must never bypass it.
"""
import base64
from dataclasses import dataclass
from email.mime.text import MIMEText
from pathlib import Path

from loguru import logger

from app.config import get_settings

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]


@dataclass
class SendResult:
    message_id: str
    thread_id: str


@dataclass
class ThreadReply:
    from_address: str
    snippet: str
    body_text: str


class GmailClient:
    def __init__(
        self,
        credentials_file: str | None = None,
        token_file: str | None = None,
        interactive: bool = True,
    ):
        """interactive=False (used by scheduled jobs) refuses to start the
        browser OAuth flow — otherwise a background job would block forever
        waiting for a human. Run `make gmail-auth` once instead."""
        from app.config import PROJECT_ROOT

        self.interactive = interactive

        settings = get_settings()
        # Relative paths resolve against the project folder, not the process cwd.
        self.credentials_file = str(
            Path(credentials_file or settings.gmail_credentials_file)
            if Path(credentials_file or settings.gmail_credentials_file).is_absolute()
            else PROJECT_ROOT / (credentials_file or settings.gmail_credentials_file)
        )
        self.token_file = str(
            Path(token_file or settings.gmail_token_file)
            if Path(token_file or settings.gmail_token_file).is_absolute()
            else PROJECT_ROOT / (token_file or settings.gmail_token_file)
        )
        self._service = None

    # ---- auth ----

    def _get_service(self):
        if self._service is not None:
            return self._service

        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = None
        if Path(self.token_file).exists():
            creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            Path(self.token_file).write_text(creds.to_json(), encoding="utf-8")
        if not creds or not creds.valid:
            if not self.interactive:
                raise RuntimeError(
                    "Gmail is not authorized and this is a non-interactive "
                    "context — run `make gmail-auth` once to complete OAuth."
                )
            if not Path(self.credentials_file).exists():
                raise RuntimeError(
                    f"Gmail OAuth client file not found: {self.credentials_file}. "
                    "See README → 'Gmail setup'."
                )
            flow = InstalledAppFlow.from_client_secrets_file(self.credentials_file, SCOPES)
            creds = flow.run_local_server(port=0)
            Path(self.token_file).write_text(creds.to_json(), encoding="utf-8")
            logger.info("Gmail OAuth complete; token saved to {}", self.token_file)

        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    def my_address(self) -> str:
        profile = self._get_service().users().getProfile(userId="me").execute()
        return profile.get("emailAddress", "")

    # ---- send ----

    def send_email(self, to: str, subject: str, body: str) -> SendResult:
        # Deliberately NO retry: if the send succeeded but the response was
        # lost, a retry would email the person twice. Failures surface to the
        # dashboard where the user can click Send again.
        mime = MIMEText(body, "plain", "utf-8")
        mime["to"] = to
        mime["subject"] = subject
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")
        resp = (
            self._get_service()
            .users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )
        return SendResult(message_id=resp["id"], thread_id=resp["threadId"])

    # ---- reply detection ----

    def get_thread_replies(self, thread_id: str, my_address: str) -> list[ThreadReply]:
        """Messages in the thread NOT sent by us = replies."""
        thread = (
            self._get_service()
            .users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        )
        replies: list[ThreadReply] = []
        for msg in thread.get("messages", []):
            headers = {
                h["name"].lower(): h["value"]
                for h in msg.get("payload", {}).get("headers", [])
            }
            sender = headers.get("from", "")
            if my_address and my_address.lower() in sender.lower():
                continue
            replies.append(
                ThreadReply(
                    from_address=sender,
                    snippet=msg.get("snippet", ""),
                    body_text=_extract_plain_text(msg.get("payload", {})),
                )
            )
        return replies


def _extract_plain_text(payload: dict) -> str:
    """Best-effort plain-text body from a Gmail message payload."""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            try:
                return base64.urlsafe_b64decode(data + "===").decode("utf-8", "replace")
            except Exception:
                return ""
    for part in payload.get("parts", []) or []:
        text = _extract_plain_text(part)
        if text:
            return text
    return ""


def client_for_account(account, interactive: bool = False) -> "GmailClient":
    """GmailClient bound to a connected MailAccount's token file."""
    return GmailClient(token_file=account.token_file, interactive=interactive)


def connect_new_account(token_file: str) -> str:
    """Run the interactive OAuth flow into a fresh token file and return the
    authorized email address. Opens a browser on this machine."""
    client = GmailClient(token_file=token_file, interactive=True)
    return client.my_address()


def reply_requests_unsubscribe(reply: ThreadReply) -> bool:
    text = f"{reply.snippet}\n{reply.body_text}".lower()
    return "unsubscribe" in text or "don't contact" in text or "do not contact" in text
