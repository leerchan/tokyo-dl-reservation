"""One-tap cancel over HTTP — the Bark push links here.

`GET  /cancel?t=<token>` → page with a single 取消 button
`POST /cancel?t=<token>` → runs the same flow as `dl-poll --cancel-booking`

ponytail: stdlib http.server, plain HTTP, one shared token. Fine because
it only ever listens on the Tailscale interface; put it behind TLS if
that ever changes.
"""

from __future__ import annotations

import argparse
import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .poll import cancel_held_booking

ENV_TOKEN = "DL_RES_CANCEL_TOKEN"
ENV_URL = "DL_RES_CANCEL_URL"  # public base the phone can reach, e.g. http://mac.tailnet.ts.net:8787

_log = logging.getLogger("dl_reservation.cancel_server")

_PAGE = """<!doctype html><meta name=viewport content="width=device-width">
<body style="font:20px system-ui;text-align:center;padding-top:30vh">
<form method=post><button style="font-size:28px;padding:1em 2em">予約を取消</button></form>"""


def cancel_link() -> str | None:
    """Full URL for the Bark push, or None if the server is not configured."""
    base, token = os.environ.get(ENV_URL), os.environ.get(ENV_TOKEN)
    if base and token:
        return f"{base.rstrip('/')}/cancel?t={token}"
    return None


class Handler(BaseHTTPRequestHandler):
    state: Path  # set by serve()
    token: str

    def _authed(self) -> bool:
        url = urlparse(self.path)
        ok = url.path == "/cancel" and parse_qs(url.query).get("t") == [self.token]
        if not ok:
            self._reply(404, "not found")
        return ok

    def _reply(self, status: int, body: str, ctype: str = "text/plain") -> None:
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self._authed():
            self._reply(200, _PAGE, "text/html")

    def do_POST(self) -> None:
        if self._authed():
            ok, msg = cancel_held_booking(self.state)
            (_log.info if ok else _log.error)(msg)
            self._reply(200 if ok else 500, msg)

    def log_message(self, fmt, *args) -> None:
        _log.info(fmt, *args)


def serve(state: Path, token: str, port: int, host: str = "0.0.0.0") -> HTTPServer:
    Handler.state, Handler.token = state, token
    return HTTPServer((host, port), Handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dl-cancel-server", description=__doc__)
    parser.add_argument("--state", type=Path, default=Path("state/snapshot.json"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DL_RES_CANCEL_PORT", "8787")))
    args = parser.parse_args(argv)
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
    token = os.environ.get(ENV_TOKEN)
    if not token:
        parser.error(f"{ENV_TOKEN} is not set")
    _log.info("listening on :%d (state=%s)", args.port, args.state)
    serve(args.state, token, args.port).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
