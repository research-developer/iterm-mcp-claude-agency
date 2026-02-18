#!/usr/bin/env python3.7

import asyncio
import iterm2

async def main(connection):
    app = await iterm2.async_get_app(connection)

    async def watch_title(session_id):
        session = app.get_session_by_id(session_id)
        # When the session's "icon name" changes, update the tab title.
        # The icon name is set with OSC 0 and OSC 1.
        # e.g., ESC 0 ; title BEL
        async with iterm2.VariableMonitor(
                connection,
                iterm2.VariableScopes.SESSION,
                "terminalIconName",
                session_id) as mon:
            while True:
                new_value = await mon.async_get()
                # Note: it's unsafe to pass input from the session to async_set_title
                # because it's an interpolated string. Instead, set a user variable
                # (which can't do any computation) and then make the tab title
                # show its contents.
                await session.tab.async_set_variable("user.title", new_value)
                await session.tab.async_set_title("\\(user.title)")

    # Make every session monitor its title.
    async with iterm2.EachSessionOnceMonitor(app) as mon:
        while True:
            session_id = await mon.async_get()
            coro = watch_title(session_id)
            asyncio.create_task(coro)

iterm2.run_until_complete(main)
