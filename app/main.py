import logging
import ssl
import threading

from http.server import HTTPServer

import cachetools

from helpers.configs import Config
from helpers.adapter import Adapter
from helpers.adsblock import AdsBlock
from helpers.dns import DNSHandler, DNSServer
from helpers.doh import DOHHandler
from helpers.sqlite import SQLite


# ################################################################################
# main routine


def main():
    config = Config()
    sqlite = SQLite(config.sqlite_uri)

    config.sync(sqlite.session)
    adsblock = AdsBlock(sqlite, config.adsblock.reload)

    # set up logging
    logging.basicConfig(
        format=config.logging.format,
        level=logging.getLevelName(config.logging.level),
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(config.logging.filename, mode="w"),
        ],
    )

    # ... and silent the others
    for logger in ["httpx", "httpcore", "urllib3", "watchdog"]:
        logging.getLogger(logger).setLevel(logging.WARNING)

    logging.info("initialized!")

    adapter = Adapter()
    adapter.get_dns(config.dns.interface)

    if config.cache.enable:
        config.cache.cache = cachetools.TTLCache(
            maxsize=config.cache.max_size, ttl=config.cache.ttl
        )

    logging.info(
        f"cache-enable: {str(config.cache.enable).lower()}"
        + f", max-size: {config.cache.max_size}, ttl: {config.cache.ttl}"
    )

    httpd = None
    server = None

    try:
        server = DNSServer(
            DNSHandler, config.cache, config.dns, adsblock.blocked_domains
        )
        logging.info(
            f"local dns server running on {config.dns.hostname}:{config.dns.port}"
        )
        # server.serve_forever()

        threads = threading.Thread(target=server.serve_forever)
        threads.daemon = True
        threads.start()

        # set up a list of domains to be blocked
        adsblock.load_blacklist(config.adsblock.blacklist)
        adsblock.load_custom(config.adsblock.custom)
        adsblock.load_whitelist(config.adsblock.whitelist)
        server.blocked_domains = adsblock.blocked_domains

        httpd = HTTPServer((config.doh.hostname, config.doh.port), DOHHandler)
        httpd.blocked_domains = adsblock.blocked_domains
        httpd.cache_enable = config.cache.enable
        httpd.cache_wip = config.cache.wip
        httpd.cache = config.cache.cache
        httpd.dns_custom = config.dns.custom
        httpd.target_doh = config.dns.target_doh
        httpd.target_mode = config.dns.target_mode

        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(certfile="certs/cert.pem", keyfile="certs/key.pem")
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)

        logging.info(
            f"local doh server running on {config.doh.hostname}:{config.doh.port}"
        )
        httpd.serve_forever()

    except KeyboardInterrupt:
        httpd.shutdown()
        server.shutdown()

    # adapter.reset_dns(cfg.dns.interface_name)

    sqlite.session.close()
    logging.info("sayonara!")


# ################################################################################
# where it all begins


if __name__ == "__main__":
    main()
