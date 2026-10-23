"""
Serve the Database Management dashboard from this machine.

The front end is this server; PostgreSQL is the only data source. On a timer the
server re-reads PostgreSQL, rebuilds the page in memory and serves that - so the
page every viewer gets is a plain self-contained HTML file with no outbound calls
of its own, and the data behind it is never older than REFRESH_SECONDS.

    python serve.py

Settings, all optional, read from the environment:
    PORT=8090                 port to listen on
    BIND=0.0.0.0              0.0.0.0 serves the LAN and any forwarded public IP;
                              use 127.0.0.1 to keep it on this machine only
    REFRESH_SECONDS=900       how old the data may get before a re-read (15 min)
    DASH_USER / DASH_PASS     when both are set, every request needs this login
    REFRESH_TOKEN=<secret>    lets /refresh?token=<secret> force a rebuild

Endpoints:
    /            the dashboard
    /healthz     JSON: age of the data, last error, last run summary
    /refresh     force a rebuild (needs REFRESH_TOKEN, or a logged-in user)
"""
import base64
import gzip
import hmac
import io
import json
import os
import socket
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import refresh as R

PORT = int(os.environ.get("PORT", "8090"))
BIND = os.environ.get("BIND", "0.0.0.0")
TTL = int(os.environ.get("REFRESH_SECONDS", "900"))
USER = os.environ.get("DASH_USER") or ""
PASS = os.environ.get("DASH_PASS") or ""
TOKEN = os.environ.get("REFRESH_TOKEN") or ""

_lock = threading.Lock()
_state = {"html": None, "built": 0.0, "error": None, "summary": [], "builds": 0}


def _build():
    """Re-read PostgreSQL and render. A failure keeps the previous page on air."""
    try:
        data, lines = R.build_data(log=None)
        html = R.render_html(data)
        _state.update(html=html, built=time.time(), error=None, summary=lines,
                      builds=_state["builds"] + 1)
        R.publish(html, log=lambda m: print(m.strip()))   # keep the server folder current
        print("[%s] rebuilt from PostgreSQL (%d bytes)"
              % (time.strftime("%H:%M:%S"), len(html)))
    except SystemExit as e:                     # refresh.py exits on a fatal problem
        _state["error"] = str(e)
        print("[%s] REFRESH FAILED: %s" % (time.strftime("%H:%M:%S"), e))
    except Exception as e:                      # network blip, malformed cell, ...
        _state["error"] = "%s: %s" % (type(e).__name__, e)
        print("[%s] REFRESH ERROR: %s" % (time.strftime("%H:%M:%S"), _state["error"]))


def ensure(force=False):
    with _lock:
        fresh = _state["html"] and (time.time() - _state["built"]) < TTL
        if force or not fresh:
            _build()
        if _state["html"] is None:              # first build failed - last file on disk
            path = os.path.join(R.HERE, "dashboard.html")
            if os.path.exists(path):
                _state["html"] = io.open(path, encoding="utf-8").read()
                _state["built"] = os.path.getmtime(path)
                print("    serving the last dashboard.html on disk while PostgreSQL is unavailable")
        return _state["html"]


def authorised(header):
    if not (USER and PASS):
        return True
    if not header or not header.startswith("Basic "):
        return False
    try:
        got = base64.b64decode(header[6:]).decode("utf-8", "replace")
    except Exception:
        return False
    return hmac.compare_digest(got, "%s:%s" % (USER, PASS))


class Handler(BaseHTTPRequestHandler):
    server_version = "DatabaseManagementBI"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):          # one tidy line per request
        print("[%s] %s %s" % (time.strftime("%H:%M:%S"), self.address_string(), fmt % args))

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        raw = body.encode("utf-8") if isinstance(body, str) else body
        head = {"Content-Type": ctype, "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer"}
        if "gzip" in (self.headers.get("Accept-Encoding") or "") and len(raw) > 1024:
            raw = gzip.compress(raw, 6)
            head["Content-Encoding"] = "gzip"
        head["Content-Length"] = str(len(raw))
        head.update(extra or {})
        self.send_response(code)
        for k, v in head.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        query = self.path.split("?", 1)[1] if "?" in self.path else ""

        if path == "/healthz":                  # never behind auth, so uptime checks work
            age = int(time.time() - _state["built"]) if _state["built"] else None
            return self._send(HTTPStatus.OK, json.dumps({
                "ok": _state["html"] is not None,
                "data_age_seconds": age, "refresh_seconds": TTL,
                "builds": _state["builds"], "last_error": _state["error"],
                "summary": _state["summary"][:4],
            }, indent=1), "application/json; charset=utf-8")

        if not authorised(self.headers.get("Authorization")):
            return self._send(HTTPStatus.UNAUTHORIZED, "Authentication required.",
                              "text/plain; charset=utf-8",
                              {"WWW-Authenticate": 'Basic realm="Dashboard"'})

        if path == "/refresh":
            tok = ""
            for part in query.split("&"):
                if part.startswith("token="):
                    tok = part[6:]
            if TOKEN and not hmac.compare_digest(tok, TOKEN) and not (USER and PASS):
                return self._send(HTTPStatus.FORBIDDEN, "Bad token.", "text/plain; charset=utf-8")
            ensure(force=True)
            return self._send(HTTPStatus.OK, json.dumps(
                {"rebuilt": True, "error": _state["error"], "summary": _state["summary"][:4]},
                indent=1), "application/json; charset=utf-8")

        if path != "/":
            return self._send(HTTPStatus.NOT_FOUND, "Not found.", "text/plain; charset=utf-8")

        html = ensure()
        if html is None:
            return self._send(HTTPStatus.SERVICE_UNAVAILABLE,
                              "The dashboard could not be built from PostgreSQL.\n\n%s"
                              % (_state["error"] or "unknown error"),
                              "text/plain; charset=utf-8")
        age = int(time.time() - _state["built"])
        return self._send(HTTPStatus.OK, html, extra={
            "Cache-Control": "no-cache, must-revalidate",
            "X-Data-Age-Seconds": str(age)})


def keep_warm():
    """Rebuild a little before the page goes stale, so no viewer waits for a fetch."""
    while True:
        time.sleep(max(30, TTL // 2))
        try:
            if _state["built"] and (time.time() - _state["built"]) >= TTL:
                with _lock:
                    _build()
        except Exception as e:
            print("    keep-warm error: %r" % (e,))


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    print("Building the first page from PostgreSQL ...")
    ensure(force=True)
    if _state["error"] and _state["html"] is None:
        sys.exit("FAILED to build at startup: %s" % _state["error"])
    threading.Thread(target=keep_warm, daemon=True).start()
    srv = ThreadingHTTPServer((BIND, PORT), Handler)
    srv.daemon_threads = True
    print("\n  dashboard   http://%s:%d/    (this machine: http://localhost:%d/)"
          % (lan_ip(), PORT, PORT))
    print("  health      http://%s:%d/healthz" % (lan_ip(), PORT))
    print("  data reread every %d s from PostgreSQL" % TTL)
    print("  auth        %s" % ("on (DASH_USER/DASH_PASS)" if USER and PASS
                                else "OFF - anyone who can reach this port sees the data"))
    print("\n  Ctrl+C to stop.\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
