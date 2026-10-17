"""Configuration: a .env file (KEY=VALUE) with real environment variables overriding it."""
import os, re

def load_env(path):
    """KEY=VALUE lines; '#' comments; optional quotes. Real environment variables override the file."""
    out = {}
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip(); v = v.split(" #")[0].strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                out[k] = v
    for k in list(out):
        if k in os.environ:
            out[k] = os.environ[k]
    return out

def parse_bytes(s):
    """'50G', '512M', '1T', or a plain number of bytes."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGT]?)B?\s*", str(s), re.I)
    if not m:
        raise ValueError(f"bad size: {s}")
    unit = m.group(2).upper()
    return int(float(m.group(1)) * (1024 ** ("KMGT".index(unit) + 1) if unit else 1))

def parse_model_slots(s):
    """'model-a:2,model-b:3' -> {'model-a': {2}, 'model-b': {3}}. List a model twice to give it two slots."""
    out = {}
    for item in str(s).split(","):
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            raise ValueError(f"bad MODEL_SLOTS entry (want model:slot): {item}")
        model, slot = item.rsplit(":", 1)
        out.setdefault(model.strip(), set()).add(int(slot))
    return out

class Cfg:
    def __init__(self, env):
        g = env.get
        self.listen_host = g("LISTEN_HOST", "0.0.0.0")
        self.listen_port = int(g("LISTEN_PORT", "8090"))
        self.upstream_host = g("UPSTREAM_HOST", "127.0.0.1")
        self.upstream_port = int(g("UPSTREAM_PORT", "8080"))
        self.upstream_api_key = g("UPSTREAM_API_KEY", "")
        self.slot_save_dir = g("SLOT_SAVE_DIR", "")
        self.chat_id_header = g("CHAT_ID_HEADER", "X-OpenWebUI-Chat-Id").lower()
        self.task_header = g("TASK_HEADER", "X-OpenWebUI-Task").lower()
        self.persist_fallback = g("PERSIST_FALLBACK", "true").lower() == "true"
        self.model_slots = parse_model_slots(g("MODEL_SLOTS", ""))
        self.save_on_evict = g("SAVE_ON_EVICT", "true").lower() == "true"
        self.idle_save_seconds = int(g("IDLE_SAVE_SECONDS", "120"))
        self.max_age_days = float(g("MAX_AGE_DAYS", "30"))
        self.max_bytes = parse_bytes(g("MAX_BYTES", "50G"))
        self.reconcile_minutes = float(g("RECONCILE_INTERVAL_MINUTES", "60"))
        self.openwebui_url = g("OPENWEBUI_URL", "").rstrip("/")
        self.openwebui_api_key = g("OPENWEBUI_API_KEY", "")
        self.log_level = g("LOG_LEVEL", "info").lower()
        if not self.slot_save_dir:
            raise SystemExit("SLOT_SAVE_DIR is required (same directory as llama-server --slot-save-path)")
        if not os.path.isdir(self.slot_save_dir):
            raise SystemExit(f"SLOT_SAVE_DIR is not a directory: {self.slot_save_dir}")
