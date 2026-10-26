# Running the iTerm MCP server

This is the supported setup on this machine. It runs exactly **one** server
process and leaves nothing behind.

```
launchd  (label: com.iterm-mcp.daemon, ~/Library/LaunchAgents/com.iterm-mcp.daemon.plist)
  │  starts at login, restarts on crash
  ▼
.venv/bin/python -m iterm_mcpy daemon --port 12345      ← the ONLY iterm-mcp process
  ├── iTerm2 Python API (one websocket, shared by every client)
  └── http://127.0.0.1:12345/mcp   ← every Claude Code session connects here over HTTP
      http://127.0.0.1:12345/health   (no child processes are spawned per session)
```

All clients share one iTerm2 connection and one agent/team registry, which is
why there is a single daemon.

## Rules that keep it orphan-free

1. **Only launchd starts the daemon.** The plist runs the daemon directly. It
   does not run the shim (`run_server.py` or bare `iterm-mcp`). A shim spawns a
   *detached* daemon that launchd can't track.
2. **Claude Code connects over HTTP.** An `http` MCP entry spawns no process.
   A stdio entry spawns a shim for every session, and shims auto-spawn
   daemons and kill ones they consider stale.
3. **The port is fixed.** `--port 12345` disables the 12340-12349 fallback. If
   12345 is taken, the daemon fails loudly instead of quietly starting a second
   copy on another port.
4. **Restart happens only after a crash, and is throttled.** `KeepAlive.SuccessfulExit=false`
   plus `ThrottleInterval=30` prevent a broken environment from respawning
   every 10 s forever.
5. **The daemon has its own venv** (`.venv/`, gitignored). Installs into the
   shared anaconda base env can't break it.

## One-time setup

```bash
cd ~/research-developer/iterm-mcp-claude-agency
/opt/anaconda3/bin/python3 -m venv .venv      # same interpreter binary → no new macOS permission prompts
.venv/bin/pip install -e .                    # pyproject pins mcp<2
./install-daemon.sh                           # refuses to install if the venv can't import the server
claude mcp add --scope user --transport http iterm http://127.0.0.1:12345/mcp
```

`install-daemon.sh` is safe to re-run. It regenerates the plist from
`com.iterm-mcp.daemon.plist.template` and reloads it.

## Day-to-day

| Task | Command |
|---|---|
| Is it up? | `.venv/bin/iterm-mcp status` (or `curl -s 127.0.0.1:12345/health`) |
| Restart (after `git pull` / new commits) | `launchctl kickstart -k gui/$(id -u)/com.iterm-mcp.daemon` |
| Stop until next login | `launchctl unload ~/Library/LaunchAgents/com.iterm-mcp.daemon.plist` |
| Start again | `launchctl load ~/Library/LaunchAgents/com.iterm-mcp.daemon.plist` |
| Logs | `tail -f ~/.iterm-mcp/daemon.log` |
| launchd's view | `launchctl print gui/$(id -u)/com.iterm-mcp.daemon \| grep -E 'state\|pid\|runs\|last exit'` |
| Reconnect Claude Code after a restart | `/mcp` → select `iterm` → Reconnect |

Things to know:

- **The daemon does not pick up code changes on its own.** It keeps running the
  code it started with, and HTTP clients don't do a version handshake (only the
  stdio shim does). Restart it after pulling or committing.
- **`iterm-mcp stop` means stop, not restart.** It sends SIGTERM and the daemon
  exits cleanly. launchd leaves a clean exit alone, so the daemon stays down
  until `kickstart` or next login. Use `kickstart -k` to restart.
- If iTerm2 itself restarts, the daemon is left holding a dead websocket and tool
  calls fail. Fix: `kickstart -k`.

## Orphan audit

```bash
pgrep -fl 'iterm_mcpy|run_server'            # expect exactly ONE line: the launchd daemon
lsof -nP -iTCP:12340-12349 -sTCP:LISTEN      # expect only :12345
```

Anything else is left over from a stdio config or a manual run. Kill it and
`kickstart -k` the daemon.

## Don'ts

Each of these has caused orphans or an outage on this machine.

