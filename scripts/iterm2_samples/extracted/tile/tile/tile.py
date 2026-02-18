import iterm2

async def main(connection):
    tmux_conns = await iterm2.async_get_tmux_connections(connection)
    for tmux in tmux_conns:
        await tmux.async_send_command("select-layout tile")

iterm2.run_until_complete(main)


