# Development

## Layout

```text
ssd_cache_shim.py       entry point: CLI flags, threads, signal handling
config.py               .env parsing, Cfg
upstream.py             llama-server client (/props, /slots, /slots/N?action=save|restore|erase)
store.py                SSD index and age/size enforcement
slots.py                slot table and victim choice
engine.py               policy: keys, acquire/release, saves, restores, reconcile
server.py               HTTP front (proxy + /shim/* admin endpoints)
shimlog.py              logging
start-shim.cmd/.sh      foreground launchers
stop-shim.cmd/.sh       flush then stop
deploy/                 launchd plist, systemd units
docs/                   this documentation
.env.example            annotated configuration template
```

Standard library only, by intent. A single `pip install` step would double the setup instructions for three platforms
and this tool needs nothing a dependency would give it. Keep it that way unless a feature truly cannot be done without.

Python 3.9 is the floor. Avoid syntax newer than that (no `match`, no `X | Y` type unions at runtime).

## Running against a scratch llama-server

Any small GGUF works. CPU is fine; the shim does not care where the model runs.

```sh
mkdir -p /tmp/slotcache
llama-server -m small-model.gguf --device none -ngl 0 -np 2 -c 8192 --jinja \
  --host 127.0.0.1 --port 18080 --alias test --slot-save-path /tmp/slotcache
```

A test `.env`:

```text
LISTEN_PORT=18090
UPSTREAM_PORT=18080
SLOT_SAVE_DIR=/tmp/slotcache
IDLE_SAVE_SECONDS=5
RECONCILE_INTERVAL_MINUTES=0
LOG_LEVEL=debug
```

Run `python ssd_cache_shim.py --env test.env`, then exercise it with curl:

```sh
chat() { curl -s http://127.0.0.1:18090/v1/chat/completions -H "Content-Type: application/json" \
  -H "X-OpenWebUI-Chat-Id: $1" ${3:+-H "X-OpenWebUI-Task: $3"} \
  -d "{\"model\":\"test\",\"messages\":[{\"role\":\"user\",\"content\":\"$2\"}],\"max_tokens\":4}"; }

chat A "hi"            # slot 0
chat B "hi"            # slot 1
chat C "hi"            # evicts A (LRU): A saved to SSD, C takes slot 0
chat A "hi again"      # evicts B: B saved, A restored from SSD into slot 1
chat A "title" title_generation   # task: pinned, never saved
```

With `LOG_LEVEL=debug` every decision is logged. `python ssd_cache_shim.py --env test.env --status` shows the slot table.
Wait `IDLE_SAVE_SECONDS` and watch the idle save fire; `--flush` and `--reconcile` exercise the rest.

## Invariants worth keeping

- A slot is only saved or restored while `busy == 0` from everyone else's point of view and `busy == 1` from the
  operation's own. llama-server rejects save and restore on a slot that is processing, and the shim must never let a
  request slip into a slot mid-restore.
- The index is the truth about the directory. Anything that creates or deletes a file must go through `Store` so the
  index stays consistent. A crash between a llama-server save and the index write leaves an orphan `.bin`, which the next
  startup deletes.
- `persist=False` conversations (tasks, temporary chats) are never written. The flag is checked in both save paths.
- The stamp must change whenever a saved file could become invalid: model, context size per slot, slot count.
- Reconcile must fail closed. Any Open WebUI answer other than 200 or 404 aborts the sweep without deleting.

## Extending

**Another client's conversation id.** If a client sends its id in a different header, set `CHAT_ID_HEADER`. If it sends
it somewhere in the body, extend `Engine.classify()`; return the same tuple shape `(key, chat_id, source, persist)` and
use a new `source` value so reconcile can tell it apart (reconcile only checks `openwebui` entries against Open WebUI).

**Another deletion oracle.** Add a check beside `_openwebui_chat_exists()` keyed on the new `source`, keeping the
fail-closed behaviour.

**Asynchronous saves.** The save-on-evict path is synchronous so that the victim's state is on SSD before llama-server
overwrites it. Making it asynchronous would require llama-server to copy the state first, which it does not do. The idle
save already runs in the background, so under normal pacing evictions find clean slots.

**Prefix sharing between files.** Out of scope. llama-server's save format is one slot's full state; a shared-prefix store
would need llama-server support.

## Style

Short modules, one responsibility each, comments only where the reason is not obvious from the code. Log lines are meant
to be read by someone watching the shim work: say what moved where and how long it took.
