"""HTTP client for the llama-server control endpoints the shim uses: /props, /slots, /slots/N?action=..."""
import http.client, json

class Upstream:
    def __init__(self, cfg):
        self.host, self.port, self.key = cfg.upstream_host, cfg.upstream_port, cfg.upstream_api_key

    def call(self, method, path, body=None, timeout=3600):
        hdrs = {"Content-Type": "application/json"}
        if self.key:
            hdrs["Authorization"] = f"Bearer {self.key}"
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            conn.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=hdrs)
            resp = conn.getresponse(); data = resp.read()
        finally:
            conn.close()
        try:
            return resp.status, json.loads(data) if data else {}
        except ValueError:
            return resp.status, {"raw": data.decode(errors="replace")}

    def props(self):
        return self.call("GET", "/props", timeout=30)

    def slots(self):
        return self.call("GET", "/slots", timeout=30)

    def slot(self, id_slot, action, filename=None):
        """action: save | restore | erase. save/restore take a bare filename inside llama-server's --slot-save-path."""
        body = {"filename": filename} if filename else {}
        return self.call("POST", f"/slots/{id_slot}?action={action}", body)
