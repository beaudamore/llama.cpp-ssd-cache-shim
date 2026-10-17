# llama-server settings

The shim works with any llama-server configuration that has `--slot-save-path`, but a few flags interact with it and are
worth setting deliberately.

## Required

| Flag | Why |
| --- | --- |
| `--slot-save-path DIR` | Enables the save and restore endpoints. The directory must exist at startup and must be the same directory as the shim's `SLOT_SAVE_DIR`. llama-server appends a bare filename to this path; the shim never sends a path of its own. |

## Recommended

| Flag | Recommendation | Why |
| --- | --- | --- |
| `--cache-ram` | `0` when the shim is in front | llama-server's host-RAM prompt cache parks evicted slot states in RAM (8192 MiB by default) and reloads them when a prompt matches. With the shim, every eviction is already written to SSD and every return is restored from SSD before the request reaches llama-server, so the RAM copy is written and almost never read. Setting it to `0` gives that RAM back. What you lose: a fast return for task and temporary-chat states, which the shim never persists, and the window after a shim restart before the shim knows what the slots hold. Both are one re-prefill each. |
| `-np N` (slots) | as many as VRAM allows | Each slot is one conversation on the GPU. Two slots means every side task evicts a chat. Three or more means the task churn becomes invisible. The shim adapts to whatever count `/props` reports. |
| `-c N` (context) | the same every start | `-c` is the total across slots; each slot gets `-c / -np`. The shim stamps every saved file with the per-slot context size and deletes all files when it changes. |
| `--checkpoint-min-step N` and `--ctx-checkpoints N` | keep whatever you use for mid-prompt edits | Checkpoints are part of the slot state and travel with it into the saved file and back. They are what lets an edited turn in the middle of a long chat resume from a nearby point on recurrent and SWA models. Unrelated to the shim, but not lost by it. |
| `--api-key KEY` | optional | If set, put the same key in the shim's `UPSTREAM_API_KEY` so the shim's own `/props` and `/slots` calls are accepted. Client requests carry their own `Authorization` header through the shim untouched. |

## Flags that change the saved files

Saved states are only valid for the configuration that wrote them.

| Change | Effect |
| --- | --- |
| Different model file | Stamp changes; every file is deleted at the next shim start. |
| Different `-c` or `-np` | Same. The per-slot context size and slot count are part of the stamp. |
| Different `-ctk` / `-ctv` (KV cache type) | Not in the stamp, because llama-server does not report it in `/props`. llama-server rejects the restore because the stored tensor types do not match, and the shim deletes that file and re-prefills. One slow turn per chat, no corruption. Clear the directory first if you prefer. |
| Adding or removing `--mmproj` | Not in the stamp. Text-only states restore fine either way. States that contain image chunks need the projector present. |

## Flags that do not apply

| Flag | Note |
| --- | --- |
| `--cache-reuse N` | Reuses matching KV chunks after an edit point by shifting their positions. llama-server disables it on recurrent (hybrid) models, where there are no positions to shift, and whenever an `--mmproj` is loaded. Checkpoints are the mechanism for those models. |
| `--slots` endpoint toggle | `/slots` is on by default. The shim reads it only for status; the save and restore actions are governed by `--slot-save-path` alone. |

## How the layers stack

| Layer | Where | Survives llama-server restart | Who manages it |
| --- | --- | --- | --- |
| Prefix cache | slot, on the GPU | no | llama-server, always on |
| Checkpoints | slot, on the GPU | no (but saved inside the slot file) | llama-server, via the checkpoint flags |
| RAM prompt cache | host RAM | no | llama-server, `--cache-ram` |
| SSD slot files | disk | yes | the shim |

With the shim the top layer is the prefix cache on the pinned slot, and the bottom layer is the SSD. The RAM layer in between is optional.

## Example

Windows, a 9B model on a 4 GB GPU, two slots of 64k each:

```bat
llama-server.exe -m Qwen3.5-9B-Q4_K_M.gguf --mmproj mmproj-Qwen3.5-9B-f16.gguf -ngl all -np 2 -c 131072 ^
  -fa on -ctk q8_0 -ctv q8_0 --checkpoint-min-step 2048 --ctx-checkpoints 16 ^
  --cache-ram 0 --slot-save-path C:\docker-data\llama-slot-cache --jinja --host 0.0.0.0 --port 8080 --alias qwen3.5-9b
```

With this setup an 11,000-token workspace prompt saves as a file of roughly 400 to 500 MB and restores in well under a
second from SSD.
