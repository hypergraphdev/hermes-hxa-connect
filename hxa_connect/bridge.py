"""HTTP bridge server for outbound HXA messages (Hermes → HXA Hub)."""

from __future__ import annotations

import json
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

logger = logging.getLogger("hxa-connect")


class BridgeHandler(BaseHTTPRequestHandler):
    """HTTP handler for outbound messages."""

    client = None  # Set by start_bridge()

    def do_POST(self):
        if self.path == "/send":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}

            to = body.get("to", "")
            content = body.get("content", "")
            thread_id = body.get("thread_id", "")

            if not content:
                self._respond(400, {"ok": False, "error": "Missing content"})
                return

            try:
                if thread_id:
                    self.client.send_thread_message(thread_id, content)
                    logger.info(f"Bridge → Thread {thread_id}: {content[:60]}")
                elif to:
                    self.client.send_dm(to, content)
                    logger.info(f"Bridge → DM {to}: {content[:60]}")
                else:
                    self._respond(400, {"ok": False, "error": "Missing 'to' or 'thread_id'"})
                    return
                self._respond(200, {"ok": True})
            except Exception as e:
                logger.error(f"Bridge send error: {e}")
                self._respond(500, {"ok": False, "error": str(e)})
        else:
            self._respond(404, {"error": "Not found"})

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {"ok": True, "bot": self.client.token[:20] + "..." if self.client.token else ""})
        else:
            self._respond(404, {"error": "Not found"})

    def _respond(self, code: int, body: dict):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, format, *args):
        pass  # Suppress default access logging


def start_bridge(client, port: int = 9200) -> HTTPServer:
    """Start bridge HTTP server in a background thread."""
    BridgeHandler.client = client
    server = HTTPServer(("0.0.0.0", port), BridgeHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"Bridge server listening on port {port}")
    return server
