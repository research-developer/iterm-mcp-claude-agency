#!/usr/bin/env python3

import iterm2

async def main(connection):
    app = await iterm2.async_get_app(connection)
    domain = iterm2.broadcast.BroadcastDomain()
    for tab in app.terminal_windows[0].tabs:
        domain.add_session(tab.sessions[0])
    await iterm2.async_set_broadcast_domains(connection, [domain])


iterm2.run_until_complete(main)
