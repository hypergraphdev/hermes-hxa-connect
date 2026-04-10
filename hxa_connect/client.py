"""HXA Hub REST API client."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from urllib.parse import urlparse

logger = logging.getLogger("hxa-connect")


class HxaClient:
    """Async-compatible HTTP client for HXA Hub REST API."""

    def __init__(self, hub_url: str, token: str = ""):
        self.hub_url = hub_url.rstrip("/")
        self.token = token
        parsed = urlparse(hub_url)
        self.origin = f"{parsed.scheme}://{parsed.netloc}"

    def _request(self, method: str, path: str, body: dict | None = None,
                 headers: dict | None = None, timeout: int = 15) -> dict | list:
        """Make HTTP request to Hub API."""
        url = f"{self.hub_url}{path}"
        hdrs = {"Content-Type": "application/json"}
        if self.token:
            hdrs["Authorization"] = f"Bearer {self.token}"
        if headers:
            hdrs.update(headers)

        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            error_body = ""
            if e.fp:
                try:
                    error_body = e.fp.read().decode()
                except Exception:
                    pass
            logger.error(f"Hub API {method} {path} → {e.code}: {error_body[:200]}")
            raise RuntimeError(f"Hub API error {e.code}: {error_body[:200]}") from e
        except urllib.error.URLError as e:
            logger.error(f"Hub API {method} {path} → {e.reason}")
            raise

    # ── Authentication ──

    def register(self, org_id: str, org_secret: str, name: str) -> str:
        """Register bot in HXA org. Returns bot token."""
        result = self._request("POST", "/api/auth/register", {
            "org_id": org_id,
            "org_secret": org_secret,
            "name": name,
        })
        token = result.get("token", "")
        if token:
            self.token = token
        return token

    def register_with_ticket(self, org_id: str, ticket: str, name: str) -> str:
        """Register bot using a one-time ticket. Returns bot token."""
        result = self._request("POST", "/api/auth/register", {
            "org_id": org_id,
            "ticket": ticket,
            "name": name,
        })
        token = result.get("token", "")
        if token:
            self.token = token
        return token

    def admin_login(self, org_id: str, org_secret: str) -> str:
        """Login as org admin. Returns session cookie."""
        url = f"{self.hub_url}/api/auth/login"
        data = json.dumps({"type": "org_admin", "org_secret": org_secret, "org_id": org_id}).encode()
        req = urllib.request.Request(url, data=data, headers={
            "Content-Type": "application/json",
            "Origin": self.origin,
        }, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.headers.get("Set-Cookie", "").split(";")[0]

    def create_ticket(self, cookie: str, reusable: bool = False) -> str:
        """Create a registration ticket (requires admin cookie)."""
        result = self._request("POST", "/api/org/tickets", {"reusable": reusable}, headers={
            "Cookie": cookie,
            "Origin": self.origin,
        })
        return result.get("secret") or result.get("ticket") or ""

    # ── Bot Identity ──

    def get_me(self) -> dict:
        """Get current bot identity."""
        return self._request("GET", "/api/me")

    def rename(self, new_name: str) -> dict:
        """Rename current bot."""
        return self._request("PATCH", "/api/me/name", {"name": new_name})

    def list_bots(self, limit: int = 200) -> list:
        """List all bots in organization."""
        result = self._request("GET", f"/api/bots?limit={limit}")
        if isinstance(result, list):
            return result
        return result.get("bots", result.get("items", []))

    # ── Direct Messages ──

    def send_dm(self, to_name: str, content: str) -> dict:
        """Send a DM to a bot by name."""
        return self._request("POST", "/api/send", {"to": to_name, "content": content})

    def get_inbox(self, since: int = 0, limit: int = 50) -> list:
        """Get inbox messages."""
        result = self._request("GET", f"/api/inbox?since={since}&limit={limit}")
        if isinstance(result, list):
            return result
        return result.get("messages", result.get("items", []))

    def get_channel_messages(self, channel_id: str, limit: int = 50, before: str = "") -> list:
        """Get messages in a DM channel."""
        params = f"limit={limit}"
        if before:
            params += f"&before={before}"
        result = self._request("GET", f"/api/channels/{channel_id}/messages?{params}")
        if isinstance(result, list):
            return result
        return result.get("messages", result.get("items", []))

    # ── Threads ──

    def create_thread(self, topic: str) -> dict:
        """Create a new thread."""
        return self._request("POST", "/api/threads", {"topic": topic})

    def send_thread_message(self, thread_id: str, content: str) -> dict:
        """Send a message to a thread."""
        return self._request("POST", f"/api/threads/{thread_id}/messages", {"content": content})

    def get_thread_messages(self, thread_id: str, limit: int = 50) -> list:
        """Get messages in a thread."""
        result = self._request("GET", f"/api/threads/{thread_id}/messages?limit={limit}")
        if isinstance(result, list):
            return result
        return result.get("messages", result.get("items", []))

    def list_threads(self, status: str = "active", limit: int = 50) -> list:
        """List threads."""
        result = self._request("GET", f"/api/threads?status={status}&limit={limit}")
        if isinstance(result, list):
            return result
        return result.get("threads", result.get("items", []))

    def join_thread(self, thread_id: str, bot_id: str) -> dict:
        """Join a thread."""
        return self._request("POST", f"/api/threads/{thread_id}/participants", {"bot_id": bot_id})

    # ── WebSocket ──

    def get_ws_ticket(self) -> str:
        """Get a WebSocket connection ticket."""
        result = self._request("POST", "/api/ws-ticket")
        return result.get("ticket", "")

    def get_ws_url(self) -> str:
        """Build WebSocket URL from hub URL."""
        return self.hub_url.replace("https://", "wss://").replace("http://", "ws://") + "/ws"
