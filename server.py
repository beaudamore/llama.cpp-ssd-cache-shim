"""HTTP front: the OpenAI-compatible proxy plus the shim's own /shim/* admin endpoints."""
import http.client, json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from shimlog import log

CHAT_PATHS = {"/v1/chat/completions", "/chat/completions", "/v1/completions", "/completions", "/completion"}
HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-authorization",
       "proxy-authenticate", "content-length", "host"}

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    engine = None   # set by make_server

    def log_message(self, *a):
        pass

    def _send(self, status, body, ctype="application/json"):
        data = json.dumps(body).encode() if not isinstance(body, bytes) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close"); self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/shim/status":
            return self._send(200, self.engine.status())
        self.proxy()

    def do_POST(self):
        if self.path == "/shim/reconcile":
            return self._send(200, {"removed": self.engine.reconcile()})
        if self.path == "/shim/flush":
            return self._send(200, {"saved": self.engine.flush()})
        self.proxy()

    do_PUT = do_DELETE = do_OPTIONS = do_HEAD = do_PATCH = lambda self: self.proxy()

    def _pin(self, body):
        """For a chat/completion request: choose the slot and inject id_slot. Returns (body, slot or None)."""
        try:
            data = json.loads(body)
        except ValueError:
            return body, None
        if not isinstance(data, dict) or "id_slot" in data:
            return body, None
        eng = self.engine
        key, chat_id, source, persist = eng.classify({k.lower(): v for k, v in self.headers.items()}, data)
        model = str(data.get("model") or eng.model_alias)
        slot = eng.acquire(key, chat_id, source, persist, model)
        data["id_slot"] = slot
        log("debug", f"{key} -> slot {slot} ({source}{'' if persist else ', no persist'})")
        return json.dumps(data).encode(), slot

    def proxy(self):
        eng = self.engine; cfg = eng.cfg
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        fwd = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        slot = None; ok = False
        if self.command == "POST" and self.path.split("?")[0] in CHAT_PATHS and body:
            body, slot = self._pin(body)
        conn = http.client.HTTPConnection(cfg.upstream_host, cfg.upstream_port, timeout=3600)
        try:
            conn.request(self.command, self.path, body=body, headers=fwd)
            resp = conn.getresponse()
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in HOP:
                    self.send_header(k, v)
            self.send_header("Connection", "close"); self.end_headers()
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                self.wfile.write(chunk); self.wfile.flush()
            ok = resp.status == 200
        except OSError as e:
            try:
                self._send(503, {"error": {"message": f"upstream llama-server error: {e}", "type": "unavailable"}})
            except OSError:
                pass
        finally:
            conn.close()
            if slot is not None:
                eng.release(slot, ok)

class Server(ThreadingHTTPServer):
    # HTTPServer defaults allow_reuse_address to True (SO_REUSEADDR). On Windows that lets a second shim bind the same
    # port while the first is still listening, and connections then go to either one. Off, a second start fails at bind
    # (WinError 10048 / EADDRINUSE) instead of silently overlapping.
    allow_reuse_address = False
    allow_reuse_port = False

def make_server(cfg, engine):
    Handler.engine = engine
    srv = Server((cfg.listen_host, cfg.listen_port), Handler)
    srv.daemon_threads = True
    return srv

def admin_call(cfg, method, path):
    """Call a running shim's /shim/* endpoint from the CLI. Returns (status, bytes)."""
    conn = http.client.HTTPConnection("127.0.0.1", cfg.listen_port, timeout=3600)
    try:
        conn.request(method, path); resp = conn.getresponse(); data = resp.read()
    finally:
        conn.close()
    return resp.status, data
