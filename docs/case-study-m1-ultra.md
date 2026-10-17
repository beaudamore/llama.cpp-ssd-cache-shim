# Case study: Mac Studio M1 Ultra, 27B model, three 256k slots, LoRA lane

Second deployment, 2026-10-17. Numbers below are from the shim's own log and llama-server's log on the day.

## Setup

| | |
| --- | --- |
| Machine | Mac Studio M1 Ultra, 128 GB unified memory, internal SSD for the slot files |
| llama-server | build b11698, Qwen3.8-27B Q4_0 with mmproj and one LoRA (`--lora ... --lora-init-without-apply`), `-np 3 -c 786432` (3 x 256k), `-ctk q8_0 -ctv q8_0`, MTP draft 3, `--checkpoint-min-step 2048 --ctx-checkpoints 16`, `--cache-ram 0`, `--slot-save-path ~/llama-slot-cache` |
| Chain | clients -> this shim :8180 -> `lora-shim.py` :8191 (loopback, one model id per adapter) -> llama-server :8190 (loopback) |
| Shim | `MODEL_SLOTS=qwen3.8-27b-biblical:2` (LoRA id only on slot 2, base id on slots 0 and 1), `MAX_BYTES=100G`, otherwise defaults |
| Service | three launchd jobs run by `llamactl` in local-llm-config; `llmctl stop llama` stops the shim first so its final flush reaches llama-server |
| Clients | Open WebUI (two headers on the connection), Hermes and OpenClaw by hash key; every client kept its URL and key |

Resident size of llama-server with these flags: 49.3 GB (weights 14.9, KV 26.1, draft KV 3.0, recurrent state 1.8, compute 3.5).
The 16 GB host-RAM prompt cache it ran with before is gone.

## Save and restore times

Workspace prompts here are about 12k tokens (a persona plus tools). Files are llama-server's full slot state: attention KV, recurrent
state, token list and the context checkpoints.

| Tokens in slot | File | Save | Restore |
| --- | --- | --- | --- |
| 22 | 300 MiB | 250 to 310 ms | |
| 3,025 | 861 MiB | 580 ms | 97 ms |
| 12,446 | 1,211 MiB | 1,009 ms | 256 ms |
| 13,026 | 1,232 MiB | 991 ms | |

A near-empty slot already writes about 300 MiB (recurrent state and a checkpoint), so files do not scale linearly from zero.

## Restore versus prefill

A chat with a 12,446-token prompt was saved by the idle timer, pushed off the GPU by two other chats, then resumed:

```text
17:24:47 slot 0 -> SSD qwen3.8-27b__cd3fd94b-...bin (12446 tokens, 1211 MiB, 1009 ms)
17:30:48 SSD qwen3.8-27b__cd3fd94b-...bin -> slot 1 (12446 tokens, 256 ms)
llama-server: new prompt, task.n_tokens = 12476 ... cached n_tokens = 12446, memory_seq_rm [12446, end)
```

The resumed turn processed 30 new tokens. A cold prefill of a prompt the same size on this machine took 60 s at about 200 tokens
per second, so the return cost a quarter of a second instead of a minute.

## A Hermes agent run, hash-keyed

Hermes sends no chat id, so its sessions are keyed by the hash of system prompt plus first user turn. Hermes keeps its system prompt
byte-stable for the day on purpose (the date line is anchored to the session start), and the same key held across every call of a
six-call session. One research task (web search, skill read, two page reads, a note written, the answer), all pinned to one slot:

| Call | Prompt | Reused | New tokens |
| --- | --- | --- | --- |
| 1 | 15,343 | 0 | 15,343 |
| 2 | 19,089 | 15,403 | 3,686 |
| 3 | 26,555 | 19,085 | 7,470 |
| 4 | 26,950 | 26,551 | 399 |
| 5 | 27,372 | 27,273 | 99 |

Within the run the reuse is llama-server's own prefix cache; the shim only guarantees the slot stays the session's. The shim's own
contribution came next: three Open WebUI chats pushed the session off the GPU (saved: 27,552 tokens, 2,670 MiB, 2.26 s; each of the
three chats came back from its own file in 0.3 to 0.4 s and reused all but about 30 tokens), then a one-line follow-up to Hermes:

```text
17:50:30 SSD qwen3.8-27b__h-187e31ac827775f56ba1.bin -> slot 1 (27552 tokens, 612 ms)
llama-server: task.n_tokens = 27586 ... cached n_tokens = 27552
```

34 new tokens processed, reply complete 8 s after the request. A cold prefill of 27.5k tokens here is about 2.5 minutes.

What the shim does not do for agents: short one-shot sessions (subagents) each prefill the shared system prompt from scratch and
leave a file nothing reads again. Whole-conversation files cannot share a common prefix the way a block-level cache (oMLX) does.

## The LoRA rule

llama-server clears a slot's cache when the adapter set on that slot differs from the previous request's
(`launch_slot_with_task` -> `lora_should_clear_cache`). With a LoRA proxy mapping model ids to adapters, a base chat restored into a
slot that last ran the adapter, or the reverse, would be cleared right after the restore. `MODEL_SLOTS` was added for this deployment
so each model id keeps its own slot(s); the log showed no cache clears from adapter changes after the first request on each slot.

## Things learned

- `launchctl bootout` returns before the job is gone. A `start` issued two seconds later found the old label still present and skipped
  it, which left llama-server down while the shim waited politely. `llmctl stop` now waits until launchd has dropped the label.
- With two base slots and Open WebUI's task calls after every reply, a task often takes the other base slot, so a second chat gets
  saved and restored more than the slot count suggests. The saves are about one second here, so it is tolerable; a fourth slot would
  make it invisible.
- Memory after the restart looked higher than before it in `memory_pressure`, but llama-server's resident size was within 1.5 GB of
  its documented baseline; the rest was Docker, idle MLX services in the compressor, and desktop apps. Measure the breakdown
  (`vm_stat`, `top -o mem`) before attributing it to the shim.
