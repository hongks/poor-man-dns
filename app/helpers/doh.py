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

    async def do_something(self, remote_addr, dns_query, query_name, query_type):
        cache_keyname = f"{query_name}:{query_type}"
        logging.debug(f"{remote_addr} received: {query_name} {query_type}")

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

            self.server.sqlite.update(
                AdsBlockDomain(domain=cache_keyname, type="custom-hit")
            )
            logging.info(f"{remote_addr} custom-hit: {cache_keyname}")
            return web.Response(
                status=200,
                content_type="application/dns-message",
                body=response.to_wire(),
            )

        # blocked domain #########################################################
        if query_name in self.server.blocked_domains:
            response = dns.message.make_response(dns_query)
            response.set_rcode(dns.rcode.NXDOMAIN)

            self.server.sqlite.update(
                AdsBlockDomain(domain=cache_keyname, type="blacklisted")
            )
            logging.info(f"{remote_addr} blacklisted: {cache_keyname}")
            return web.Response(
                status=200,
                content_type="application/dns-message",
                body=response.to_wire(),
            )

        # cache ##################################################################
        if self.server.cache_enable:
            if cache_keyname in self.server.cache_wip:
                await asyncio.sleep(3)

            if cache_keyname in self.server.cache:
                response = dns.message.make_response(dns_query)
                response.answer = self.server.cache[cache_keyname]["response"]

                self.server.sqlite.update(
                    AdsBlockDomain(domain=cache_keyname, type="cache-hit")
                )
                logging.info(f"{remote_addr} cache-hit: {cache_keyname}")
                return web.Response(
                    status=200,
                    content_type="application/dns-message",
                    body=response.to_wire(),
                )

            else:
                self.server.cache_wip.add(cache_keyname)

        try:
            target_doh = self.server.target_doh.copy()
            if self.server.last_target_doh in target_doh:
                target_doh.remove(self.server.last_target_doh)

            if target_doh:
                target_doh = random.choice(target_doh)
            else:
                target_doh = self.server.last_target_doh

            self.server.last_target_doh = target_doh

            logging.info(f"{remote_addr} forward: {cache_keyname}, {target_doh}")
            self.server.sqlite.update(
                AdsBlockDomain(domain=cache_keyname, type="forward")
            )

            # dns-json ###########################################################
            if self.server.target_mode == "dns-json":
                headers = {
                    "accept": "application/dns-json",
                    "accept-encoding": "gzip",
                }
                params = {"name": query_name, "type": query_type}

                async with httpx.AsyncClient(
                    timeout=9.0, transport=httpx.AsyncHTTPTransport(retries=3)
                ) as client:
                    doh_response = await client.get(
                        target_doh, headers=headers, params=params
                    )

                    doh_response.raise_for_status()
                    doh_response_json = doh_response.json()

                response = dns.message.make_response(
                    dns.message.make_query(query_name, query_type)
                )

                for answer in doh_response_json.get("Answer", []):
                    rrset = dns.rrset.from_text(
                        query_name,
                        answer["TTL"],
                        dns.rdataclass.IN,
                        dns.rdatatype.from_text(
                            dns.rdatatype.to_text(answer["type"]),
                        ),
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

                async with httpx.AsyncClient(
                    timeout=9.0, transport=httpx.AsyncHTTPTransport(retries=3)
                ) as client:
                    doh_response = await client.post(
                        target_doh,
                        headers=headers,
                        content=dns_query.to_wire(),
                    )

                    doh_response.raise_for_status()
                    response = dns.message.from_wire(doh_response.content)

            logging.debug(f"{remote_addr} response message: {response.to_text()}")

            # cache ##############################################################
            if self.server.cache_enable:
                self.server.cache[cache_keyname] = {
                    "response": response.answer,
                    "timestamp": time.time(),
                }

            return web.Response(
                status=200,
                content_type="application/dns-message",
                body=response.to_wire(),
            )

        except (httpx.ConnectTimeout, httpx.HTTPStatusError) as err:
            logging.error(
                f"{remote_addr} error forward: {cache_keyname}, {target_doh}\n{err}"
            )
            return web.Response(status=500, body="internal server error")

        except Exception as err:
            logging.exception(f"{remote_addr} error unhandled: {err}")
            return web.Response(status=500, body="internal server error")

        finally:
            self.server.cache_wip.discard(cache_keyname)

    # curl -kvH "accept: application/dns-message"
    #   "https://127.0.0.1:5053/dns-query?dns=q80BAAABAAAAAAAAA3d3dwdleGFtcGxlA2NvbQAAAQAB"
    async def do_GET(self, request):
        data = await request.text()
        logging.debug(f"{request.remote} request data: {data}")

        dns_query_wire = request.query.get("dns")
        if not dns_query_wire:
            logging.error(f"{request.remote} error unsupported query:\n{self.path}")
            return web.Response(status=400, body="bad request: unsupported query")

        try:
            data = base64.urlsafe_b64decode(dns_query_wire + "==")
            dns_query = dns.message.from_wire(data)
            query_name = str(dns_query.question[0].name)

            dns_type = dns_query.question[0].rdtype
            query_type = dns.rdatatype.to_text(dns_type)

        except Exception as err:
            logging.exception(
                f"{request.remote} error invalid query: {err}\n{dns_query}"
            )
            return web.Response(status=400, body="bad request: invalid query")

        return await self.do_something(
            request.remote, dns_query, query_name, query_type
        )

    async def do_POST(self, request):
        data = await request.read()
        logging.debug(f"{request.remote} request data: {data}")

        if request.content_type != "application/dns-message":
            logging.error(f"{request.remote} error unsupported query:\n{data}")
            return web.Response(status=400, body="bad request: unsupported query")

        try:
            dns_query = dns.message.from_wire(data)
            query_name = str(dns_query.question[0].name)

            dns_type = dns_query.question[0].rdtype
            query_type = dns.rdatatype.to_text(dns_type)

        except Exception as err:
            logging.exception(f"{request.remote} error invalid query:\n{err}\n{data}")
            return web.Response(status=400, body="bad request: unsupported query")

        return await self.do_something(
            request.remote, dns_query, query_name, query_type
        )


class DOHServer:
    def __init__(self, config, sqlite, blocked_domains):
        self.cache_enable = config.cache.enable
        self.cache_wip = config.cache.wip
        self.cache = config.cache.cache

        self.hostname = config.doh.hostname
        self.port = config.doh.port
        self.dns_custom = config.dns.custom
        self.target_doh = config.dns.target_doh
        self.target_mode = config.dns.target_mode
        self.last_target_doh = None

        self.blocked_domains = blocked_domains
        self.filepath = config.filepath

        self.session = sqlite.session
        self.sqlite = sqlite
        self.running = True
        self.runner = None

        self.ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self.ssl_context.load_cert_chain(
            certfile=f"{self.filepath}/certs/cert.pem",
            keyfile=f"{self.filepath}/certs/key.pem",
        )

    async def close(self):
        self.running = False
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
            await asyncio.sleep(3600)

    async def reload(self, config, blocked_domains):
        self.dns_custom = config.dns.custom
        self.target_doh = config.dns.target_doh
        self.target_mode = config.dns.target_mode

        self.blocked_domains = blocked_domains
