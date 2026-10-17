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

Stop with `launchctl bootout gui/$(id -u)/com.ssd-cache-shim`; launchd sends SIGTERM and the shim flushes before exiting.
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
adapters, as long as it forwards the request body (including the injected `id_slot`) and passes `/props` and `/slots`
through. If that proxy sets `id_slot` only when absent, the shim's pin wins.
