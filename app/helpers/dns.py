import asyncio
import logging
import random
import time

import dns.message
import dns.rdatatype
import httpx

from .sqlite import AdsBlockDomain


class DNSHandler(asyncio.DatagramProtocol):
    def __init__(self, server):
        self.server = server

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        asyncio.create_task(self.handle_request(data, addr))

    # https://github.com/python/cpython/issues/91227
    # https://github.com/python/cpython/issues/127057
    # https://www.fournoas.com/posts/asyncio.DatagramProtocol-stop-responding-when-an-error-is-received/
    # def error_received(self, err):
    #     self.server.restart = True
    #     logging.error(f"error received, unexpected {err=}, {type(err)=}")

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

            logging.info(f"{addr} forward: {cache_keyname}, {target_doh}")
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

            logging.debug(f"{addr} response message: {response.to_text()}")

        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.HTTPStatusError,
            httpx.ReadError,
            httpx.ReadTimeout,
        ) as err:
            logging.error(f"{addr} error forward: {cache_keyname}")
            logging.error(f"+{type(err).__name__}, {target_doh}")

            response = dns.message.make_response(dns_query)
            response.set_rcode(dns.rcode.SERVFAIL)

        except Exception as err:
            logging.exception(
                f"{addr} error unhandled: {err}\n{cache_keyname}, {target_doh}"
            )

            response = dns.message.make_response(dns_query)
            response.set_rcode(dns.rcode.SERVFAIL)

        return response if response else None

    async def handle_request(self, data, addr):
        # parse dns message ######################################################
        try:
            dns_query = dns.message.from_wire(data)
            query_name = str(dns_query.question[0].name)

            dns_type = dns_query.question[0].rdtype
            query_type = dns.rdatatype.to_text(dns_type)

            cache_keyname = f"{query_name}:{query_type}"
            logging.debug(f"{addr} received: {cache_keyname}")

        except Exception as err:
            response = dns.message.Message()
            response.set_rcode(dns.rcode.FORMERR)

            logging.exception(
                f"{addr} error invalid query: {err}\n{data}\n{data.hex()}"
            )
            self.transport.sendto(response.to_wire(), addr)
            return

        # custom dns #############################################################
        if query_name in self.server.dns_custom and query_type in ["PTR", "A"]:
            response = dns.message.make_response(dns_query)
            rrset = dns.rrset.from_text(
                query_name,
                300,
                dns.rdataclass.IN,
                dns.rdatatype.PTR if query_type == "PTR" else dns.rdatatype.A,
                self.server.dns_custom[query_name],
            )
            response.answer.append(rrset)

            logging.info(f"{addr} custom-hit: {cache_keyname}")
            self.server.sqlite.update(
                AdsBlockDomain(domain=cache_keyname, type="custom-hit")
            )
            self.transport.sendto(response.to_wire(), addr)
            return

        # blocked domain #########################################################
        if query_name in self.server.adsblock.get_blocked_domains():
            response = dns.message.make_response(dns_query)
            response.set_rcode(dns.rcode.NXDOMAIN)

            logging.info(f"{addr} blacklisted: {cache_keyname}")
            self.server.sqlite.update(
                AdsBlockDomain(domain=cache_keyname, type="blacklisted")
            )
            self.transport.sendto(response.to_wire(), addr)
            return

        # cache ##################################################################
        if self.server.cache_enable:
            cached = await self.server.adsblock.get(cache_keyname)
            if cached:
                response = dns.message.make_response(dns_query)
                response.answer = cached["response"]

                logging.info(f"{addr} cache-hit: {cache_keyname}")
                self.server.sqlite.update(
                    AdsBlockDomain(domain=cache_keyname, type="cache-hit")
                )
                self.transport.sendto(response.to_wire(), addr)
                return

        # forward_to_doh #########################################################
        response = await self.server.adsblock.get_or_set(
            cache_keyname,
            lambda: self.forward_to_doh(
                addr, dns_query, query_name, query_type, cache_keyname
            ),
        )
        self.transport.sendto(response.to_wire(), addr)


class DNSServer:
    def __init__(self, config, sqlite, adsblock):
        self.cache_enable = config.cache.enable

        self.hostname = config.dns.hostname
        self.port = config.dns.port
        self.dns_custom = config.dns.custom
        self.target_doh = config.dns.target_doh
        self.target_mode = config.dns.target_mode
        self.last_target_doh = None

        self.adsblock = adsblock
        self.restart = False
        self.running = True
        self.transport = None

        self.session = sqlite.Session()
        self.sqlite = sqlite

        self.http_client = httpx.AsyncClient(
            timeout=9.0, transport=httpx.AsyncHTTPTransport(retries=3)
        )

    async def close(self):
        self.running = False

        if self.transport:
            self.transport.close()
            self.transport = None

        await self.http_client.aclose()
        logging.info("local dns server shutting down!")

    async def listen(self):
        loop = asyncio.get_running_loop()

        while self.running:
            if not self.transport or self.restart:
                # clean up old transport
                if self.transport:
                    self.transport.close()
                    self.transport = None

                self.restart = False
                logging.info("attempting to restart local dns server ...")
                await asyncio.sleep(1)

            try:
                self.transport, protocol = await asyncio.wait_for(
                    loop.create_datagram_endpoint(
                        lambda: DNSHandler(self),
                        local_addr=(self.hostname, self.port),
                    ),
                    timeout=3,  # timeout in seconds
                )

                logging.info(
                    f"local dns server running on {self.hostname}:{self.port}."
                )

            except asyncio.TimeoutError:
                self.restart = True

            await asyncio.sleep(1)

    async def reload(self, config, adsblock):
        await self.close()
        self.cache_enable = config.cache.enable

        self.dns_custom = config.dns.custom
        self.target_doh = config.dns.target_doh
        self.target_mode = config.dns.target_mode

        self.adsblock = adsblock
        await self.listen()
