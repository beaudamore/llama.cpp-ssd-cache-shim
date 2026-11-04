# Open WebUI setup

Tested against Open WebUI 0.12.x and 0.13.0. The shim works with Open WebUI out of the box through the hash fallback, but two
headers make it exact: every chat gets its own key, and the side tasks Open WebUI fires after each reply stay off the SSD.

## 1. Point the connection at the shim

Admin > Settings > Connections. Edit the OpenAI-compatible connection that currently points at llama-server and change its
URL to the shim:

| Where Open WebUI runs | URL |
| --- | --- |
| Docker on the same machine as the shim | `http://host.docker.internal:8090/v1` |
| Same host, no Docker | `http://localhost:8090/v1` |
| Another machine | `http://<shim host>:8090/v1` |

The API key field stays whatever llama-server expects; the shim passes `Authorization` through.

## 2. Add the two headers

In the same connection dialog, under Headers, add:

| Header | Value |
| --- | --- |
| `X-OpenWebUI-Chat-Id` | `{{CHAT_ID}}` |
| `X-OpenWebUI-Task` | `{{TASK}}` |

Open WebUI expands these placeholders per request. `{{CHAT_ID}}` is the chat's database id. `{{TASK}}` is empty for a
normal turn and set to a task name (`title_generation`, `tags_generation`, `follow_up_generation`, and so on) for the
background calls.

Alternative for the chat id only: set `ENABLE_FORWARD_USER_INFO_HEADERS=true` on the Open WebUI container. That sends
`X-OpenWebUI-Chat-Id` on every model request along with the user headers, but it does not send a task header, so title
and tag generation would then land in the chat's own slot and be mistaken for conversation turns. Use the per-connection
headers.

## 3. API key for the cleanup sweep

Settings > Account > API keys > Create. Put the key in `.env` as `OPENWEBUI_API_KEY`, and set `OPENWEBUI_URL` to Open
WebUI's address as seen from the shim's host (`http://localhost:3000` for the default Docker port mapping).

The sweep first fetches the chat list as a canary; only a valid key gets a 200, and anything else aborts the sweep. Then
it asks `GET /api/v1/chats/{id}` for each stored chat. Open WebUI answers 200 for a chat the key's user can read and
401 for one that is missing or belongs to someone the user cannot see, so the key's user has to be allowed to read the
chats: on a single-user install any key works; on a multi-user install use an admin key and make sure admins may read
other users' chats (`ENABLE_ADMIN_CHAT_ACCESS`, on by default). With a non-admin key on a multi-user install, other
users' files would be deleted as if their chats were gone; the cost is one re-prefill each, not data loss, but use an
admin key. A JWT from a browser session works the same way but expires; the API key does not.

## What Open WebUI sends, and how the shim treats it

| Request | Chat id header | Task header | Shim behaviour |
| --- | --- | --- | --- |
| A normal turn | the chat id | empty | pinned to the chat's slot; saved to SSD |
| Title, tags, follow-up, query generation after a reply | the chat id | task name | pinned to an idle slot (preferring one not holding a conversation); never saved |
| A subagent or internal child chat | the child chat's own id | empty | its own slot and file, independent of the parent. These chats are never revisited, so the file is wasted space until the age-out; see [future.md](future.md) for the planned fix |
| Temporary chat (not saved by Open WebUI) | `temporary:...` | empty | pinned; never saved |
| Channel message | `channel:...` | empty | pinned; never saved |
| A chat whose task model is a different server | goes to that server | | the shim never sees it |
| A filter or other plugin calling the model from the server side (`generate_chat_completion`) during a chat | the chat id (inherited) | empty unless the plugin sets `metadata.task` | **without a task marker: treated as the chat's own turn**, see below |

If your task model is the same llama-server, each reply is followed by two or three task calls. With two slots they take
whichever slot is not the active chat and evict whatever was there (saving it first if it was unsaved). With three or
more slots the churn is invisible.

### Plugins that call the model from the server side (found 2026-11-04)

Open WebUI merges the chat's `request.state.metadata` into every `generate_chat_completion` call made with that chat's
request object (`utils/chat.py`, since 0.5.12; checked on 0.13.0). So a filter's background call, for example a memory
extractor, reaches the shim with the chat's `{{CHAT_ID}}` and an empty `{{TASK}}`. The shim keys it as the chat itself,
runs the plugin's prompt in the chat's slot, and the chat's cached prompt is gone: measured with the user_memory filter,
a ~4.5k-token extraction prompt shared 3 tokens with the 25k-token chat, so the next turn re-prefilled 25,027 tokens
(134.9 s before the first token; a hit on the same chat costs ~9 s). The idle saver then wrote the plugin's state to SSD
under the chat's filename, so the SSD copy was lost too.

The fix belongs in the plugin: set `metadata.task` to a non-empty name on its payload. The chat's metadata has no `task`
key, so the name survives the merge, `{{TASK}}` renders it, and the shim keys the call `task:<name>:<chat id>` on an idle
slot, never saved, exactly like title and tag generation. The user_memory filter does this since v7.4 (valve
`extraction_task_name`, default `memory_extraction`). Use a fixed task name, not a random one: while its key is resident
the call reuses that slot (only the shared prefix, the plugin's system prompt, is reused; the rest is recomputed), whereas
a fresh key every call always needs a victim slot. If no empty or task slot is idle, that victim can be the chat's slot,
which then costs the chat a save and a restore on its next turn instead of a cache hit. The shim cannot tell these calls
apart on its own: everything it reads (headers, model) is identical to a real turn.

## LoRA model ids

If a LoRA proxy sits behind the shim, its extra model ids show up in the picker like any other, because the shim proxies
`/v1/models` untouched. Each id is keyed separately (`<model id>__<chat id>`), and with `MODEL_SLOTS` set each adapter has its
own slot, so a chat on an adapter id and a chat on the base id never clear each other's cache.

## Workspace models

A workspace model in Open WebUI sends its base model id upstream. The key is `<base model id>__<chat id>`. Switching the
base model of a workspace model therefore starts a fresh key and llama-server prefills from scratch, which is the only
correct behaviour since the saved state belongs to the other model.

## Deleting chats

Nothing happens at delete time. The next sweep (hourly by default) finds the chat gone and removes the file. Deleting all chats
removes all of that user's files on the next sweep. Archiving a chat keeps its file, since the chat still exists.
