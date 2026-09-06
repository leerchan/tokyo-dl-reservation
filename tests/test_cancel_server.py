import threading
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

from dl_reservation import cancel_server


def test_cancel_server_gates_on_token_and_runs_cancel(tmp_path: Path):
    srv = cancel_server.serve(tmp_path, "tok", 0, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    conn = HTTPConnection("127.0.0.1", srv.server_port)
    try:
        conn.request("GET", "/cancel?t=wrong"); assert conn.getresponse().read() and True
        conn.request("GET", "/cancel?t=tok"); r = conn.getresponse()
        assert r.status == 200 and b"<form" in r.read()
        with patch.object(cancel_server, "cancel_held_booking", return_value=(True, "done")) as m:
            conn.request("POST", "/cancel?t=tok"); r = conn.getresponse()
            assert r.status == 200 and r.read() == b"done"
            m.assert_called_once_with(tmp_path)
        conn.request("POST", "/cancel?t=wrong"); assert conn.getresponse().status == 404
    finally:
        srv.shutdown()


def test_cancel_link_needs_both_env(monkeypatch):
    monkeypatch.delenv(cancel_server.ENV_URL, raising=False)
    monkeypatch.setenv(cancel_server.ENV_TOKEN, "tok")
    assert cancel_server.cancel_link() is None
    monkeypatch.setenv(cancel_server.ENV_URL, "http://mac.ts.net:8787/")
    assert cancel_server.cancel_link() == "http://mac.ts.net:8787/cancel?t=tok"
