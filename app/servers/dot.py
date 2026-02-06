import asyncio
import ssl

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


class DOTHandler(BaseHandler):
    def __init__(self, server: "DOTServer"):
        self.server = server

    # kdig @localhost:5853 +tls -d example.com
    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        addr = writer.get_extra_info("peername")
        try:
            while True:
                # dot requires 2-byte length prefix
                length_bytes = await reader.readexactly(2)
                length = int.from_bytes(length_bytes, "big")
                data = await reader.readexactly(length)

                response = await self.handle_request(data, addr)
                if response:
                    wire = response.to_wire()
                    writer.write(len(wire).to_bytes(2, "big") + wire)
                    await writer.drain()

        except asyncio.IncompleteReadError:
            pass  # client closed connection

        except Exception as e:
            self.server.logger.error(f"[dot] error: {e}")

        finally:
            writer.close()
            await writer.wait_closed()


# ################################################################################
# dot server


class DOTServer(BaseServer):
    def __init__(self, config: "Config", sqlite: "SQLite", adsblock: "ADSServer"):
        super().__init__(config, sqlite, adsblock)

        self.hostname = config.dot.hostname
        self.port = config.dot.port

        self.server = None

        self.ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ssl_context.load_cert_chain(
            certfile=config.ssl.certfile, keyfile=config.ssl.keyfile
        )

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()

        await self.http_client.aclose()
        self.logger.info("local service shutting down!")

    async def listen(self):
        try:
            self.server = await asyncio.wait_for(
                asyncio.start_server(
                    lambda reader, writer: DOTHandler(self).handle_client(
                        reader, writer
                    ),
                    self.hostname,
                    self.port,
                    ssl=self.ssl_context,
                ),
                timeout=9,  # timeout in seconds
            )

            self.logger.info(f"local service running on {self.hostname}:{self.port}.")
            await self.server.serve_forever()

        except PermissionError:
            self.logger.error("permission denied, need rights to do so!")

        except Exception as err:
            self.logger.exception(f"unexpected {err=}, {type(err)=}")
