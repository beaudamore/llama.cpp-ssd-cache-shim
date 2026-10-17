# Troubleshooting

Run with `LOG_LEVEL=debug` while diagnosing. `python ssd_cache_shim.py --status` shows what the shim believes.

| Symptom | Cause | Fix |
| --- | --- | --- |
| `SLOT_SAVE_DIR is required` / `is not a directory` at start | `.env` missing or the directory does not exist | Copy `.env.example` to `.env`; create the directory (the launchers do this). |
| `llama-server not reachable ... retrying` forever | Wrong `UPSTREAM_HOST`/`UPSTREAM_PORT`, or llama-server not up | The shim waits by design; fix the address or start llama-server. |
| `/props returned 503` for a while at start | llama-server is still loading the model | Normal. The shim retries every five seconds. |
| `/props returned 401` | llama-server runs with `--api-key` | Set `UPSTREAM_API_KEY`. |
| `save rejected (400): ... Invalid filename` | A key produced characters llama-server refuses | Should not happen: filenames are sanitised to `[A-Za-z0-9._-]`. Report it with the key from the debug log. |
| `save rejected (400)` mentioning the slot save path | llama-server started without `--slot-save-path` | Add the flag and restart llama-server. |
| `save rejected (500)` on a model with images in context | Older llama-server without multimodal state serialisation | Update llama-server. |
| `restore ... rejected ...; dropping file` | The file was written by a different model or context size and the stamp did not catch it, or the file is corrupt | The shim deletes the file and continues; the conversation re-prefills once. If it repeats, clear the directory. |
| A conversation on a LoRA model id (or the base, right after a LoRA request ran on that slot) re-prefills in full although the restore succeeded | llama-server clears a slot's cache when the adapter set on that slot changes; the restore was fine and was wiped a moment later. `chat.log` shows `clearing cache for lora change` | Reserve a slot per adapter id with `MODEL_SLOTS` and put the shim in front of the LoRA proxy. See [llama-server.md](llama-server.md#lora-adapters). |
| `MODEL_SLOTS names slot(s) [N] but llama-server has M slots` at start | A reserved slot id is outside `0..M-1` | Fix the id, or raise `-np`. Slot ids start at 0. |
| `MODEL_SLOTS reserves every slot` at start | Every slot is given to a listed model, so unlisted ids would wait forever | Leave at least one slot unreserved. |
| Every request re-prefills the whole prompt | The key changes from turn to turn, so each turn lands on a fresh slot. Causes: the client sets `id_slot` itself, something between client and shim strips the chat id header, or the client rewrites the system prompt or first user message every turn (timestamps, injected context), which breaks the hash fallback. | Check with `LOG_LEVEL=debug` which key each request gets. Send a stable chat id header, or stop the client from mutating the first turn. |
| Titles and tags appear as separate conversations in the slot table | The task header is not being sent | Add `X-OpenWebUI-Task = {{TASK}}` to the Open WebUI connection headers. |
| A chat seems to lose its context after a title generation | Same cause as above: the task ran in the chat's slot and was mistaken for a turn | Same fix. The saved file still holds the real conversation up to the previous idle save. |
| `reconcile failed: Open WebUI rejected the API key` | Bad, expired or missing API key; the chat-list canary did not return 200 | Create a key in Open WebUI (Settings > Account > API keys) and set `OPENWEBUI_API_KEY`. Nothing was deleted. |
| Files for chats that still exist get deleted on a multi-user install | The key belongs to a non-admin user, who cannot see other users' chats, so Open WebUI answers 401 for them | Use an admin key, and keep `ENABLE_ADMIN_CHAT_ACCESS` on. |
| `reconcile failed: Open WebUI returned 5xx` | Open WebUI error or restarting | Transient. Nothing was deleted; the next sweep retries. |
| Files for deleted chats are not removed | Sweep not running, or not configured with the Open WebUI key | Check `RECONCILE_INTERVAL_MINUTES`, or run `--reconcile` manually and read its output. |
| Directory keeps growing | Budget too generous, or many hash-keyed conversations that only age out | Lower `MAX_BYTES` / `MAX_AGE_DAYS`, or set `PERSIST_FALLBACK=false` if those clients do not need SSD recall. |
| A request hangs when all slots are busy | Expected: the shim waits for an idle slot, as llama-server would | Add slots (`-np`) or accept the queueing. |
| Shim restarted; first turns are slow | The shim forgot the slot contents; llama-server's own RAM cache may or may not still have them | Expected. Only the first turn per slot pays. Files on SSD are unaffected. |
| Everything on SSD deleted at startup, log says `dropped` | The stamp changed: new model file, new `-c`, new `-np` | Expected. Saved states only match one configuration. |
| `.bin` files you placed in the directory vanished | The directory belongs to the shim; unknown files are deleted | Use a separate directory for manual slot saves. |
| Stop script says `shim not running` but it is | Different `LISTEN_PORT` in the `.env` the stop script read | Set `SHIM_ENV` to the right file. |
