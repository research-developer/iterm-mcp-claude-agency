#!/usr/bin/env python3.7

import asyncio
import iterm2
import os

def FormatBytes(num, suffix='B'):
    for unit in ['','Ki','Mi','Gi','Ti','Pi','Ei','Zi']:
        if abs(num) < 1024.0:
            return "%3.1f %s%s" % (num, unit, suffix)
        num /= 1024.0
    return "%.1f %s%s" % (num, 'Yi', suffix)

def GetFreeSpace():
    statvfs = os.statvfs('/')
    return FormatBytes(statvfs.f_frsize * statvfs.f_bavail)

task = None

async def main(connection):
    app = await iterm2.async_get_app(connection)

    component = iterm2.StatusBarComponent(
        short_description="Free Space",
        detailed_description="Shows the amount of free disk space",
        knobs=[],
        exemplar="💾 " + FormatBytes(1024 * 1024 * 1024 * 1024 * 2.1),
        update_cadence=None,
        identifier="com.iterm2.example.disk-space")

    async def poll():
        while True:
            space = GetFreeSpace()
            print("Measure disk space")
            await app.async_set_variable("user.diskspace", space)
            await asyncio.sleep(10)

    global task
    task = asyncio.create_task(poll())

    # This function gets called once per second.
    @iterm2.StatusBarRPC
    async def coro(knobs, space=iterm2.Reference("iterm2.user.diskspace?")):
        if space is None:
            return "Measuring"
        return str("💾 " + space)

    # Register the component.
    await component.async_register(connection, coro)

# This instructs the script to run the "main" coroutine and to keep running even after it returns.
iterm2.run_forever(main)

