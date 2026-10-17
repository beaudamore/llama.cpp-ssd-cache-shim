# Architecture

## The problem it solves

`llama-server` has three cache layers of its own, all in memory:

| Layer | Scope | Flag | What it does |
| --- | --- | --- | --- |
| Prefix cache | per slot | always on | A request routed to a slot reuses the tokens its prompt shares with the slot's previous prompt. |
| Context checkpoints | per slot | `--checkpoint-min-step`, `--ctx-checkpoints` | For recurrent and SWA models, lets a prompt that changed in the middle resume from the last checkpoint before the change. |
| RAM prompt cache | per server | `--cache-ram` | When a slot is reused for a different conversation, the old state is parked in host RAM and reloaded if that conversation returns. |

All three vanish when the state is evicted from RAM or the server restarts. llama-server also exposes a manual fourth
layer: `POST /slots/{id}?action=save` writes one slot's complete state (attention KV, recurrent state, token list) to a
file under `--slot-save-path`, and `action=restore` loads it back. Nothing in llama-server calls these automatically.

This shim is the automation around that fourth layer.

With the shim in place the RAM prompt cache becomes redundant: every eviction is already written to SSD, and every return is
restored from SSD before the request reaches llama-server, so the RAM copy is written and almost never read. Run
llama-server with `--cache-ram 0` to reclaim that memory. The only cases it still covered are task and temporary-chat
states, which the shim never persists, and the first request per slot after a shim restart. See
[llama-server.md](llama-server.md) for the full list of flags that interact with the shim.

## Components

```text
ssd_cache_shim.py   entry point, CLI, threads, signals
   |
   +-- server.py    HTTP front: proxies everything; on chat requests asks the engine for a slot and injects id_slot
   +-- engine.py    policy: keys, acquire/release, save-before-evict, restore-on-return, idle saves, reconcile
         |
         +-- slots.py     what each llama-server slot holds (key, dirty, busy, last used)
         +-- store.py     index of files on SSD (ssd-cache-index.json) and the age / size enforcement
         +-- upstream.py  HTTP client for llama-server's /props, /slots and /slots/N?action=...
   +-- config.py    .env parsing
   +-- shimlog.py   logging
```

The engine is the only module that touches more than one of the others. Everything else is a leaf.

## Conversation keys

Each request gets a key, and the key decides which slot and which file it maps to.

| Source | Key | Persisted to SSD |
| --- | --- | --- |
| Open WebUI chat, via the chat id header | `<model>__<chat id>` | yes, unless the id starts with `temporary:`, `local:` or `channel:` (Open WebUI never stores those chats) |
| Open WebUI task (title, tags, follow-ups), via the task header | `task:<task>:<chat id>` | never |
| Anything without a chat id | `<model>__h-<sha1 of system prompt + first user turn>` | yes if `PERSIST_FALLBACK=true` |

The model id is part of the key so that the same chat id under a different model never restores the wrong state. Files
are additionally stamped (see below), so even a model swap behind the same alias is safe.

The hash fallback is stable for the life of a conversation because the system prompt and the first user turn do not
change as the conversation grows. Two different conversations with the same system prompt and the same opening message
collide, which is accepted: the loser simply re-prefills.

## Slot table

The shim keeps its own view of every llama-server slot:

| Field | Meaning |
| --- | --- |
| `key` | the conversation currently on that slot, or `None` |
| `persist` | whether this conversation may be written to SSD |
| `dirty` | the slot has produced tokens since it was last saved |
| `busy` | number of in-flight operations (requests, saves, restores) using the slot; eviction only considers `busy == 0` |
| `last_used` | for least-recently-used victim choice |

The table is guarded by one condition variable. A request that finds no idle slot waits on it; llama-server would have
queued the request anyway.

## Request path

1. `server.py` receives `POST /v1/chat/completions` (or the other completion paths). Non-chat requests are proxied
   untouched. Requests that already carry `id_slot` are proxied untouched too, so a client can bypass the shim's pinning.
2. `engine.classify()` derives the key from the headers and body.
3. `engine.acquire()`:
   - If the key is already on a slot: mark busy, return it.
   - Otherwise choose a victim among the slots this model may use (`MODEL_SLOTS`: a reserved model gets only its own
     slots, every other model gets the unreserved ones): an empty slot, then a slot holding a non-persistent conversation
     (a task), then a clean slot, then the least recently used. If the victim is dirty and persistent and `SAVE_ON_EVICT`
     is on, save it to SSD first (synchronously; the slot is idle so llama-server accepts the save).
   - Record the new key on the slot. If a file for it exists on SSD, restore it into the slot.