- **Don't add stdio iTerm entries for Claude Code** (`iterm-mcp`,
  `python -m iterm_mcpy`, `run_server.py`). Use the single user-scope `http` entry.
- **Don't point launchd at `run_server.py`.** It is a deprecated wrapper that
  now runs the shim and ignores its `--transport/--port` arguments.
- **Don't run the daemon from the anaconda base env.** On 2026-09-19 an
  unrelated install upgraded `mcp` to 2.x there, and the daemon died on import.
- **Don't point a config at a different checkout.** Its git commit count
  differs, so its shim considers the shared daemon stale, kills it, and spawns
  its own. The other checkout's shims then do the same thing back.
- **The repo's `.mcp.json`** declares a stdio `iTerm` server so the plugin works
  for other users. In this checkout, a local-scope `http` entry named `iTerm`
  overrides it. Don't approve the project server if Claude Code asks.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ECONNREFUSED 127.0.0.1:12345` | Daemon isn't running | `launchctl print …` (above), then `tail ~/.iterm-mcp/daemon.log` |
| Log: `No module named 'mcp.server.fastmcp'` | `mcp` 2.x in the daemon's env | `.venv/bin/pip install -e .` (restores `mcp<2`), then `kickstart -k` |
| launchd `runs` keeps climbing, `last exit code = 1` | Crash loop | Read `daemon.log`; once fixed, `./install-daemon.sh` (its preflight import check confirms the env) |
| Log: `address already in use` | Another process holds 12345 | `lsof -nP -iTCP:12345 -sTCP:LISTEN`, kill it, `kickstart -k` |
| Tools return iTerm2 connection errors | Stale iTerm2 websocket | `kickstart -k` |
| Daemon is healthy but Claude Code says "Failed to connect" | Claude Code doesn't retry by itself | `/mcp` → Reconnect |

## Claude Desktop (optional)

Desktop's config file only accepts stdio servers, so Desktop needs the shim:
`.venv/bin/iterm-mcp install --desktop`. The shim finds the launchd daemon
healthy and only relays messages. That adds one extra process per Desktop
instance, and it exits when Desktop quits.

There is one catch. After a new commit, the shim considers the daemon stale,
SIGTERMs it, and spawns its own replacement, which launchd doesn't manage. If
the old daemon takes more than 5 s to free port 12345, the replacement lands
on 12340 and you end up with two. Run `kickstart -k` after pulling code,
before you open Desktop.

## Other modes (for reference)

| Command | What it is | When to use it |
|---|---|---|
| `iterm-mcp daemon` | The HTTP daemon in the foreground | What launchd runs; manual debugging |
| `iterm-mcp` (no args) | stdio↔HTTP shim; auto-spawns a daemon | Only for stdio-only clients (Claude Desktop) |
| `iterm-mcp stdio` | Single-process stdio server, no daemon | Isolated debugging only |
| `iterm-mcp status` / `stop` | Inspect, or SIGTERM, whatever `~/.iterm-mcp/daemon.json` points at | Any time |

## Incident: October 2026 outage

Symptom: `/mcp` reported `ECONNREFUSED at http://127.0.0.1:12345`.

Two independent faults combined:

1. On 2026-09-19, `mcp` 2.2.0 was installed into `/opt/anaconda3`, the env the
   daemon ran from. 2.x removed `mcp.server.fastmcp`, so every daemon start
   crashed on import. `pyproject.toml` had no upper bound on `mcp`.
2. The launchd plist predated the shim refactor (#129). It still ran
   `run_server.py`, which had quietly become the shim. So launchd was
   supervising a shim, and that shim spawned a detached daemon launchd couldn't
   track. With the daemon crashing, every shim respawn spawned another doomed
   daemon. The result was a 10-second crash loop that logged more than 8,000
   tracebacks (about 46 MB). Had the daemon started, it would have run as an
   untracked orphan.

The fix was the setup described above: a dedicated venv with `mcp<2`, a plist
that runs the daemon directly with a fixed port and throttled restarts, a
preflight import check in `install-daemon.sh`, and a cleanup of `~/.claude.json`
that removed the stdio, broken, and duplicate iTerm entries (backup at
`~/.claude.json.bak-iterm-*`).
