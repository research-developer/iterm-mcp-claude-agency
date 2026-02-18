#!/usr/bin/env python3

import asyncio
import iterm2

async def main(connection):
    app = await iterm2.async_get_app(connection)

    # Create four split panes and make the bottom left one active.
    bottomLeft = app.current_terminal_window.current_tab.current_session
    bottomRight = await bottomLeft.async_split_pane(vertical=True)
    topLeft = await bottomLeft.async_split_pane(vertical=False, before=True)
    topRight = await bottomRight.async_split_pane(vertical=False, before=True)
    await bottomLeft.async_activate()
    broadcast_to = [ topLeft, bottomLeft, topRight, bottomRight ]

    async def async_handle_keystroke(keystroke):
        if keystroke.keycode == iterm2.Keycode.ESCAPE:
            # User pressed escape. Terminate script.
            return True
        for session in broadcast_to:
            await session.async_send_text(keystroke.characters)
        return False

    # Construct a pattern that matches all keystrokes except those with a Command modifier.
    # This prevents iTerm2 from handling them when the bottomLeft session has keyboard focus.
    pattern = iterm2.KeystrokePattern()
    pattern.keycodes = [keycode for keycode in iterm2.Keycode]
    pattern.forbidden_modifiers = [iterm2.Modifier.COMMAND]

    future = asyncio.Future()

    # Swallow all keystrokes matching the pattern
    async def filter_all_keystrokes():
      async with iterm2.KeystrokeFilter(connection, [pattern], bottomLeft.session_id) as mon:
          await asyncio.wait([future])

    task = asyncio.create_task(filter_all_keystrokes())


    # This will block until async_handle_keystroke returns True.
    async with iterm2.KeystrokeMonitor(connection, bottomLeft.session_id) as mon:
        done = False
        while not done:
            keystroke = await mon.async_get()
            done = await async_handle_keystroke(keystroke)
            if done:
                break
        future.set_result(True)

    await task

iterm2.run_until_complete(main)
