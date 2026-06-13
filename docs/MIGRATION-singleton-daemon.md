# Post-merge migration: singleton daemon

After this branch merges to main, run these once (requires the merged code on main and the iterm-mcp package importable):

## 0. Pre-migration: this machine's specific landmines

- **A launchd agent is running the OLD daemon and will fight you.**
  `~/Library/LaunchAgents/com.iterm-mcp.daemon.plist` (label `com.iterm-mcp.daemon`,
  RunAtLoad + KeepAlive) runs the pre-migration `run_server.py --transport
  streamable-http --port 12345` and respawns it when killed. Unload it first:
  ```bash
  launchctl unload ~/Library/LaunchAgents/com.iterm-mcp.daemon.plist
  rm ~/Library/LaunchAgents/com.iterm-mcp.daemon.plist   # or rewrite it to run `iterm-mcp daemon`
  ```
  (If you want a boot-started daemon afterwards, recreate the plist around
  `iterm-mcp daemon` — but the shim auto-start makes that optional.)

- **The editable install is dangling.** The interpreter's
  `__editable__.iterm_mcp-0.1.0.pth` points at a deleted worktree, so
  `python -m iterm_mcpy` and the `iterm-mcp` script fail outside a checkout.
  Reinstall from the merged main checkout first:
  ```bash
  cd /Users/psentro/research-developer/iterm-mcp-claude-agency && pip install -e .
  ```

## 1. Migrate client configs

- Claude Code: remove the broken HTTP-as-stdio entry if present, then re-add:
  ```bash
  claude mcp remove iterm-mcp   # the stale "command": "http://127.0.0.1:12345/mcp" entry
  iterm-mcp install --code      # prints the exact `claude mcp add` command; run it
  ```
- Claude Desktop:
  ```bash
  iterm-mcp install --desktop   # rewrites the iterm entry to `<python> -m iterm_mcpy`
  ```
  then restart Claude Desktop. (Until then, the old run_server.py path still works — it now routes through the shim.)

## 2. Verify the singleton end-to-end

1. `iterm-mcp stop` (clean slate), then open a Claude Code session and call the `sessions` tool with `op="GET"` — the daemon auto-starts; `iterm-mcp status` shows one pid.
2. Open Claude Desktop, use the iterm server — `iterm-mcp status` shows the SAME pid; `ps aux | grep '[i]term_mcpy' ` shows 1 daemon + 1 shim per client.
3. Register an agent from Claude Code (`agents op="CREATE"`), then list agents from Claude Desktop — the agent must be visible (shared registry: the point of the architecture).
4. Quit both clients; the daemon stays up (by design); `iterm-mcp stop` shuts it down and clears ~/.iterm-mcp/daemon.json.

## Known limitations (tracked for follow-up)

- If the daemon dies while a client's stdin is idle, that client's shim notices only on its next message (documented in `iterm_mcpy/shim.py:_pump`).
- A daemon holding a dead iTerm2 WebSocket fails tool calls until restarted (`iterm-mcp stop` + next client auto-respawns). Reconnect supervisor is a separate planned work item.
- Editable installs always report version 0.1.0, so the shim's version-mismatch restart won't fire between dev iterations — use `iterm-mcp stop` after changing daemon code.
- The registry's implicit *active session* is shared across ALL clients (last writer wins); pass explicit session/agent/team targets in multi-client setups.
