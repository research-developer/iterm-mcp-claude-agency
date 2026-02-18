import iterm2

async def main(connection):
    async with iterm2.CustomControlSequenceMonitor(
            connection, "shared-secret", r'^create-window$') as mon:
        while True:
            match = await mon.async_get()
            await iterm2.Window.async_create(connection)

iterm2.run_forever(main)
