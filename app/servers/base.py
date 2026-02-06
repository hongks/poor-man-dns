import asyncio
import logging
import random
import socket
import time

import dns.message
import dns.rcode
import dns.rdataclass
import dns.rdatatype
import dns.rrset
import httpx

from helpers.sqlite import AdsBlockDomain


# ################################################################################
# typing annotations to avoid circular imports


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from helpers.adsblock import ADSServer
    from helpers.config import Config
    from helpers.sqlite import SQLite


# ################################################################################
# base handler


class BaseHandler:
    def __init__(self, server):
        self.server = server

    async def forward_dns(
        self,
        addr: tuple,
        dns_query: dns.message.Message,
        query_name: str,
        cache_keyname: str,
    ) -> dns.message.Message | None:
        target_server = None
        for rule in self.server.forward.dns:
            if not rule:
                continue

            domain, servers = rule.split(":")
            if query_name.endswith(domain + "."):
                target_server = servers.split(",")[0]  # use the first server
                break

        if not target_server:
            self.server.logger.debug(f"{addr} no forward rule: {query_name}")
            return None

        self.server.logger.info(f"{addr} forward: {target_server}, {cache_keyname}")
        self.server.sqlite.update(
            AdsBlockDomain(
                domain=cache_keyname,
                type="forward",
            )
        )

        try:
            loop = asyncio.get_running_loop()
            with socket.socket(
                socket.AF_INET,
                socket.SOCK_DGRAM,
            ) as sock:  # send ipv4 via udp
                sock.setblocking(False)
                await loop.sock_sendto(
                    sock,
                    dns_query.to_wire(),
                    (target_server, 53),
                )

                data, _ = await asyncio.wait_for(
                    loop.sock_recvfrom(sock, 4096),
                    1.5,
                )
                response = dns.message.from_wire(data)

            if self.server.cache.enable:
                self.server.adsblock.set(
                    cache_keyname,
                    {
                        "response": response.answer,
                        "timestamp": time.time(),
                    },
                )

            self.server.logger.debug(f"{addr} response message: {response.to_text()}")
            return response

        except asyncio.TimeoutError as err:
            self.server.logger.error(f"{addr} error forward timeout: {cache_keyname}")
            self.server.logger.error(f"+{type(err).__name__}, {target_server}")

            response = dns.message.make_response(dns_query)
            response.set_rcode(dns.rcode.SERVFAIL)

        except Exception as err:
            self.server.logger.exception(
                f"{addr} error unhandled: {err}\n{cache_keyname}, {target_server}"
            )

            response = dns.message.make_response(dns_query)
            response.set_rcode(dns.rcode.SERVFAIL)

        return response if response else None

    async def handle_request(
        self,
        data: bytes,
        addr: tuple,
    ) -> dns.message.Message:
        # parse dns message ######################################################
        try:
            dns_query = dns.message.from_wire(data)
            query_name = str(dns_query.question[0].name)

            dns_type = dns_query.question[0].rdtype
            query_type = dns.rdatatype.to_text(dns_type)

            cache_keyname = f"{query_name}:{query_type}"
            self.server.logger.debug(f"{addr} received: {cache_keyname}")

        except Exception as err:
            response = dns.message.Message()
            response.set_rcode(dns.rcode.FORMERR)

            self.server.logger.exception(
                f"{addr} error invalid query: {err}\n{data}\n{data.hex()}"
            )
            return response

        # forward custom dns #####################################################
        if query_name in self.server.forward.custom and query_type in [
            "PTR",
            "A",
        ]:
            response = dns.message.make_response(dns_query)
            rrset = dns.rrset.from_text(
                query_name,
                300,
                dns.rdataclass.IN,
                dns.rdatatype.PTR if query_type == "PTR" else dns.rdatatype.A,
                self.server.forward.custom[query_name],
            )
            response.answer.append(rrset)

            self.server.logger.info(f"{addr} custom-hit: {cache_keyname}")
            self.server.sqlite.update(
                AdsBlockDomain(
                    domain=cache_keyname,
                    type="custom-hit",
                )
            )
            return response

        # blocked domains ########################################################
        if (
            query_type == "PTR"
            or query_name in self.server.adsblock.get_blocked_domains()
        ):
            response = dns.message.make_response(dns_query)
            response.set_rcode(dns.rcode.NXDOMAIN)

            self.server.logger.info(f"{addr} blacklisted: {cache_keyname}")
            self.server.sqlite.update(
                AdsBlockDomain(domain=cache_keyname, type="blacklisted")
            )
            return response

        # cache ##################################################################
        if self.server.cache.enable:
            cached = await self.server.adsblock.get(cache_keyname)
            if cached:
                response = dns.message.make_response(dns_query)
                response.answer = cached["response"]

                self.server.logger.info(f"{addr} cache-hit: {cache_keyname}")
                self.server.sqlite.update(
                    AdsBlockDomain(domain=cache_keyname, type="cache-hit")
                )
                return response

        # forward dns ############################################################
        response = await self.forward_dns(
            addr,
            dns_query,
            query_name,
            cache_keyname,
        )
        if response:
            return response

        # upstream doh ###########################################################
        response = await self.server.adsblock.get_or_set(
            cache_keyname,
            lambda: self.upstream_doh(
                addr, dns_query, query_name, query_type, cache_keyname
            ),
        )
        return response

    # import dns.resolver
    # r = dns.resolver.Resolver(configure=False)
    # r.nameservers = ["https://cloudflare-dns.com/dns-query"]
    # query = r.resolve("example.com", "A")
    # print(query.response)
    async def upstream_doh(
        self,
        addr: tuple,
        dns_query: dns.message.Message,
        query_name: str,
        query_type: str,
        cache_keyname: str,
    ) -> dns.message.Message | None:
        response = None
        target_doh = self.server.upstream.doh.copy()
        if self.server.last_target_doh in target_doh:
            target_doh.remove(self.server.last_target_doh)

        target_doh = (
            random.choice(target_doh) if target_doh else self.server.last_target_doh
        )
        self.server.last_target_doh = target_doh

        try:
            self.server.logger.info(f"{addr} upstream: {target_doh}, {cache_keyname}")
            self.server.sqlite.update(
                AdsBlockDomain(domain=cache_keyname, type="upstream")
            )

            # dns-json ###########################################################
            if self.server.upstream.message == "dns-json":
                headers = {
                    "content-type": "application/dns-json",
                    "accept": "application/dns-json",
                    "accept-encoding": "gzip",
                }
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
                        dns.rdatatype.from_text(
                            dns.rdatatype.to_text(
                                answer["type"],
                            )
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

                doh_response = await self.server.http_client.post(
                    target_doh, headers=headers, content=dns_query.to_wire()
                )
                doh_response.raise_for_status()

                response = dns.message.from_wire(doh_response.content)

            # cache ##############################################################
            if self.server.cache.enable:
                self.server.adsblock.set(
                    cache_keyname,
                    {
                        "response": response.answer,
                        "timestamp": time.time(),
                    },
                )

            self.server.logger.debug(f"{addr} response message: {response.to_text()}")

        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.HTTPStatusError,
            httpx.ReadError,
            httpx.ReadTimeout,
        ) as err:
            self.server.logger.error(
                f"+{type(err).__name__} error{addr} upstream: {target_doh}, {cache_keyname}"
            )

            response = dns.message.make_response(dns_query)
            response.set_rcode(dns.rcode.SERVFAIL)

        except Exception as err:
            self.server.logger.exception(
                f"{addr} error unhandled: {err}\n{cache_keyname}, {target_doh}"
            )

            response = dns.message.make_response(dns_query)
            response.set_rcode(dns.rcode.SERVFAIL)

        return response if response else None


# ################################################################################
# base server


class BaseServer:
    def __init__(self, config: "Config", sqlite: "SQLite", adsblock: "ADSServer"):
        name = self.__class__.__name__[:3].lower()
        self.logger = logging.getLogger(name)

        self.cache = config.cache
        self.forward = config.forward
        self.upstream = config.upstream
        self.adsblock = adsblock

        self.last_target_doh = None

        self.session = sqlite.Session()
        self.sqlite = sqlite

        self.http_client = httpx.AsyncClient(
            timeout=9.0, transport=httpx.AsyncHTTPTransport(retries=3)
        )

    async def close(self):
        raise NotImplementedError("close() must be implemented by subclass!")

    async def listen(self):
        raise NotImplementedError("listen() must be implemented by subclass!")

    async def reload(self, config: "Config", adsblock: "ADSServer"):
        await self.close()

        self.cache = config.cache
        self.forward = config.forward
        self.upstream = config.upstream
        self.adsblock = adsblock

        await self.listen()
