import base64
import logging
import random
import ssl
import time

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import dns.message
import dns.query
import dns.rdatatype
import httpx


class DOHHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_response(self, status_code, content_type, response_data):
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(response_data)

    def do_something(self, dns_query, query_name, query_type):
        cache_keyname = f"{query_name}:{query_type}"
        logging.debug(f"{self.client_address} received: {query_name} {query_type}")

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

            logging.info(f"{self.client_address} custom-hit: {cache_keyname}")
            self.do_response(200, "application/dns-message", response.to_wire())
            return

        # blocked domain #########################################################
        if query_name in self.server.blocked_domains:
            logging.info(f"{self.client_address} blacklisted: {cache_keyname}")
            self.send_error(400, "bad request: blacklisted")
            return

        # cache ##################################################################
        if self.server.cache_enable:
            if cache_keyname in self.server.cache_wip:
                time.sleep(3)

            if cache_keyname in self.server.cache:
                response = dns.message.make_response(dns_query)
                response.answer = self.server.cache[cache_keyname]["response"]

                logging.info(f"{self.client_address} cache-hit: {cache_keyname}")
                self.do_response(200, "application/dns-message", response.to_wire())
                return

            else:
                self.server.cache_wip.add(cache_keyname)

        try:
            target_doh = random.choice(self.server.target_doh)
            logging.info(
                f"{self.client_address} forward: {cache_keyname}, {target_doh}"
            )

            # dns-json ###########################################################
            if self.server.target_mode == "dns-json":
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

            self.do_response(200, "application/dns-message", response.to_wire())

        except Exception as e:
            logging.error(f"{self.client_address} error unhandled: {e}")
            self.send_error(500, "internal server error")

        finally:
            if self.server.cache_enable:
                self.server.cache_wip.discard(cache_keyname)

    # curl -kvH "accept: application/dns-message"
    #   "https://127.0.0.1:5053/dns-query?dns=q80BAAABAAAAAAAAA3d3dwdleGFtcGxlA2NvbQAAAQAB"
    def do_GET(self):
        logging.debug(f"{self.client_address} request data: {self.request}")

        parsed_path = urlparse(self.path)
        params = parse_qs(parsed_path.query)
        dns_query_wire = params.get("dns", [None])[0]

        if not dns_query_wire:
            logging.error(
                f"{self.client_address} error unsupported query:\n{self.path}"
            )

            self.send_error(400, "bad request: unsupported query")
            return

        try:
            data = base64.urlsafe_b64decode(dns_query_wire + "==")
            dns_query = dns.message.from_wire(data)
            query_name = str(dns_query.question[0].name)

            dns_type = dns_query.question[0].rdtype
            query_type = dns.rdatatype.to_text(dns_type)

        except Exception as e:
            logging.error(
                f"{self.client_address} error invalid query: {e}\n{dns_query}"
            )
            return

        self.do_something(dns_query, query_name, query_type)

    def do_POST(self):
        logging.debug(f"{self.client_address} request data: {self.request}")

        if self.headers.get("Content-Type") != "application/dns-message":
            logging.error(
                f"{self.client_address} error unsupported query:\n{self.request}"
            )

            self.send_error(400, "bad request: unsupported query")
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(content_length)

            dns_query = dns.message.from_wire(data)
            query_name = str(dns_query.question[0].name)

            dns_type = dns_query.question[0].rdtype
            query_type = dns.rdatatype.to_text(dns_type)

            self.do_something(dns_query, query_name, query_type)

        except Exception as e:
            logging.error(
                f"{self.client_address} error invalid query:\n{e}\n{self.request}"
            )

            self.send_error(400, "bad request: unsupported query")
            return


class DOHServer(ThreadingHTTPServer):
    def __init__(self, config, sqlite, blocked_domains):
        self.cache_enable = config.cache.enable
        self.cache_wip = config.cache.wip
        self.cache = config.cache.cache

        self.dns_custom = config.dns.custom
        self.target_doh = config.dns.target_doh
        self.target_mode = config.dns.target_mode

        self.blocked_domains = blocked_domains
        self.filepath = config.filepath

        self.session = sqlite.session
        self.sqlite = sqlite

        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(
            certfile=f"{self.filepath}/certs/cert.pem",
            keyfile=f"{self.filepath}/certs/key.pem",
        )

        super().__init__((config.doh.hostname, config.doh.port), DOHHandler)
        self.socket = context.wrap_socket(self.socket, server_side=True)

        logging.info(
            f"local doh server running on {config.doh.hostname}:{config.doh.port}."
        )
