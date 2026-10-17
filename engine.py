"""The policy: conversation keys, slot pinning with save-before-evict and restore-on-return, idle saves, cleanup."""
import hashlib, http.client, os, threading, time, urllib.parse
from shimlog import log
from slots import Slots
from store import Store
from upstream import Upstream

NON_SAVED_PREFIXES = ("temporary:", "local:", "channel:")   # Open WebUI chat ids that are never stored in its DB

class Engine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.up = Upstream(cfg)
        self.store = Store(cfg)
        self.slots = None
        self.model_alias = None
        self.stop = threading.Event()

    # -- startup ------------------------------------------------------------------------------------------------------
    def connect(self):
        """Wait for llama-server, read its slot count and model, and load the SSD index against that stamp."""
        while True:
            try:
                st, props = self.up.props()
                if st == 200 and "total_slots" in props:
                    break
                log("warn", f"llama-server /props returned {st}; retrying")
            except OSError as e:
                log("warn", f"llama-server not reachable on {self.cfg.upstream_host}:{self.cfg.upstream_port} ({e}); retrying")
            time.sleep(5)
        n = int(props["total_slots"])
        self.model_alias = props.get("model_alias") or os.path.basename(props.get("model_path", "model"))
        n_ctx_slot = (props.get("default_generation_settings") or {}).get("n_ctx")
        stamp = hashlib.sha1(f"{props.get('model_path')}|{n_ctx_slot}|{n}".encode()).hexdigest()[:16]
        self.slots = Slots(n)
        self.store.load(stamp)
        log("info", f"llama-server: model {self.model_alias}, {n} slots, n_ctx/slot {n_ctx_slot}, stamp {stamp}")

    # -- keys ---------------------------------------------------------------------------------------------------------
    def classify(self, headers, data):
        """Returns (key, chat_id, source, persist). source: openwebui | hash | task. headers are lower-cased."""
        model = str(data.get("model") or self.model_alias)
        chat_id = (headers.get(self.cfg.chat_id_header) or "").strip()
        task = (headers.get(self.cfg.task_header) or "").strip()
        if not chat_id:
            md = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
            chat_id = str(md.get("chat_id") or "").strip()
            task = task or str(md.get("task") or "").strip()
        if task:
            return f"task:{task}:{chat_id or self._hash(data)}", chat_id or None, "task", False
        if chat_id:
            saved = not chat_id.startswith(NON_SAVED_PREFIXES)
            return f"{model}__{chat_id}", chat_id, "openwebui", saved
        return f"{model}__h-{self._hash(data)}", None, "hash", self.cfg.persist_fallback

    @staticmethod
    def _hash(data):
        """Stable id for clients without chat ids: system prompt + first user turn."""
        msgs = data.get("messages") or []
        parts = ["s:" + str(m.get("content"))[:4000] for m in msgs if m.get("role") == "system"]
        first_user = next((m for m in msgs if m.get("role") == "user"), None)
        if first_user:
            parts.append("u:" + str(first_user.get("content"))[:4000])
        if not parts:
            parts.append("p:" + str(data.get("prompt"))[:4000])
        return hashlib.sha1("\n".join(parts).encode()).hexdigest()[:20]

    # -- slot operations ----------------------------------------------------------------------------------------------
    def _save(self, i):
        """Write slot i to SSD. The caller holds the slot busy."""
        s = self.slots.t[i]
        name = Store.filename(s["key"])
        try:
            st, r = self.up.slot(i, "save", name)
        except OSError as e:
            log("warn", f"slot {i} save failed: {e}"); return False
        if st != 200:
            log("warn", f"slot {i} save rejected ({st}): {r}"); return False
        self.store.put(name, s["model"], s["key"], s["chat_id"], s["source"], r.get("n_saved", 0), r.get("n_written", 0))
        log("info", f"slot {i} -> SSD {name} ({r.get('n_saved')} tokens, {r.get('n_written', 0) / 2**20:.0f} MiB, "
                    f"{r.get('timings', {}).get('save_ms', 0):.0f} ms)")
        return True

    def _restore(self, i, name):
        try:
            st, r = self.up.slot(i, "restore", name)
        except OSError as e:
            log("warn", f"slot {i} restore failed: {e}"); return False
        if st != 200:
            log("warn", f"slot {i} restore of {name} rejected ({st}): {r}; dropping file"); self.store.remove(name); return False
        self.store.touch(name)
        log("info", f"SSD {name} -> slot {i} ({r.get('n_restored')} tokens, {r.get('timings', {}).get('restore_ms', 0):.0f} ms)")
        return True

    def acquire(self, key, chat_id, source, persist, model):
        """Pin key to a slot, evicting (and saving) another conversation if needed. Returns the slot id, marked busy."""
        sl = self.slots
        with sl.cond:
            while True:
                i = sl.find(key)
                if i is not None:
                    s = sl.t[i]; s["busy"] += 1; s["last_used"] = time.time()
                    return i
                i = sl.victim()
                if i is not None:
                    break
                sl.cond.wait(1.0)
            s = sl.t[i]; s["busy"] += 1
            evict = dict(s)
        try:
            if evict["key"] is not None and evict["dirty"] and evict["persist"] and self.cfg.save_on_evict:
                log("info", f"slot {i}: evicting {evict['key']} for {key}")
                self._save(i)
            name = Store.filename(key)
            with sl.cond:
                s.update(key=key, name=name, model=model, chat_id=chat_id, source=source, persist=persist,
                         dirty=False, last_used=time.time())
            if persist and self.store.has(name):
                self._restore(i, name)
            return i
        except Exception:
            with sl.cond:
                s["busy"] -= 1; sl.cond.notify_all()
            raise

    def release(self, i, ok):
        with self.slots.cond:
            s = self.slots.t[i]
            s["busy"] -= 1; s["last_used"] = time.time()
            if ok:
                s["dirty"] = True
            self.slots.cond.notify_all()

    # -- background ---------------------------------------------------------------------------------------------------
    def idle_saver(self):
        while not self.stop.wait(10):
            self.flush(idle_for=self.cfg.idle_save_seconds)

    def flush(self, idle_for=0):
        """Save every dirty persistent idle slot that has been idle at least idle_for seconds. Returns the count."""
        saved = 0
        for i in list(self.slots.t):
            with self.slots.cond:
                s = self.slots.t[i]
                if not (s["dirty"] and s["persist"] and s["busy"] == 0 and time.time() - s["last_used"] >= idle_for):
                    continue
                s["busy"] += 1
            try:
                if self._save(i):
                    with self.slots.cond:
                        s["dirty"] = False
                    saved += 1
            finally:
                with self.slots.cond:
                    s["busy"] -= 1; self.slots.cond.notify_all()
        return saved

    def reconciler(self):
        if self.cfg.reconcile_minutes <= 0:
            return
        while not self.stop.wait(self.cfg.reconcile_minutes * 60):
            try:
                self.reconcile()
            except Exception as e:
                log("warn", f"reconcile failed: {e}")

    def _openwebui_chat_exists(self, chat_id):
        u = urllib.parse.urlsplit(self.cfg.openwebui_url)
        cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        conn = cls(u.hostname, u.port, timeout=30)
        try:
            conn.request("GET", f"{u.path}/api/v1/chats/{urllib.parse.quote(chat_id)}",
                         headers={"Authorization": f"Bearer {self.cfg.openwebui_api_key}", "Accept": "application/json"})
            resp = conn.getresponse(); resp.read()
        finally:
            conn.close()
        if resp.status == 200:
            return True
        if resp.status == 404:
            return False
        raise RuntimeError(f"Open WebUI returned {resp.status} for chat {chat_id}; not deleting")

    def reconcile(self):
        """Delete files for chats Open WebUI no longer has, then enforce age and size limits. Returns names removed."""
        removed = []
        if self.cfg.openwebui_url and self.cfg.openwebui_api_key:
            for name, chat_id in self.store.openwebui_entries():
                if not self._openwebui_chat_exists(chat_id):
                    self.store.remove(name); removed.append(name)
                    with self.slots.cond:
                        for s in self.slots.t.values():
                            if s["name"] == name:
                                s["persist"] = False   # still on the GPU, but never write it back
        removed += self.store.enforce(self.cfg.max_age_days, self.cfg.max_bytes)
        log("info", f"reconcile: {len(removed)} files removed, {len(self.store.files)} kept, {self.store.total_bytes() / 2**20:.0f} MiB")
        return removed

    def status(self):
        return {"model": self.model_alias, "slots": self.slots.snapshot(),
                "ssd": {"files": len(self.store.files), "bytes": self.store.total_bytes(), "dir": self.store.dir}}
