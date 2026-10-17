# Configuration

The shim reads a `.env` file (`KEY=VALUE`, `#` comments, optional quotes) from next to the script, or from `--env PATH`.
A real environment variable with the same name overrides the file, which is how a service manager or a container can
inject secrets. Run one shim per llama-server, each with its own file.

## Network

| Key | Default | Meaning |
| --- | --- | --- |
| `LISTEN_HOST` | `0.0.0.0` | Interface the shim listens on. Use `127.0.0.1` if only local clients connect. |
| `LISTEN_PORT` | `8090` | Port clients connect to. Point them at `http://host:PORT/v1`. |
| `UPSTREAM_HOST` | `127.0.0.1` | llama-server host. |
| `UPSTREAM_PORT` | `8080` | llama-server port. |
| `UPSTREAM_API_KEY` | empty | Bearer key for the shim's own calls to llama-server (`/props`, `/slots`). Needed only if llama-server runs with `--api-key`. Client requests pass their own `Authorization` header through untouched. |

## Storage

| Key | Default | Meaning |
| --- | --- | --- |
| `SLOT_SAVE_DIR` | required | Directory for saved slot files. Must be exactly the directory passed to llama-server as `--slot-save-path`, because the shim sends llama-server a bare filename and llama-server joins it to its own path. The shim deletes any `.bin` here it does not know about. |

Put it on the SSD. Network shares work but make every save and restore a network transfer.

## Keys

| Key | Default | Meaning |
| --- | --- | --- |
| `CHAT_ID_HEADER` | `X-OpenWebUI-Chat-Id` | Request header carrying the conversation id. Any client can send it. |
| `TASK_HEADER` | `X-OpenWebUI-Task` | Request header whose non-empty value marks a side task (title, tags, follow-ups). Task requests are pinned but never saved. |
| `PERSIST_FALLBACK` | `true` | Save conversations that arrive without a chat id (keyed by a hash of system prompt + first user turn). `false` keeps them on the GPU only. |

Both headers are matched case-insensitively. If the chat id header is absent, the shim also looks for `metadata.chat_id`
and `metadata.task` in the JSON body, for clients that forward Open WebUI's metadata object.

## Saving

| Key | Default | Meaning |
| --- | --- | --- |
| `SAVE_ON_EVICT` | `true` | Save a dirty slot before another conversation takes it. `false` relies on the idle save alone. |
| `IDLE_SAVE_SECONDS` | `120` | A dirty slot idle this long is saved in the background. Lower values keep SSD fresher at the cost of more writes while a user is typing. |

## Budget and cleanup

| Key | Default | Meaning |
| --- | --- | --- |
| `MAX_AGE_DAYS` | `30` | Files unused for longer are deleted on the next sweep. The only cleanup that applies to hash-keyed files. Fractions allowed. |
| `MAX_BYTES` | `50G` | After age cleanup, least recently used files are deleted until the directory is under this. Accepts `K`, `M`, `G`, `T` suffixes or a plain byte count. |
| `RECONCILE_INTERVAL_MINUTES` | `60` | How often the sweep runs inside the shim. `0` disables the timer; run `ssd_cache_shim.py --reconcile` from a scheduler instead. |
| `OPENWEBUI_URL` | empty | Base URL of Open WebUI as seen from the shim's host, for example `http://localhost:3000`. |
| `OPENWEBUI_API_KEY` | empty | API key whose user can read the chats. With both set, each Open WebUI-keyed file is checked with `GET /api/v1/chats/{id}` and deleted on 404. Without them, only age and size cleanup run. |

## Logging

| Key | Default | Meaning |
| --- | --- | --- |
| `LOG_LEVEL` | `info` | `debug` logs every key-to-slot decision. `info` logs saves, restores, evictions and sweeps. |

Logs go to stdout, one line per event. The launchers and service units redirect them to a file.
