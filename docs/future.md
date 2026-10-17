# Future enhancements

Recorded from the first deployment on 2026-10-17. None of these are implemented; the shim is deliberately being left
unchanged while the Windows and Mac deployments are tested, so that what is observed can be attributed to what exists.

## Do not persist one-shot conversations

**Finding.** Open WebUI subagents (the `delegate_task` built-in tool), timers and note runs each create a hidden internal
chat with its own chat id. The shim treats them like any chat: pinned to a slot while they run, saved to SSD when evicted
or idle. Watching a three-subagent delegation on the two-slot laptop showed:

- each subagent occupies a slot for its whole run and makes several model calls within that run (its own tool-call
  loop), so keeping it on a GPU slot while it runs is right;
- once the run ends, nothing ever sends another message to that chat. Its file on SSD (several hundred MB on a 9B model
  with a workspace prompt) is never read and only leaves through the 30-day age-out;
- the lead chat was saved once when the subagents needed its slot and would be restored when it resumed, which is the
  behaviour wanted.

The same holds for Hermes subagents: one-shot sessions nothing resends to. Hermes heartbeat ticks are the opposite case:
identical prompts hash to the same key every tick, so one file serves as a warm prefix and is worth keeping.

**Why the shim cannot tell today.** Open WebUI's per-connection header templates expose the chat id, message id, task
name, model id and user fields, but no internal flag. Hermes sends no identifying header at all (unverified; it falls
through to the hash key).

**Options.**

1. *Ask Open WebUI on first sight.* When a chat id arrives that the shim has not seen, call `GET /api/v1/chats/{id}` once
   and read `meta.internal`. Internal chats are marked non-persistent for the life of the slot and the answer is cached.
   One API call per new chat, needs `OPENWEBUI_API_KEY`, nothing to configure in Open WebUI. Stops the write rather than
   undoing it. Preferred.
2. *Clean up in the sweep.* The reconcile already fetches every stored chat; read the same marker there and delete
   internal files. No change to the request path; the file exists for up to one sweep interval. Backstop for option 1.
3. *A shorter age for hash-keyed files*, or a separate `MAX_AGE_DAYS_FALLBACK`, so Hermes subagent files leave sooner
   without touching Open WebUI-keyed ones.
4. *A client header for non-persistent sessions*, for example `X-Shim-Persist: false`, honoured if present. Useful for
   any client that knows which of its sessions are throwaway, if Hermes can send per-role headers.

## Other candidates

- **Save and restore timings in `--status`.** The shim logs them per operation; a running total per slot would make the
  case-study numbers collectable without reading the console.
- **Log to a file.** The launchers rely on the console. A `LOG_FILE` key would let `--status` style tooling read history.
- **Prefill-versus-restore benchmark.** A `--bench` mode that restarts nothing but reports, for a given chat id, the
  restore time and the token count it saved, for documenting deployments like the T600 case study.
- **Metrics endpoint.** Counts of saves, restores, evictions, bytes written and read, in the Prometheus text format
  llama-server already uses for `/metrics`.
