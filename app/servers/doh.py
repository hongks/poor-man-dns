import asyncio
import base64
import ssl

from aiohttp import web

from .base import BaseHandler, BaseServer


# ################################################################################
# typing annotations to avoid circular imports


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from helpers.adsblock import AdsBlock
    from helpers.config import Config
    from helpers.sqlite import SQLite


# ################################################################################
# doh handler


class DOHHandler(BaseHandler):
    def __init__(self, server: "DOHServer"):
        self.server = server

    # aiohttp supports only http v1.1
    #
    # from base64 import urlsafe_b64encode; import dns.message
    # data = dns.message.make_query("example.com.", "A").to_wire()
    # data = urlsafe_b64encode(data).decode("utf-8").rstrip("=")
    #
    # curl -kvH "accept: application/dns-message" "https://127.0.0.1:5053/dns-query?dns=<data>"
    async def do_GET(self, request: web.Request) -> web.Response:
        data = await request.text()
        dns_query_wire = request.query.get("dns")

        if not dns_query_wire:
            self.logger.error(f"{request.remote} error unsupported query:\n{data}")
            return web.Response(status=400, body=b"bad request: unsupported query")

        data = base64.urlsafe_b64decode(dns_query_wire + "==")
        return await self.handle_query(data, request.remote)

    async def do_POST(self, request: web.Request) -> web.Response:
        data = await request.read()

        if request.content_type != "application/dns-message":
            self.logger.error(f"{request.remote} error unsupported query:\n{data}")
            return web.Response(status=400, body=b"bad request: unsupported query")

        return await self.handle_query(data, request.remote)

    async def handle_query(self, data: bytes, addr: tuple) -> web.Response:
        response = await self.handle_request(data, addr)
        if response:
            rcode_name = response.rcode().to_text()
            if rcode_name == "FORMERR":
                return web.Response(status=400, body=b"bad request: invalid query")

            elif rcode_name == "SERVFAIL":
                return web.Response(status=500, body=b"internal server error")

            else:
                return web.Response(
                    status=200,
                    content_type="application/dns-message",
                    body=response.to_wire(),
                )


# ################################################################################
# doh server


class DOHServer(BaseServer):
    def __init__(self, config: "Config", sqlite: "SQLite", adsblock: "AdsBlock"):
        super().__init__(config, sqlite, adsblock)
        self.cache_enable = config.cache.enable

        self.hostname = config.doh.hostname
        self.port = config.doh.port

        self.runner = None
        self.shutdown_event = asyncio.Event()

        self.ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self.ssl_context.load_cert_chain(
            certfile=config.ssl.certfile, keyfile=config.ssl.keyfile
        )

    async def close(self):
        self.shutdown_event.set()

        if self.runner:
            await self.runner.cleanup()

        await self.http_client.aclose()
        self.logger.info("local service shutting down!")

    async def listen(self):
        handler = DOHHandler(self)

        app = web.Application()
        app.router.add_get("/dns-query", handler.do_GET)
        app.router.add_post("/dns-query", handler.do_POST)

        self.runner = web.AppRunner(app)
        await self.runner.setup()

        site = web.TCPSite(
            self.runner,
            self.hostname,
            self.port,
            ssl_context=self.ssl_context,
        )
        await site.start()

        self.logger.info(f"local service running on {self.hostname}:{self.port}.")
        await self.shutdown_event.wait()
