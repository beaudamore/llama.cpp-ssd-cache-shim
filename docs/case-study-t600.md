# Case study: a 4 GB laptop GPU

The shim was built and first deployed on this machine on 2026-10-17. The numbers below are from that session's index
and status output, not estimates.

## The machine

| | |
| --- | --- |
| Laptop | Windows 11 Pro, NVIDIA T600, 4 GB VRAM, Turing (compute 7.5) |
| llama-server | local CUDA 13.4 build of llama.cpp, master from 2026-10-17 |
| Model | Qwen3.5-9B Q4_K_M (5.7 GB) plus the f16 vision projector (0.9 GB) |
| Slots | 2, 64k context each (`-np 2 -c 131072`), KV cache q8_0 |
| Checkpoints | every 2048 tokens, 16 per slot |
| Client | Open WebUI 0.12.3 in Docker, three workspace models on the same base, each with a long system prompt |
| SSD directory | `C:\docker-data\llama-slot-cache` on the internal NVMe |

The model does not fit in VRAM. llama-server's memory fitter splits layers between the GPU and the CPU at startup, so
prefill runs at a fraction of what a dedicated GPU would manage. Re-prefilling a workspace prompt is the single most
expensive thing this laptop does in normal use, which is what made it the right test case.

## The problem on this hardware

Every workspace model carries a system prompt plus knowledge of about 11,000 tokens. With two slots, the third
conversation opened in a session evicts the first. Before the shim, llama-server's RAM prompt cache held those evicted
states, but only until llama-server restarted, and a laptop that gets closed, reopened and rebooted restarts llama-server
often. Each restart meant every conversation paid the 11,000-token prefill again on its next message.

## Session log

Four conversations were opened in turn through the shim, then two of the earlier ones were revisited.

| Time | Action | Shim's view afterwards |
| --- | --- | --- |
| 15:23 | shim smoke test (curl, 17-token prompt) | slot 0: smoke test |
| 15:2x | Open WebUI chat with Jake | slot 0: Jake, slot 1: Jake's tags task; smoke test saved to SSD (103 MB) |
| 15:29 | chat with Father Elias | Jake evicted, saved (404 MB); slot 0: Elias, slot 1: Elias's tags task |
| 15:38 | second message to Jake | Jake restored, continued, re-saved (487 MB) |
| 15:39 | a second, separate Jake chat | its own key and file (386 MB) |
| 15:47 | chat with Pastor Ezekiel | slot 1: Ezekiel |
| 15:47:06 | message to the first Jake chat | **restored from SSD** into slot 0; Ezekiel evicted and saved |
| 15:47:52 | message to Father Elias | **restored from SSD** into slot 1 |

Index state at the end:

| Conversation | Tokens in state | File | Last written | Last used |
| --- | --- | --- | --- | --- |
| Jake, first chat | 11,151 | 487 MB | 15:38:47 | 15:47:06 |
| Jake, second chat | 11,116 | 386 MB | 15:39:51 | 15:39:51 |
| Father Elias | 11,565 | 393 MB | 15:36:24 | 15:47:52 |
| Pastor Ezekiel | 11,976 | 400 MB | 15:47:30 | 15:47:30 |
| smoke test | 128 | 103 MB | 15:23:01 | 15:23:01 |

A last-used time later than the last-written time means the file was read back without being rewritten, which only a
restore does. Both revisits at 15:47 were restores of about 11,000 tokens each, and the user's description of both
replies was that they came back fast.

Total SSD use after four conversations: 1.9 GB.

## What the numbers say

- **File size is 35 to 50 KB per token** in this configuration (q8_0 KV on a 9B model with checkpoints every 2048
  tokens). The spread comes from the checkpoints: a conversation that has been edited or continued several times carries
  more of them. Jake's first chat grew from 404 MB to 589 MB across three messages while gaining fewer than a hundred
  tokens. A 64k-context slot, full, would be roughly 2.5 to 3 GB. The 50 GB default budget holds around a hundred typical
  workspace conversations or twenty full ones.
- **Even an empty-looking chat is 100 MB.** The smoke test had 128 tokens of state and still cost 103 MB, because the
  file carries the slot's fixed structures. Short conversations are not worth persisting for speed, but they are cheap
  enough that the shim does not bother to special-case them.
- **Two slots means constant churn.** Every reply from Open WebUI is followed by a tags or title task, which takes the
  second slot. With the user's chat in one slot and the task in the other, any new conversation evicts the user's chat.
  The shim's victim order (empty, then task, then clean, then least recently used) keeps the churn from ever landing on
  the active chat, but a third slot would remove it entirely. On 4 GB of VRAM that slot is not available.

## A subagent run

Later the same day, with Open WebUI's subagents enabled, one chat asked the lead model to delegate three summaries and
compare them. The shim's slot table was polled every two seconds during the run.

| Time | Event |
| --- | --- |
| 16:09 | lead chat (bf53faaf) generating in slot 0; its title task in slot 1 |
| 16:11 | lead finishes its first turn; subagent 1 (226d6ef8) takes slot 1 |
| 16:14 | lead idle-saved to SSD (388 MB) |
| 16:17 | subagent 2 (6c62cef8) takes slot 0 from the lead; no save needed, the lead was clean |
| 16:20 | subagent 1 finishes and is idle-saved (381 MB) |
| 16:23 | subagent 3 (b1b8d3f6) takes slot 1 |
| 16:25 | subagent 2 idle-saved (379 MB) |
| 16:27:07 | lead **restored from SSD** into slot 0 to write the comparison |
| 16:29 | subagent 3 idle-saved (384 MB) |
| 16:30 | lead done; follow-up and tags tasks run in slot 1 |

Observations:

- The lead model called `delegate_task` one at a time, so at most one subagent ran beside the lead and the two slots
  were never oversubscribed. Each subagent made several model calls during its run, its own tool loop, and stayed on its
  slot for all of them.
- The lead's eviction cost nothing because the idle save had already run, and its return was a restore of 11,268 tokens.
  This is the shim doing what it is for.
- The three subagent files, 1.1 GB together, will never be read. Nothing ever sends another message to a finished
  subagent chat. This is the waste described in [future.md](future.md).

## Changes made because of this deployment

- `--cache-ram 0` on llama-server. The host-RAM prompt cache was duplicating every save the shim made, and its one
  remaining benefit, surviving a shim restart, was not worth 8 GB on a laptop that also runs a TTS server and Docker. See
  [llama-server.md](llama-server.md).
- The per-connection headers in Open WebUI rather than the global `ENABLE_FORWARD_USER_INFO_HEADERS`, because only the
  per-connection form can send the task header, and without it the tags task would have been pinned into Jake's slot and
  mistaken for a turn. See [openwebui.md](openwebui.md).

## Not yet measured

The before-and-after comparison that matters most on this machine is the first message to an existing conversation
after a llama-server restart: a full 11,000-token prefill without the shim against a restore with it. The restores above
were observed as fast, but the prefill side was not timed in the same session. When it is, this document should get the
two numbers side by side.
