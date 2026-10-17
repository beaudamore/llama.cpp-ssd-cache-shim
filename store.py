"""SSD index: which slot files exist in SLOT_SAVE_DIR, for which conversation, stamped with the model they belong to."""
import json, os, re, threading, time
from shimlog import log

INDEX_NAME = "ssd-cache-index.json"
SAFE = re.compile(r"[^A-Za-z0-9._-]")   # llama-server's fs_validate_filename rejects path separators and most punctuation

class Store:
    """The index is the truth: a .bin without an entry is garbage and is removed."""
    def __init__(self, cfg):
        self.dir = cfg.slot_save_dir
        self.path = os.path.join(self.dir, INDEX_NAME)
        self.lock = threading.Lock()
        self.files = {}
        self.stamp = None

    @staticmethod
    def filename(key):
        return SAFE.sub("-", key)[:200] + ".bin"   # key already starts with the model id

    def load(self, stamp):
        """Read the index, drop entries whose file is gone or whose stamp (model/ctx/slots) no longer matches."""
        self.stamp = stamp
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as fh:
                    self.files = json.load(fh).get("files", {})
            except ValueError:
                log("warn", f"index unreadable, starting empty: {self.path}"); self.files = {}
        dropped = 0
        for name in list(self.files):
            full = os.path.join(self.dir, name)
            if not os.path.exists(full):
                del self.files[name]; dropped += 1
            elif self.files[name].get("stamp") != stamp:
                self._unlink(full); del self.files[name]; dropped += 1
        for name in os.listdir(self.dir):
            if name.endswith(".bin") and name not in self.files:
                self._unlink(os.path.join(self.dir, name)); dropped += 1
        self.save()
        log("info", f"index: {len(self.files)} files, {self.total_bytes() / 2**20:.0f} MiB on SSD, {dropped} dropped")

    @staticmethod
    def _unlink(full):
        try:
            os.remove(full); log("info", f"deleted {os.path.basename(full)}")
        except OSError as e:
            log("warn", f"could not delete {full}: {e}")

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"stamp": self.stamp, "files": self.files}, fh, indent=1)
        os.replace(tmp, self.path)

    def has(self, name):
        with self.lock:
            return name in self.files and os.path.exists(os.path.join(self.dir, name))

    def put(self, name, model, key, chat_id, source, n_tokens, nbytes):
        with self.lock:
            now = time.time()
            e = self.files.get(name, {"created": now})
            e.update(model=model, key=key, chat_id=chat_id, source=source, stamp=self.stamp,
                     n_tokens=n_tokens, bytes=nbytes, last_used=now)
            self.files[name] = e; self.save()

    def touch(self, name):
        with self.lock:
            if name in self.files:
                self.files[name]["last_used"] = time.time(); self.save()

    def remove(self, name):
        with self.lock:
            self.files.pop(name, None); self.save()
            full = os.path.join(self.dir, name)
            if os.path.exists(full):
                self._unlink(full)

    def openwebui_entries(self):
        """(filename, chat_id) for every file keyed by an Open WebUI chat id."""
        with self.lock:
            return [(n, e["chat_id"]) for n, e in self.files.items() if e.get("source") == "openwebui" and e.get("chat_id")]

    def total_bytes(self):
        return sum(e.get("bytes", 0) for e in self.files.values())

    def enforce(self, max_age_days, max_bytes):
        """Drop files unused for max_age_days, then least recently used until total <= max_bytes. Returns names removed."""
        gone = []
        with self.lock:
            cutoff = time.time() - max_age_days * 86400
            for name, e in list(self.files.items()):
                if e.get("last_used", 0) < cutoff:
                    gone.append(name)
            by_lru = sorted((n for n in self.files if n not in gone), key=lambda n: self.files[n].get("last_used", 0))
            total = sum(self.files[n].get("bytes", 0) for n in by_lru)
            for name in by_lru:
                if total <= max_bytes:
                    break
                total -= self.files[name].get("bytes", 0); gone.append(name)
        for name in gone:
            self.remove(name)
        return gone
