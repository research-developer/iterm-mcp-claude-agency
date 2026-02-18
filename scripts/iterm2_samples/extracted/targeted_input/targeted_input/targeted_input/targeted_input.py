#!/usr/bin/env python3
# NOTE: This script depends on aiohttp.
import aiohttp
import asyncio
import iterm2
from aiohttp import web

async def main(connection):
    app = await iterm2.async_get_app(connection)

    async def send_html(txt, request):
        binary = txt.encode('utf8')
        resp = web.StreamResponse()
        resp.content_length = len(binary)
        resp.content_type = 'text/html'
        await resp.prepare(request)
        await resp.write(binary)
        return resp

    def html_for_domain(domain):
        txt = '<hr/><form action="/send" method="POST">'
        n = 0
        for session in domain.sessions:
            txt += '{}: <input name="{}" type="text" value="Value to send to session {}" /><br/>'.format(n, session.session_id, n)
            n += 1
        txt += '<input type="Submit"></form>'
        return txt

    async def main_page(request):
        txt = '<a href="/">Refresh</a><br/>'
        if not app.broadcast_domains:
            txt += "Turn on broadcast input and click refresh"
        for domain in app.broadcast_domains:
            txt += html_for_domain(domain)
        return await send_html(txt, request)

    async def send(request):
        reader = await request.post()
        for session_id in reader.keys():
            value = reader[session_id]
            session = app.get_session_by_id(session_id)
            if session:
                await session.async_send_text(value, suppress_broadcast=True)
        return await main_page(request)

    def init():
        webapp = web.Application()
        webapp.router.add_get('/', main_page)
        return webapp

    # Set up a web server on port 9999. The web pages give the script a user interface.
    webapp = web.Application()
    webapp.router.add_get('/', main_page)
    webapp.router.add_post('/send', send)
    runner = web.AppRunner(webapp)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', 9999)
    await site.start()

    # Register a custom toolbelt tool that shows the web pages served by the server in this script.
    await iterm2.tool.async_register_web_view_tool(connection, "Targeted Input", "com.iterm2.example.targeted-input", False, "http://localhost:9999/")

iterm2.run_forever(main)

