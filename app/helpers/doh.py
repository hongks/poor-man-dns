import base64
import logging
import random
import time

import dns.message
import dns.query
import dns.rdatatype
import httpx

from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs


class DOHHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_something(self, dns_query, query_name, query_type):
        cache_keyname = f"{query_name}:{query_type}"
        logging.debug(f"{self.client_address} received: {query_name} {query_type}")

        if query_name in self.server.blocked_domains:
            response = dns.message.make_response(dns_query)
            response.set_rcode(dns.rcode.NXDOMAIN)

            logging.info(f"{self.client_address} blacklisted: {query_name}")
            self.send_error(400, "bad request: blacklisted")
            return

        if self.server.cache_enable:
            if cache_keyname in self.server.cache_wip:
                time.sleep(0.3)

            if cache_keyname in self.server.cache:
                cached_response = self.server.cache[cache_keyname]
                response = dns.message.make_response(dns_query)
                response.answer = cached_response["response"]

                logging.info(f"{self.client_address} cache-hit: {cache_keyname}")
                self.send_response(200)
                self.send_header("Content-Type", "application/dns-message")
                self.end_headers()
                self.wfile.write(response.to_wire())
                return

        try:
            if cache_keyname not in self.server.cache_wip:
                self.server.cache_wip.add(cache_keyname)

            target_hostname = random.choice(self.server.target_hostname)
            headers = {
                "content-type": "application/dns-message",
                "accept": "application/dns-message",
                "accept-encoding": "gzip",
            }
            logging.info(
                f"{self.client_address} forward: {cache_keyname}, {target_hostname}"
            )

            doh_response = httpx.post(
                target_hostname,
                headers=headers,
                content=dns_query.to_wire(),
                timeout=10,
            )
            doh_response.raise_for_status()

            response = dns.message.from_wire(doh_response.content)
            logging.debug(
                f"{self.client_address} response message: {response.to_text()}"
            )

            if self.server.cache_enable:
                self.server.cache[cache_keyname] = {
                    "response": response.answer,
                    "timestamp": time.time(),
                }

                if cache_keyname in self.server.cache_wip:
                    self.server.cache_wip.remove(cache_keyname)

            self.send_response(200)
            self.send_header("Content-Type", "application/dns-message")
            self.end_headers()

            self.wfile.write(doh_response.content)

        except Exception as e:
            logging.error(f"{self.client_address} error unhandled: {e}")
            self.send_error(500, "internal server error")

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
                f"{self.client_address} error invalid query:\n{e}\n{dns_query}"
            )
            return

        self.do_something(dns_query, query_name, query_type)

    def do_POST(self):
        logging.debug(f"{self.client_address} request data: {self.request}")

        data = None
        if self.headers.get("Content-Type") == "application/dns-message":
            content_length = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(content_length)
        else:
            logging.error(
                f"{self.client_address} error unsupported query:\n{self.request}"
            )
            self.send_error(400, "bad request: unsupported query")
            return

        try:
            dns_query = dns.message.from_wire(data)
            query_name = str(dns_query.question[0].name)

            dns_type = dns_query.question[0].rdtype
            query_type = dns.rdatatype.to_text(dns_type)

        except Exception as e:
            logging.error(
                f"{self.client_address} error invalid query:\n{e}\n{self.request}"
            )
            return

        self.do_something(dns_query, query_name, query_type)
