#!/usr/bin/env python3.7
import iterm2

async def main(connection):
    app = await iterm2.async_get_app(connection)

    async def move_current_tab_by_n_windows(delta):
        tab_to_move = app.current_terminal_window.current_tab
        window_with_tab_to_move = app.get_window_for_tab(tab_to_move.tab_id)
        i = app.terminal_windows.index(window_with_tab_to_move)
        n = len(app.terminal_windows)
        j = (i + delta) % n
        if i == j:
            return
        window = app.terminal_windows[j]
        await window.async_set_tabs(window.tabs + [tab_to_move])

    @iterm2.RPC
    async def move_current_tab_to_next_window():
        await move_current_tab_by_n_windows(1)
    await move_current_tab_to_next_window.async_register(connection)

    @iterm2.RPC
    async def move_current_tab_to_previous_window():
        n = len(app.terminal_windows)
        if n > 0:
            await move_current_tab_by_n_windows(n - 1)
    await move_current_tab_to_previous_window.async_register(connection)

iterm2.run_forever(main)