4. `server.py` injects `id_slot` into the JSON body and streams the upstream response through unchanged.
5. `engine.release()` clears busy, marks the slot dirty if the response was a 200, and wakes any waiter.

Because the pinned slot now holds either the conversation's own previous state (restored or still resident) or nothing
useful, llama-server's prefix cache does the rest: it matches the new prompt against the slot's tokens and only
processes what is new. llama-server's RAM prompt cache still operates underneath; pinning does not disable it.

## Saving

Two triggers write a slot to SSD:

- **Idle save.** A background thread checks every ten seconds and saves any dirty, persistent, idle slot that has been
  idle for `IDLE_SAVE_SECONDS`. After it, the slot is clean, and a later eviction costs nothing.
- **Save on evict.** When a dirty persistent slot is chosen as a victim, it is saved before being reused. This covers a
  conversation that is pushed out before its idle save.

A save is one call to llama-server, which writes one file. The file is llama-server's own serialisation format and is
opaque to the shim. Sizes scale with tokens in context, KV type, and the model: tens of MB for short chats, a few GB for a
full 128k context at q8 on a 9B model.

## The index

`SLOT_SAVE_DIR/ssd-cache-index.json` records, for every file: key, model, chat id, source, token count, byte size, created
and last used times, and the stamp. The index is authoritative. On startup:

- an index entry whose file is missing is dropped;
- an entry whose stamp differs from the current stamp is deleted along with its file;
- a `.bin` in the directory with no entry is deleted.

The stamp is a hash of llama-server's reported model path, context size per slot and slot count. Change any of these and
every saved state is invalid, so it is thrown away rather than risk restoring a mismatched file. llama-server would reject
or misinterpret such a restore.

The directory therefore belongs to the shim. Do not point two shims, or a shim and manual save calls, at the same one.

## Cleanup

`engine.reconcile()` runs every `RECONCILE_INTERVAL_MINUTES` in-process, on `POST /shim/reconcile`, and on
`ssd_cache_shim.py --reconcile` (which calls the running shim, or sweeps the directory itself if the shim is down).

1. If `OPENWEBUI_URL` and `OPENWEBUI_API_KEY` are set: first a canary, `GET /api/v1/chats/?page=1`, which answers 200
   only to a valid key; anything else aborts the sweep. Then for every file keyed by an Open WebUI chat id, `GET
   /api/v1/chats/{id}`. A 200 keeps the file. A 401 or 404 deletes it: Open WebUI answers 401, not 404, for a chat that
   does not exist or that the key's user may not read, which is why the canary matters, since a bad key would also
   answer 401 on every chat. Any other status or a connection failure raises and the sweep stops without deleting. If
   the deleted conversation is still on a GPU slot, that slot is marked non-persistent so it is never written back.
2. Files unused for more than `MAX_AGE_DAYS` are deleted. This is the only cleanup for hash-keyed files.
3. Least recently used files are deleted until the total is under `MAX_BYTES`.

This is a sweep, not an event hook, by design: it needs nothing configured on the Open WebUI side beyond an API key, it
survives the shim being down when a chat is deleted, and it cannot be missed.

## Multimodal prompts

Save and restore cover slots that processed images. The one caveat is in llama-server itself: it never places a context
checkpoint right after an image chunk, so an edit landing just past an image resumes from the checkpoint before it.

## Limits

- **Whole-slot granularity.** A file is one conversation's complete state. There is no sharing of common prefixes between
  files, and a conversation whose early prompt changed (an edited system prompt) restores a state llama-server then
  mostly discards.
- **Synchronous.** Save and restore block the slot for one sequential write or read. Other slots keep serving.
- **Shim restart.** The shim forgets what the slots hold. Nothing is corrupted; the first request to each slot cannot reuse
  what was there, and llama-server's RAM cache may still restore it. Files on SSD are unaffected.
- **One shim per llama-server.** The shim assumes it is the only thing deciding slot assignment. Clients that set
  `id_slot` themselves bypass it for that request.
- **Per-request LoRA adapters.** llama-server clears a slot's cache when the adapter set on that slot changes, including a
  state the shim just restored there. `MODEL_SLOTS` keeps each adapter's model id on its own slot so the set never
  changes on a slot; without it, a restore followed by an adapter switch is a wasted read plus a full re-prefill.
- **Hash collisions.** Two different conversations with identical system prompt and opening message share a key.
