"""Does the bridge come back by itself after the hub goes away?

Starts a throw-away WebSocket server on this machine (no real hub, no token),
lets HxaWebSocket connect, takes the server down, brings it back, and checks
that the bridge reconnects without being restarted — once for a clean close
(hub restart) and once for a dropped line.  Then checks disconnect() stops it.

    python tests/reconnect_test.py          # ~40 s; exit code 0 = all passed

Run it with every websockets version the bridge may meet (12 … latest): the
connection object changed in 14 and no longer has `.closed`.
"""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import websockets

SOURCE = Path(__file__).resolve().parent.parent / "hxa_connect" / "websocket.py"
PORT = 48731
BACK_WITHIN = 30  # seconds the bridge gets to come back once the hub is up again


def bridge_class(source: Path):
    """Load websocket.py on its own (no bot, no config)."""
    spec = importlib.util.spec_from_file_location("hxa_websocket_under_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.HxaWebSocket


class FakeClient:
    """Stands in for HxaClient: hands out a ticket and the address of the fake hub."""

    def get_ws_ticket(self):
        return "test-ticket"

    def get_ws_url(self):
        return f"ws://127.0.0.1:{PORT}/ws"


class FakeHub:
    """A hub that greets every new connection with one numbered message."""

    def __init__(self):
        self.server = None
        self.lines = set()
        self.greeted = 0

    async def _serve(self, ws, *_):
        self.lines.add(ws)
        self.greeted += 1
        try:
            await ws.send(json.dumps({"type": "message", "message": {"content": f"hello {self.greeted}"}}))
            await ws.wait_closed()
        finally:
            self.lines.discard(ws)

    async def up(self):
        self.server = await websockets.serve(self._serve, "127.0.0.1", PORT)

    async def down(self):
        """Clean shutdown, the way a restarting hub closes its connections."""
        self.server.close()
        await self.server.wait_closed()

    async def cut(self):
        """Pull the plug: no closing handshake."""
        for ws in list(self.lines):
            ws.transport.abort()
        await self.down()


async def heard(inbox, count):
    """Wait until the bridge has delivered `count` messages; False if it never does."""
    for _ in range(BACK_WITHIN * 10):
        if len(inbox) >= count:
            return True
        await asyncio.sleep(0.1)
    return False


async def main():
    print(f"websockets {websockets.__version__}, python {sys.version.split()[0]}, testing {SOURCE}")
    hub, inbox, results = FakeHub(), [], {}

    async def on_message(msg):
        inbox.append(msg["content"])

    bridge = bridge_class(SOURCE)(FakeClient())
    await hub.up()
    listening = asyncio.create_task(bridge.listen(on_message=on_message))
    results["connects the first time"] = await heard(inbox, 1)

    for label, stop in (("comes back after the hub restarts", hub.down),
                        ("comes back after the line is cut", hub.cut)):
        await stop()
        await asyncio.sleep(8)  # the hub stays away long enough for a retry to fail
        await hub.up()
        results[label] = await heard(inbox, len(inbox) + 1)

    try:
        await asyncio.wait_for(bridge.disconnect(), 10)
        await asyncio.wait_for(listening, 70)
        results["disconnect() stops it"] = True
    except Exception as error:
        print(f"  disconnect failed: {error!r}")
        results["disconnect() stops it"] = False
        listening.cancel()
    await hub.down()

    for label, passed in results.items():
        print(f"  {'PASS' if passed else 'FAIL'}  {label}")
    print(f"  messages delivered: {inbox}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
