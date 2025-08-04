import asyncio
import base64
import logging
import random
import ssl
import time

import dns.message
import dns.rdatatype
import httpx

from aiohttp import web

from .sqlite import AdsBlockDomain


class DOHHandler:
    def __init__(self, server):
        self.server = server

    async def handle_query(self, addr, data):
        try:
            dns_query = dns.message.from_wire(data)
            query_name = str(dns_query.question[0].name)

            dns_type = dns_query.question[0].rdtype
            query_type = dns.rdatatype.to_text(dns_type)

            cache_keyname = f"{query_name}:{query_type}"
            logging.debug(f"{addr} received: {cache_keyname}")

        except Exception as err:
            logging.exception(f"{addr} error invalid query: {err}\n{data}")
            return web.Response(status=400, body=b"bad request: invalid query")

        # custom dns #############################################################
        if query_name in self.server.dns_custom and query_type in ["PTR", "A"]:
            response = dns.message.make_response(dns_query)
            rrset = dns.rrset.from_text(
                query_name,
                300,
                dns.rdataclass.IN,
                dns.rdatatype.A,
                self.server.dns_custom[query_name],
            )
            response.answer.append(rrset)

            logging.info(f"{(addr,)} custom-hit: {cache_keyname}")
            self.server.sqlite.update(
                AdsBlockDomain(domain=cache_keyname, type="custom-hit")
            )
            return web.Response(
                status=200,
                content_type="application/dns-message",
                body=response.to_wire(),
            )

        # blocked domain #########################################################
        if query_name in self.server.adsblock.get_blocked_domains():
            response = dns.message.make_response(dns_query)
            response.set_rcode(dns.rcode.NXDOMAIN)

            logging.info(f"{(addr,)} blacklisted: {cache_keyname}")
            self.server.sqlite.update(
                AdsBlockDomain(domain=cache_keyname, type="blacklisted")
            )
            return web.Response(
                status=200,
                content_type="application/dns-message",
                body=response.to_wire(),
            )

        # cache ##################################################################
        if self.server.cache_enable:
            cached = await self.server.adsblock.get(cache_keyname)
            if cached:
                response = dns.message.make_response(dns_query)
                response.answer = cached["response"]

                logging.info(f"{(addr,)} cache-hit: {cache_keyname}")
                self.server.sqlite.update(
                    AdsBlockDomain(domain=cache_keyname, type="cache-hit")
                )
                return web.Response(
                    status=200,
                    content_type="application/dns-message",
                    body=response.to_wire(),
                )

        return await self.server.adsblock.get_or_set(
            cache_keyname,
            lambda: self.forward_to_doh(
                addr, dns_query, query_name, query_type, cache_keyname
            ),
        )

    async def forward_to_doh(
        self, addr, dns_query, query_name, query_type, cache_keyname
    ):
        response = None

        try:
            target_doh = self.server.target_doh.copy()
            if self.server.last_target_doh in target_doh:
                target_doh.remove(self.server.last_target_doh)

            target_doh = (
                random.choice(target_doh) if target_doh else self.server.last_target_doh
            )
            self.server.last_target_doh = target_doh

            logging.info(f"{(addr,)} forward: {cache_keyname}, {target_doh}")
            self.server.sqlite.update(
                AdsBlockDomain(domain=cache_keyname, type="forward")
            )

            # dns-json ###########################################################
            if self.server.target_mode == "dns-json":
                headers = {"accept": "application/dns-json", "accept-encoding": "gzip"}
                params = {"name": query_name, "type": query_type}

                doh_response = await self.server.http_client.get(
                    target_doh, headers=headers, params=params
                )
                doh_response.raise_for_status()
                doh_response_json = doh_response.json()

                response = dns.message.make_response(dns_query)
                for answer in doh_response_json.get("Answer", []):
                    rrset = dns.rrset.from_text(
                        query_name,
                        answer["TTL"],
                        dns.rdataclass.IN,
                        dns.rdatatype.from_text(dns.rdatatype.to_text(answer["type"])),
                        answer["data"],
                    )
                    response.answer.append(rrset)

            # dns-message ########################################################
            else:
                headers = {
                    "content-type": "application/dns-message",
                    "accept": "application/dns-message",
                    "accept-encoding": "gzip",
                }

                doh_response = await self.server.http_client.post(
                    target_doh, headers=headers, content=dns_query.to_wire()
                )
                doh_response.raise_for_status()

                response = dns.message.from_wire(doh_response.content)

            # cache ##############################################################
            if self.server.cache_enable:
                self.server.adsblock.set(
                    cache_keyname,
                    {
                        "response": response.answer,
                        "timestamp": time.time(),
                    },
                )

            logging.debug(f"{(addr,)} response message: {response.to_text()}")
            return web.Response(
                status=200,
                content_type="application/dns-message",
                body=response.to_wire(),
            )

        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.HTTPStatusError,
            httpx.ReadTimeout,
        ) as err:
            logging.error(f"{addr} error forward: {cache_keyname}")
            logging.error(f"+{type(err).__name__}, {target_doh}")

            return web.Response(status=500, body=b"internal server error")

        except Exception as err:
            logging.exception(
                f"{(addr,)} error unhandled: {err}\n{cache_keyname}, {target_doh}"
            )
            return web.Response(status=500, body=b"internal server error")

    # from base64 import urlsafe_b64encode; import dns.message
    # data = dns.message.make_query("example.com.", "A").to_wire()
    # data = urlsafe_b64encode(data).decode("utf-8").rstrip("=")
    #
    # curl -kvH "accept: application/dns-message" "https://127.0.0.1:5053/dns-query?dns=<data>"
    async def do_GET(self, request):
        data = await request.text()
        dns_query_wire = request.query.get("dns")

        if not dns_query_wire:
            logging.error(f"{request.remote} error unsupported query:\n{data}")
            return web.Response(status=400, body=b"bad request: unsupported query")

        data = base64.urlsafe_b64decode(dns_query_wire + "==")
        return await self.handle_query(request.remote, data)

    async def do_POST(self, request):
        data = await request.read()

        if request.content_type != "application/dns-message":
            logging.error(f"{request.remote} error unsupported query:\n{data}")
            return web.Response(status=400, body=b"bad request: unsupported query")

        return await self.handle_query(request.remote, data)


