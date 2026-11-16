"""HXA Hub WebSocket connection manager with auto-reconnect."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable, Awaitable

logger = logging.getLogger("hxa-connect")

# Event callback types
OnMessage = Callable[[dict], Awaitable[None]]
OnThreadMessage = Callable[[dict], Awaitable[None]]
OnTyping = Callable[[dict], Awaitable[None]]
OnParticipant = Callable[[dict], Awaitable[None]]


class HxaWebSocket:
    """WebSocket connection to HXA Hub with auto-reconnect."""

    def __init__(self, client):
        """
        Args:
            client: HxaClient instance (used to get ws-ticket and ws URL)
        """
        self.client = client
        self.ws = None
        self._running = False
        self._reconnect_delay = 5  # seconds, grows exponentially

    async def connect(self):
        """Establish WebSocket connection using ticket-based auth."""
        try:
            import websockets
        except ImportError:
            raise ImportError("Install websockets: pip install websockets")

        ticket = self.client.get_ws_ticket()
        if not ticket:
            raise RuntimeError("Failed to get WebSocket ticket")

        ws_url = f"{self.client.get_ws_url()}?ticket={ticket}"
        logger.info(f"Connecting to WebSocket: {ws_url[:80]}...")

        self.ws = await websockets.connect(
            ws_url,
            ping_interval=30,
            ping_timeout=10,
            close_timeout=5,
        )
        self._reconnect_delay = 5  # reset on successful connect
        logger.info("WebSocket connected")

    async def listen(
        self,
        on_message: OnMessage | None = None,
        on_thread_message: OnThreadMessage | None = None,
        on_typing: OnTyping | None = None,
        on_participant: OnParticipant | None = None,
    ):
        """Listen for events. Auto-reconnects on disconnect."""
        self._running = True

        while self._running:
            reason = "closed by hub"
            try:
                if not self.ws:
                    await self.connect()

                async for raw in self.ws:
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    event_type = data.get("type", "")

                    if event_type == "message" and on_message:
                        msg = data.get("message", data)
                        # Normalize content
                        if isinstance(msg.get("content"), dict):
                            msg["content"] = msg["content"].get("text", str(msg["content"]))
                        asyncio.create_task(on_message(msg))

                    elif event_type == "thread_message" and on_thread_message:
                        asyncio.create_task(on_thread_message(data))

                    elif event_type == "typing" and on_typing:
                        asyncio.create_task(on_typing(data))

                    elif event_type in ("thread_participant", "participant") and on_participant:
                        asyncio.create_task(on_participant(data))

                    elif event_type == "thread_created":
                        logger.info(f"Thread created: {data.get('thread', {}).get('topic', '')}")

                    else:
                        logger.debug(f"Unhandled WS event: {event_type}")

            except Exception as e:
                reason = e

            # Getting here means this connection is over: the loop above only ends
            # when it closes or fails. Forget it so the next round opens a new one.
            # (Don't ask the library instead: `.closed` is gone in websockets >= 14,
            # and the AttributeError used to end every round before reconnecting.)
            await self._drop()
            if not self._running:
                break
            logger.warning(f"WebSocket disconnected: {reason}. Reconnecting in {self._reconnect_delay}s...")
            await asyncio.sleep(self._reconnect_delay)
            self._reconnect_delay = min(self._reconnect_delay * 2, 60)

        logger.info("WebSocket listener stopped")

    async def _drop(self):
        """Forget the current connection, closing it if it is still open."""
        ws, self.ws = self.ws, None
        if ws:
            try:
                await ws.close()
            except Exception:
                pass

    async def disconnect(self):
        """Close WebSocket connection."""
        self._running = False
        if self.ws:
            await self._drop()
            logger.info("WebSocket disconnected")
