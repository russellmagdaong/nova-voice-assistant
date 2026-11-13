"""Serves NOVA's tools to Claude from inside NOVA: an MCP server over HTTP on 127.0.0.1.

Living inside NOVA (not in a process Claude starts) means timers keep running when Claude's
process restarts, and can ring through NOVA's voice and notch.

Locked down:
- listens on 127.0.0.1 only (not reachable from the network), on a random free port;
- every request needs a random secret made when NOVA starts (only the Claude process gets it);
- requests from a web browser (an Origin header) are refused, so a web page can't reach it;
- only the fixed tools registered here exist, and each call's arguments are checked against the
  tool's schema (tools_server.call).
"""
from __future__ import annotations

import hmac
import json
import logging
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from . import tools_server

log = logging.getLogger("nova.tools")
MAX_BODY = 256 * 1024


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # Claude closing a kept-alive connection is normal. (The default prints a traceback to
        # stderr, which doesn't exist under pythonw.)
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        log.exception("tools request failed")


class ToolHost:
    def __init__(self, tools: list[dict], funcs: dict[str, Callable]):
        self.tools, self.funcs = tools, funcs
        self.token = secrets.token_urlsafe(32)
        host = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _send(self, code: int, body: bytes = b"", ctype: str = "application/json"):
                self.send_response(code)
                if body:
                    self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _allowed(self) -> bool:
                auth = self.headers.get("Authorization", "")
                ok = hmac.compare_digest(auth.encode(), f"Bearer {host.token}".encode())
                if self.headers.get("Origin") or not ok or self.path.split("?")[0] != "/mcp":
                    log.warning("refused a tools request (%s %s)", self.command, self.path[:40])
                    self._send(403)
                    return False
                return True

            def do_POST(self):
                if not self._allowed():
                    return
                n = int(self.headers.get("Content-Length") or 0)
                if not 0 < n <= MAX_BODY:
                    return self._send(413)
                try:
                    msg = json.loads(self.rfile.read(n))
                except json.JSONDecodeError:
                    return self._send(400)
                msgs = msg if isinstance(msg, list) else [msg]
                replies = []
                for m in msgs:
                    if isinstance(m, dict):
                        if m.get("method") == "tools/call":
                            p = m.get("params") or {}
                            log.info("tool call: %s %s", p.get("name"), json.dumps(p.get("arguments") or {})[:200])
                        r = tools_server.handle(m, host.tools, host.funcs)
                        if r is not None:
                            replies.append(r)
                if not replies:
                    return self._send(202)               # only notifications
                out = replies if isinstance(msg, list) else replies[0]
                self._send(200, json.dumps(out).encode("utf-8"))

            def do_GET(self):                            # no server-to-client stream
                if self._allowed():
                    self.send_response(405)
                    self.send_header("Allow", "POST")
                    self.send_header("Content-Length", "0")
                    self.end_headers()

            def do_DELETE(self):
                if self._allowed():
                    self._send(200)

            def log_message(self, fmt, *args):           # (quiet; tool calls are logged above)
                pass

        self.server = _Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True, name="tool-host").start()
        log.info("tools ready on 127.0.0.1:%d: %s", self.port, ", ".join(t["name"] for t in tools))

    @property
    def names(self) -> list[str]:
        return [t["name"] for t in self.tools]

    def mcp_config(self) -> dict:
        return {"type": "http", "url": f"http://127.0.0.1:{self.port}/mcp",
                "headers": {"Authorization": f"Bearer {self.token}"}}

    def close(self):
        self.server.shutdown()
        self.server.server_close()
