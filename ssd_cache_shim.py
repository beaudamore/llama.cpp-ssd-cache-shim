#!/usr/bin/env python3
"""ssd-cache-shim: an OpenAI-compatible proxy in front of one llama-server that keeps conversation KV state on SSD.

Point Open WebUI (or any OpenAI-style client) at this shim; the shim points at llama-server. Each conversation is keyed
by "<model>__<chat id>" (the X-OpenWebUI-Chat-Id header, or a hash of the first user turn for clients without ids) and is
pinned to one llama-server slot while it is on the GPU. When a slot is needed for a different conversation, the shim
first saves the evicted slot's state to SSD (POST /slots/N?action=save) and, if the incoming conversation already has a
file on SSD, restores it into the slot (action=restore) before forwarding the request. Idle dirty slots are also saved on
a timer, so an eviction usually finds the file already current and costs no write.

Cleanup is a periodic sweep (or `--reconcile` from a scheduled task): every file whose chat no longer exists in Open WebUI
(GET /api/v1/chats/{id} answers 401 or 404; the key is verified against the chat list first) is deleted, then
MAX_AGE_DAYS and MAX_BYTES are enforced LRU-first. Files are stamped
with the model path and context size reported by llama-server and dropped when either changes.

llama-server must run with --slot-save-path pointing at the same directory as SLOT_SAVE_DIR. Everything the shim does
not understand (models list, health, metrics, embeddings) is proxied unchanged. Standard library only.

Modules: config.py (.env), upstream.py (llama-server client), store.py (SSD index), slots.py (slot table),
engine.py (policy), server.py (HTTP front), shimlog.py (logging).

usage: ssd_cache_shim.py [--env PATH] [--reconcile] [--status] [--flush]
"""
import argparse, json, os, signal, sys, threading
from config import Cfg, load_env
from engine import Engine
from server import admin_call, make_server
from shimlog import log, set_level

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", default=os.path.join(here, ".env"), help="config file (default: .env next to this script)")
    ap.add_argument("--reconcile", action="store_true", help="run one cleanup sweep and exit (via the running shim if up, else directly)")
    ap.add_argument("--status", action="store_true", help="print the running shim's slot table and exit")
    ap.add_argument("--flush", action="store_true", help="ask the running shim to save every dirty idle slot to SSD and exit")
    a = ap.parse_args()
    cfg = Cfg(load_env(a.env))
    set_level(cfg.log_level)

    if a.status or a.flush or a.reconcile:
        path = "/shim/status" if a.status else "/shim/flush" if a.flush else "/shim/reconcile"
        try:
            st, data = admin_call(cfg, "GET" if a.status else "POST", path)
            print(data.decode()); sys.exit(0 if st == 200 else 1)
        except OSError:
            if not a.reconcile:
                sys.exit(f"shim not running on 127.0.0.1:{cfg.listen_port}")
        eng = Engine(cfg); eng.connect()          # shim is down: sweep the directory directly
        print(json.dumps({"removed": eng.reconcile()})); return

    eng = Engine(cfg); eng.connect()
    threading.Thread(target=eng.idle_saver, daemon=True, name="idle-saver").start()
    threading.Thread(target=eng.reconciler, daemon=True, name="reconciler").start()
    srv = make_server(cfg, eng)

    def stop(signum, _frame):
        log("info", f"signal {signum}: saving dirty slots before exit")
        eng.stop.set()
        try:
            eng.flush()
        finally:
            os._exit(0)
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    log("info", f"listening on {cfg.listen_host}:{cfg.listen_port} -> {cfg.upstream_host}:{cfg.upstream_port}; SSD dir {cfg.slot_save_dir}; "
                f"idle save {cfg.idle_save_seconds}s; max {cfg.max_bytes / 2**30:.0f} GiB / {cfg.max_age_days:g} days; "
                f"reconcile every {cfg.reconcile_minutes:g} min"
                f"{' (Open WebUI check on)' if cfg.openwebui_api_key else ' (age/size only; no OPENWEBUI_API_KEY)'}")
    srv.serve_forever()

if __name__ == "__main__":
    main()
