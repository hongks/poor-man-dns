import asyncio

import dns.message

from .base import BaseHandler, BaseServer


# ################################################################################
# typing annotations to avoid circular imports


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from helpers.adsblock import ADSServer
    from helpers.config import Config
    from helpers.sqlite import SQLite


# ################################################################################
# dot handler


class DNSHandler(BaseHandler, asyncio.DatagramProtocol):
    def __init__(self, server: "DNSServer"):
        self.server = server

    def connection_made(self, transport: asyncio.DatagramTransport):
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple):
        asyncio.create_task(self.handle_request(data, addr))

    async def handle_request(self, data: bytes, addr: tuple) -> dns.message.Message:
        response = await super().handle_request(data, addr)
        if response:
            self.transport.sendto(response.to_wire(), addr)

        return response

    # https://github.com/python/cpython/issues/91227
    # https://github.com/python/cpython/issues/127057
    # https://www.fournoas.com/posts/asyncio.DatagramProtocol-stop-responding-when-an-error-is-received/
    # def error_received(self, err):
    #     self.server.restart = True
    #     logging.error(f"error received, unexpected {err=}, {type(err)=}")


# ################################################################################
# dns server


class DNSServer(BaseServer):
    def __init__(self, config: "Config", sqlite: "SQLite", adsblock: "ADSServer"):
        super().__init__(config, sqlite, adsblock)

        self.hostname = config.dns.hostname
        self.port = config.dns.port

        self.transport = None

        self.shutdown_event = asyncio.Event()
        self.restart = False
        self.running = True

    async def close(self):
        self.running = False
        self.shutdown_event.set()

        if self.transport:
            self.transport.close()
            self.transport = None

        await self.http_client.aclose()
        self.logger.info("local service shutting down!")

    async def listen(self):
        loop = asyncio.get_running_loop()

        while self.running:
            if not self.transport or self.restart:
                # clean up old transport
                if self.transport:
                    self.transport.close()
                    self.transport = None

                    self.restart = False
                    self.logger.info("attempt to restart local service ...")
                    await asyncio.sleep(1)

            try:
                self.transport, protocol = await asyncio.wait_for(
                    loop.create_datagram_endpoint(
                        lambda: DNSHandler(self),
                        local_addr=(self.hostname, self.port),
                    ),
                    timeout=9,  # timeout in seconds
                )

                self.logger.info(
                    f"local service running on {self.hostname}:{self.port}."
                )
                await self.shutdown_event.wait()

            except asyncio.TimeoutError:
                self.restart = True

            except PermissionError:
                self.logger.error("permission denied, need rights to do so!")

            await asyncio.sleep(1)
