#!/usr/bin/env python3.7

import iterm2

pids = []

async def main(connection):
    @iterm2.RPC
    async def oneshot_alert(
            title,
            subtitle,
            pid=iterm2.Reference("jobPid")):
        global pids
        if pid in pids:
            return
        pids.append(pid)
        alert = iterm2.Alert(title, subtitle)
        await alert.async_run(connection)
    await oneshot_alert.async_register(connection)

iterm2.run_forever(main)
