# Running on Windows, macOS and Linux

The shim is one Python file tree with no dependencies. Python 3.9 or newer, any platform. Line endings are normalised by
`.gitattributes`, so a checkout works unchanged everywhere.

Before anything else, llama-server needs `--slot-save-path <dir>` with an existing directory, and `.env` needs
`SLOT_SAVE_DIR` set to that same directory.

## Windows

Launch in a console: `start-shim.cmd`. It creates `SLOT_SAVE_DIR` if missing, then runs the shim in the foreground. Set
`SHIM_ENV` to use a different `.env`.

Stop: `stop-shim.cmd`. It calls `--flush` so every unsaved slot reaches SSD, then kills the listener. Run it before
stopping llama-server.

To run at logon or as a background task, use Task Scheduler:

```bat
schtasks /Create /TN "ssd-cache-shim" /SC ONLOGON /RL LIMITED ^
  /TR "\"C:\path\to\python.exe\" \"C:\path\to\llama.cpp-ssd-cache-shim\ssd_cache_shim.py\""
```

Scheduled cleanup instead of the in-process timer (set `RECONCILE_INTERVAL_MINUTES=0`):

```bat
schtasks /Create /TN "ssd-cache-shim-reconcile" /SC DAILY /ST 03:30 ^
  /TR "\"C:\path\to\python.exe\" \"C:\path\to\llama.cpp-ssd-cache-shim\ssd_cache_shim.py\" --reconcile"
```

Paths in `.env` are plain Windows paths, for example `SLOT_SAVE_DIR=C:\llama-slot-cache`. Ctrl-C in the console flushes
dirty slots before exit.

## macOS

Launch: `./start-shim.sh` (uses `python3` on `PATH`; set `PYTHON=/path/to/python3` to override). Stop: `./stop-shim.sh`.

As a launchd agent, edit the paths in `deploy/com.ssd-cache-shim.plist`, then:

```sh
mkdir -p logs
cp deploy/com.ssd-cache-shim.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ssd-cache-shim.plist
```

Stop with `launchctl bootout gui/$(id -u)/com.ssd-cache-shim`; launchd sends SIGTERM and the shim flushes before exiting. The plist
sets `ExitTimeOut` to 300 s for that flush (launchd's default is 20 s, after which it sends SIGKILL). Note that `bootout` returns
before the process is gone; a script that stops and immediately restarts should wait until `launchctl print` no longer finds the
label.
If llama-server is itself a launchd job, start the shim after it or just let the shim wait, which it does.

Scheduled cleanup: a second plist with a `StartCalendarInterval` running `ssd_cache_shim.py --reconcile`, or keep the
in-process timer.

If the slot directory is on an external volume, grant Python access to it the first time macOS asks; launchd attributes
file access to the executable it launched.

## Linux

Launch: `./start-shim.sh`. Stop: `./stop-shim.sh` (uses `lsof`, falling back to `ss`).

As a systemd service, edit `User` and the paths in `deploy/ssd-cache-shim.service`, then:

```sh
sudo cp deploy/ssd-cache-shim.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ssd-cache-shim
journalctl -u ssd-cache-shim -f
```

`TimeoutStopSec=300` in the unit gives a large final flush time to finish. For scheduled cleanup,
`deploy/ssd-cache-shim-reconcile.service` and `.timer` run `--reconcile` nightly at 03:30.

## Docker and containers

The shim is not containerised here because it needs to share a filesystem path with llama-server and gains nothing from
isolation. If you do put it in a container, mount `SLOT_SAVE_DIR` at the same path in both containers, or set
`SLOT_SAVE_DIR` to the shim's view of the directory and give llama-server its own view with `--slot-save-path`; only the
filenames travel between them.

Open WebUI in Docker reaches a shim on the host at `http://host.docker.internal:8090/v1` (Docker Desktop) or the host's
LAN address (Linux Docker without `host-gateway`).

## Fronting more than one llama-server

One shim per llama-server, each with its own `.env`, port and `SLOT_SAVE_DIR`. Two servers must not share a slot
directory: the index assumes one model stamp per directory.

## Chaining with other proxies

Another proxy can sit between the shim and llama-server, for example one that rewrites model ids or applies LoRA
adapters, as long as it forwards the request body (including the injected `id_slot`) and the request headers, and passes
`/props` and `/slots` through. If that proxy sets `id_slot` only when absent, the shim's pin wins. For a LoRA proxy, also
set `MODEL_SLOTS` so each adapter keeps its own slot; see [llama-server.md](llama-server.md).

What the other proxy must do for the shim to work through it:

| Requirement | Why |
| --- | --- |
| Forward the request body unchanged apart from its own rewrites, keeping `id_slot` | The shim's slot pin travels in the body. |
| Set its own `id_slot` only when the body has none | Otherwise it overrides the shim's pin and the slot table drifts from reality. |
| Forward request headers | The chat id and task headers are how the shim keys conversations. |
| Pass `GET /props`, `GET /slots` and `POST /slots/{id}?action=...` through | The shim reads the slot count and model from `/props` and saves and restores through `/slots`. |
| Keep the `Authorization` header or add llama-server's key itself | The shim adds its own key (`UPSTREAM_API_KEY`) only on its control calls. |

A plain pass-through proxy that only touches the body of chat requests satisfies all of this; `lora-shim.py` does.

Setup, with the shim's default port and llama-server moved behind it:

1. llama-server on a loopback port, with `--slot-save-path` and the adapters: `--lora a.gguf --lora b.gguf --lora-init-without-apply`.
2. The LoRA proxy on a second loopback port, upstream = llama-server. Its model ids are the ones clients will use.
3. The shim on the public port, `UPSTREAM_PORT` = the proxy's port, `UPSTREAM_API_KEY` = llama-server's key if it has one, and
   one `MODEL_SLOTS` entry per adapter id, each naming a slot the base id will never use (see
   [llama-server.md](llama-server.md#lora-adapters) for why). With three slots and one adapter: `MODEL_SLOTS=my-model-lora:2`.
4. Start in that order, or start all three and let the shim wait. Stop in the reverse order: shim, proxy, llama-server, so the
   shim's final flush still reaches llama-server. Give the shim's service a long stop timeout (`ExitTimeOut` in the launchd
   plist, `TimeoutStopSec` in the systemd unit; both ship with 300 s), since a full slot can be gigabytes.
5. Check `ssd_cache_shim.py --status`: the `reserved` field lists the adapter ids and their slots, and `/v1/models` through the
   shim lists the base and every adapter id, since the shim proxies it untouched.

If the shim is added to an existing chain, give it the port clients already use and move the proxy to a loopback port behind it:
clients keep their URL and key, and only the Open WebUI headers are new. A worked example with timings is in
[case-study-m1-ultra.md](case-study-m1-ultra.md).
