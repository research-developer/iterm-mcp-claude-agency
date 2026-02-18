#!/usr/bin/env python3.7

import asyncio
import iterm2
# This script was created with the "basic" environment which does not support adding dependencies
# with pip.

async def main(connection):
    app = await iterm2.async_get_app(connection)
    async def stty(field, value, session_id):
        session = app.get_session_by_id(session_id)
        await session.async_send_text(f'stty {field} {value}\n')

    async def watch_cols(session_id):
        async with iterm2.VariableMonitor(
                connection,
                iterm2.VariableScopes.SESSION,
                "columns",
                session_id) as mon:
            while True:
                new_value = await mon.async_get()
                print("columns changed")
                await stty("columns", new_value, session_id)

    async def watch_rows(session_id):
        async with iterm2.VariableMonitor(
                connection,
                iterm2.VariableScopes.SESSION,
                "rows",
                session_id) as mon:
            while True:
                new_value = await mon.async_get()
                await stty("rows", new_value, session_id)

    print("start rows task")
    rows_task = iterm2.EachSessionOnceMonitor.async_foreach_session_create_task(app, watch_rows)
    print("start cols task")
    cols_task = iterm2.EachSessionOnceMonitor.async_foreach_session_create_task(app, watch_cols)
    await asyncio.gather(rows_task, cols_task)

# This instructs the script to run the "main" coroutine and to keep running even after it returns.
iterm2.run_forever(main)