class DOHServer:
    def __init__(self, config, sqlite, adsblock):
        self.cache_enable = config.cache.enable

        self.hostname = config.doh.hostname
        self.port = config.doh.port
        self.dns_custom = config.dns.custom
        self.target_doh = config.dns.target_doh
        self.target_mode = config.dns.target_mode
        self.last_target_doh = None

        self.adsblock = adsblock
        self.filepath = config.filepath

        self.session = sqlite.Session()
        self.sqlite = sqlite
        self.running = True
        self.runner = None

        self.http_client = httpx.AsyncClient(
            timeout=9.0, transport=httpx.AsyncHTTPTransport(retries=3)
        )
        self.ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self.ssl_context.load_cert_chain(
            certfile=f"{self.filepath}/certs/cert.pem",
            keyfile=f"{self.filepath}/certs/key.pem",
        )

    async def close(self):
        self.running = False

        await self.http_client.aclose()
        if self.runner:
            await self.runner.cleanup()

        logging.debug("local doh server shutting down!")

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

        logging.getLogger("aiohttp").setLevel(logging.WARNING)
        logging.info(f"local doh server running on {self.hostname}:{self.port}.")

        while self.running:
            await asyncio.sleep(60)

    async def reload(self, config, adsblock):
        await self.close()
        self.cache_enable = config.cache.enable

        self.dns_custom = config.dns.custom
        self.target_doh = config.dns.target_doh
        self.target_mode = config.dns.target_mode

        self.adsblock = adsblock
        self.filepath = config.filepath
        await self.listen()
