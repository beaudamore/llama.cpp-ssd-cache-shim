# llama.cpp SSD cache shim

A small OpenAI-compatible proxy that sits between your chat client and one `llama-server`, and keeps each conversation's
KV cache on SSD when it is not on the GPU. Switch back to a conversation from last week and it resumes from a file
instead of re-prefilling the whole prompt.

```text
Open WebUI / Hermes / curl  -->  ssd_cache_shim.py :8090  -->  llama-server :8080
                                        |
                                        +--> SLOT_SAVE_DIR/   (same directory as llama-server --slot-save-path)
```

- Pure Python standard library, one process, no dependencies. Python 3.9+.
- Windows, macOS and Linux. Launchers for each, plus launchd and systemd units.
- Works with any client that speaks `/v1/chat/completions`. Open WebUI gets exact per-chat keys through two headers;
  anything else is keyed by a hash of its first turn.
- Cleans up after itself: deletes files for chats that Open WebUI has deleted, and enforces an age and a size budget.

## Why

`llama-server` already caches the prompt prefix per slot and keeps evicted slot states in host RAM (`--cache-ram`).
Both are lost when the state is evicted from RAM or the server restarts. With long contexts a handful of conversations
fills that RAM cache, and every switch back to an older chat means a full re-prefill. This shim adds the SSD tier under
it, using llama-server's own slot save and restore endpoints, so the cost of coming back to a conversation becomes one
sequential read.

## Quick start

1. Run `llama-server` with `--slot-save-path /path/to/llama-slot-cache` (the directory must exist). Add `--cache-ram 0`
   too: the shim replaces llama-server's RAM prompt cache, so that RAM can go back to the system. See
   [docs/llama-server.md](docs/llama-server.md).
2. `cp .env.example .env` and set `SLOT_SAVE_DIR` to that same directory and `UPSTREAM_PORT` to llama-server's port.
3. Start the shim:
   - Windows: `start-shim.cmd`
   - macOS / Linux: `./start-shim.sh`
4. Point your client at `http://<host>:8090/v1` instead of llama-server.
5. Open WebUI users: add the two headers described in [docs/openwebui.md](docs/openwebui.md) so chats are keyed by id.

Stop with `stop-shim.cmd` / `./stop-shim.sh`, which saves every unsaved slot first. Always stop the shim before llama-server.

## Commands

```sh
python ssd_cache_shim.py                 # run (reads .env next to the script)
python ssd_cache_shim.py --env other.env # another config (one shim per llama-server)
python ssd_cache_shim.py --status        # slot table and SSD totals of the running shim
python ssd_cache_shim.py --flush         # save every dirty idle slot now
python ssd_cache_shim.py --reconcile     # cleanup sweep now (via the running shim, or directly if it is down)
```

The same three actions are available over HTTP as `GET /shim/status`, `POST /shim/flush`, `POST /shim/reconcile`.

## Documentation

| Document | Covers |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | How it works: keys, slot pinning, eviction, save and restore, the index, cleanup, limits |
| [docs/configuration.md](docs/configuration.md) | Every `.env` key |
| [docs/llama-server.md](docs/llama-server.md) | Which llama-server flags matter: `--slot-save-path`, why `--cache-ram 0`, slots, context, checkpoints |
| [docs/openwebui.md](docs/openwebui.md) | Open WebUI setup: connection, headers, API key, tasks and subagents |
| [docs/platforms.md](docs/platforms.md) | Running as a service on Windows, macOS and Linux; scheduled cleanup |
| [docs/development.md](docs/development.md) | Code layout, testing against a scratch llama-server, extending the shim |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Symptoms, causes, fixes |
| [docs/case-study-t600.md](docs/case-study-t600.md) | First deployment: a 9B model on a 4 GB laptop GPU, with the real file sizes and restore log |

## Requirements

- A current `llama-server` from llama.cpp: it must expose `total_slots` in `/props`, the `/slots/{id}?action=save|restore`
  endpoints, and accept `id_slot` on completion requests. All three have been in llama-server for a long time; the shim
  was developed against a build from October 2026. Models with recurrent layers (Qwen3.5, Mamba hybrids) are supported;
  llama-server serialises their recurrent state along with the attention KV.
- Python 3.9 or newer.

## Status

Working and tested against llama-server with a two-slot model: eviction with save, restore on return, task requests kept
off disk, hash-keyed clients, flush, status and reconcile. Not yet battle-tested under heavy concurrent load.
