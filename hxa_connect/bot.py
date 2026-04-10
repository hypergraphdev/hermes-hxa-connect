"""HXA Bot — main entry point for Hermes ↔ HXA communication."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
from pathlib import Path

from .client import HxaClient
from .config import get_config
from .websocket import HxaWebSocket

logger = logging.getLogger("hxa-connect")

# Deduplication window (30 seconds)
_DEDUP_WINDOW = 30
_recent_msgs: dict[str, float] = {}


def _is_duplicate(msg_id: str) -> bool:
    """Check if message was already processed."""
    import time
    now = time.time()
    if msg_id in _recent_msgs and now - _recent_msgs[msg_id] < _DEDUP_WINDOW:
        return True
    _recent_msgs[msg_id] = now
    # Cleanup old entries
    if len(_recent_msgs) > 500:
        cutoff = now - _DEDUP_WINDOW
        for k in list(_recent_msgs):
            if _recent_msgs[k] < cutoff:
                del _recent_msgs[k]
    return False


class HxaBot:
    """HXA bot that bridges Hermes Agent with HXA organization."""

    def __init__(self, hub_url: str = "", org_id: str = "", org_secret: str = "",
                 agent_name: str = "", agent_token: str = "", data_dir: str = ""):
        cfg = get_config()
        self.hub_url = hub_url or cfg["hub_url"]
        self.org_id = org_id or cfg["org_id"]
        self.org_secret = org_secret or cfg["org_secret"]
        self.agent_name = agent_name or cfg["agent_name"]
        self.data_dir = data_dir or cfg["data_dir"]

        self.client = HxaClient(self.hub_url, agent_token or cfg["agent_token"])
        self.ws = HxaWebSocket(self.client)
        self.bot_id = ""
        self.bot_name = ""

    def _token_file(self) -> Path:
        return Path(self.data_dir) / "token.json"

    def _save_token(self, token: str, bot_id: str, bot_name: str):
        """Persist token to disk."""
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        self._token_file().write_text(json.dumps({
            "token": token, "bot_id": bot_id, "bot_name": bot_name,
            "hub_url": self.hub_url, "org_id": self.org_id,
        }, indent=2))

    def _load_token(self) -> str:
        """Load token from disk."""
        try:
            if self._token_file().exists():
                data = json.loads(self._token_file().read_text())
                return data.get("token", "")
        except Exception:
            pass
        return ""

    async def register_or_restore(self):
        """Register bot in HXA org, or restore from saved token."""
        # Try saved token first
        saved_token = self._load_token()
        if saved_token:
            self.client.token = saved_token
            try:
                me = self.client.get_me()
                self.bot_id = me.get("id", "")
                self.bot_name = me.get("name", "")
                logger.info(f"Restored token: {self.bot_name} (id={self.bot_id})")
                return
            except Exception:
                logger.warning("Saved token invalid, re-registering...")

        # Register with org_secret
        if not self.org_id or not self.org_secret:
            raise RuntimeError("org_id and org_secret are required for registration")

        name = self.agent_name or f"hermes_{os.getpid()}"
        logger.info(f"Registering as '{name}' in org {self.org_id}...")

        try:
            # Try direct registration with org_secret
            token = self.client.register(self.org_id, self.org_secret, name)
        except RuntimeError as e:
            if "409" in str(e) or "conflict" in str(e).lower():
                # Name conflict — try with ticket
                logger.info("Name conflict, using ticket-based registration...")
                cookie = self.client.admin_login(self.org_id, self.org_secret)
                ticket = self.client.create_ticket(cookie)
                token = self.client.register_with_ticket(self.org_id, ticket, name)
            else:
                raise

        if not token:
            raise RuntimeError("Registration returned no token")

        self.client.token = token
        me = self.client.get_me()
        self.bot_id = me.get("id", "")
        self.bot_name = me.get("name", "")
        self._save_token(token, self.bot_id, self.bot_name)
        logger.info(f"Registered: {self.bot_name} (id={self.bot_id}, token={token[:20]}...)")

    async def on_dm(self, message: dict):
        """Handle incoming DM."""
        msg_id = message.get("id", "")
        if msg_id and _is_duplicate(msg_id):
            return

        sender_name = message.get("sender_name", message.get("sender_id", "unknown"))
        sender_id = message.get("sender_id", "")
        content = message.get("content", "")

        # Skip own messages
        if sender_id == self.bot_id:
            return

        logger.info(f"DM from {sender_name}: {content[:80]}")

        # Ask Hermes
        reply = await self.ask_hermes(f"[HXA-Connect DM] {sender_name} said: {content}")
        if reply:
            try:
                self.client.send_dm(sender_name, reply)
                logger.info(f"DM reply to {sender_name}: {reply[:80]}")
            except Exception as e:
                logger.error(f"Failed to send DM reply: {e}")

    async def on_thread_message(self, data: dict):
        """Handle incoming thread message."""
        message = data.get("message", {})
        thread_id = data.get("thread_id", message.get("thread_id", ""))
        msg_id = message.get("id", "")
        if msg_id and _is_duplicate(msg_id):
            return

        sender_name = message.get("sender_name", message.get("sender_id", "unknown"))
        sender_id = message.get("sender_id", "")
        content = message.get("content", "")

        # Skip own messages
        if sender_id == self.bot_id:
            return

        # Mention filter: only respond if @mentioned
        if self.bot_name and f"@{self.bot_name}" not in content:
            logger.debug(f"Thread {thread_id} from {sender_name}: not mentioned, skipping")
            return

        logger.info(f"Thread {thread_id} from {sender_name}: {content[:80]}")

        # Ask Hermes
        reply = await self.ask_hermes(
            f"[HXA-Connect Thread:{thread_id}] {sender_name} said: {content}"
        )
        if reply:
            try:
                self.client.send_thread_message(thread_id, reply)
                logger.info(f"Thread reply to {thread_id}: {reply[:80]}")
            except Exception as e:
                logger.error(f"Failed to send thread reply: {e}")

    async def on_typing(self, data: dict):
        """Handle typing indicator (no-op for now)."""
        pass

    async def ask_hermes(self, question: str) -> str:
        """Send question to Hermes Agent via CLI and parse the reply."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "hermes", "chat", "-q", question,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"},
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            output = stdout.decode("utf-8", errors="replace")
            return self._parse_hermes_reply(output)
        except asyncio.TimeoutError:
            logger.error("Hermes chat -q timed out (120s)")
            return ""
        except Exception as e:
            logger.error(f"Hermes chat -q failed: {e}")
            return ""

    @staticmethod
    def _parse_hermes_reply(output: str) -> str:
        """Extract clean reply text from hermes chat -q TUI output."""
        lines = output.split("\n")
        reply_lines: list[str] = []

        # Strategy 1: Find reply block between ⚕ Hermes markers
        in_reply = False
        for line in lines:
            if "⚕" in line and "Hermes" in line and "───" in line:
                in_reply = True
                continue
            if in_reply and ("╰─" in line or "Resume this session" in line):
                break
            if in_reply:
                clean = line.replace("│", "").strip()
                if clean:
                    reply_lines.append(clean)

        # Strategy 2: Fallback — extract non-TUI lines after "Query:"
        if not reply_lines:
            after_query = False
            box_chars = set("╭╰╮╯│┌└┐┘├┤┬┴┼─═║╔╗╚╝╠╣╦╩╬█▀▄▌▐░▒▓⠀")
            for line in lines:
                if "Query:" in line:
                    after_query = True
                    continue
                if not after_query:
                    continue
                if "Resume this session" in line:
                    break
                if line.startswith(("Session:", "Duration:", "Messages:", "Initializing")):
                    break
                clean = line.strip()
                if not clean or len(clean) < 3:
                    continue
                if clean[0] in box_chars:
                    continue
                if "───" in clean or "⚕" in clean:
                    continue
                # Skip ASCII art banner lines
                if re.match(r'^[█╗║╔╝╚═\s]+$', clean):
                    continue
                reply_lines.append(clean)

        return "\n".join(reply_lines).strip()

    async def start(self):
        """Main entry: register, connect WS, listen for messages."""
        logger.info(f"Starting HXA bot: hub={self.hub_url}")

        # Step 1: Register or restore token
        await self.register_or_restore()
        logger.info(f"Bot ready: {self.bot_name} (id={self.bot_id})")

        # Step 2: Connect WebSocket and listen
        logger.info("Connecting to HXA Hub WebSocket...")
        await self.ws.listen(
            on_message=self.on_dm,
            on_thread_message=self.on_thread_message,
            on_typing=self.on_typing,
        )

    async def stop(self):
        """Gracefully stop."""
        await self.ws.disconnect()
        logger.info("HXA bot stopped")


async def main():
    """CLI entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    bot = HxaBot()
    try:
        await bot.start()
    except KeyboardInterrupt:
        await bot.stop()


def run():
    """Synchronous entry point."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
