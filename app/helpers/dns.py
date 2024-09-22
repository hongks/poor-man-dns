import logging
import random
import time
import socketserver

import dns.message
import dns.query
import dns.rdatatype
import httpx


class DNSHandler(socketserver.BaseRequestHandler):
    def send_response(self, socket, response):
        try:
            socket.sendto(response.to_wire(), self.client_address)
        except Exception as e:
            logging.error(f"{self.client_address} error replying: {e}")

    def handle(self):
        logging.debug(f"{self.client_address} request data: {self.request}")
        data = self.request[0].strip()
        socket = self.request[1]

        # parse dns message ######################################################
        try:
            dns_query = dns.message.from_wire(data)
            query_name = str(dns_query.question[0].name)

            dns_type = dns_query.question[0].rdtype
            query_type = dns.rdatatype.to_text(dns_type)

            cache_keyname = f"{query_name}:{query_type}"
            logging.debug(f"{self.client_address} received: {query_name} {query_type}")

        except Exception as e:
            response = dns.message.Message()
            response.set_rcode(dns.rcode.FORMERR)

            logging.error(
                f"{self.client_address} error invalid query:\n{e}\n{self.request}"
            )
            self.send_response(socket, response)
            return

        # custom dns #############################################################
        if query_name in self.server.dns_custom and query_type == "A":
            response = dns.message.make_response(dns_query)
            rrset = dns.rrset.from_text(
                query_name,
                300,
                dns.rdataclass.IN,
                dns.rdatatype.A,
                self.server.dns_custom[query_name],
            )
            response.answer.append(rrset)

            logging.info(f"{self.client_address} custom-hit: {query_name}")
            self.send_response(socket, response)
            return

        # blocked domain #########################################################
        if query_name in self.server.blocked_domains:
            response = dns.message.make_response(dns_query)
            response.set_rcode(dns.rcode.NXDOMAIN)

            logging.info(f"{self.client_address} blacklisted: {query_name}")
            self.send_response(socket, response)
            return

        # cache ##################################################################
        if self.server.cache_enable:
            if cache_keyname in self.server.cache_wip:
                time.sleep(1)
            else:
                self.server.cache_wip.add(cache_keyname)

            if cache_keyname in self.server.cache:
                response = dns.message.make_response(dns_query)
                response.answer = self.server.cache[cache_keyname]["response"]
                # response = dns.message.from_wire(cached_response["response"])

                logging.info(f"{self.client_address} cache-hit: {cache_keyname}")
                self.send_response(socket, response)
                return

        try:
            target_doh = random.choice(self.server.target_doh)
            logging.info(
                f"{self.client_address} forward: {cache_keyname}, {target_doh}"
            )

            if self.server.target_mode == "dns-json":
                # dns-json #######################################################
                headers = {
                    "accept": "application/dns-json",
                    "accept-encoding": "gzip",
                }
                params = {"name": query_name, "type": query_type}

                doh_response = httpx.get(
                    target_doh, headers=headers, params=params, timeout=9.0
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
                            dns.rdatatype.to_text(answer["type"]),
                        ),
                        answer["data"],
                    )

                    response.answer.append(rrset)

            else:
                # dns-message ####################################################
                headers = {
                    "content-type": "application/dns-message",
                    "accept": "application/dns-message",
                    "accept-encoding": "gzip",
                }

                doh_response = httpx.post(
                    target_doh,
                    headers=headers,
                    content=dns_query.to_wire(),
                    timeout=9.0,
                )
                doh_response.raise_for_status()

                response = dns.message.from_wire(doh_response.content)

            logging.debug(
                f"{self.client_address} response message: {response.to_text()}"
            )

            # cache ##############################################################
            if self.server.cache_enable:
                self.server.cache[cache_keyname] = {
                    "response": response.answer,
                    "timestamp": time.time(),
                }

                if cache_keyname in self.server.cache_wip:
                    self.server.cache_wip.remove(cache_keyname)

        except Exception as e:
            logging.error(f"{self.client_address} error unhandled: {e}")

            response = dns.message.make_response(dns_query)
            response.set_rcode(dns.rcode.SERVFAIL)

        finally:
            self.send_response(socket, response)


class DNSServer(socketserver.ThreadingUDPServer):
    def __init__(
        self,
        RequestHandlerClass,
        cache,
        dns,
        blocked_domains,
    ):
        self.cache_enable = cache.enable
        self.cache_wip = cache.wip
        self.cache = cache.cache

        self.blocked_domains = blocked_domains
        self.dns_custom = dns.custom
        self.target_doh = dns.target_doh
        self.target_mode = dns.target_mode

        super().__init__((dns.hostname, dns.port), RequestHandlerClass)
