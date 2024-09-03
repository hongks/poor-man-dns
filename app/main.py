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


# ##############################################################################
# main routine


def main():
    cfg = Config()
    session = SQLite().session(cfg)
    adsblock = AdsBlock(session, cfg.adsblock.reload)

    # set up logging
    logging.basicConfig(
        format=cfg.logging.format,
        level=logging.getLevelName(cfg.logging.level),
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(cfg.logging.filename, mode="w"),
        ],
    )

    # ... and silent the others
    for logger in ["httpx", "httpcore", "urllib3", "watchdog"]:
        logging.getLogger(logger).setLevel(logging.WARNING)

    logging.info("initialized!")

    adapter = Adapter()
    adapter.get_dns(cfg.dns.interface_name)

    if cfg.cache.enable:
        cfg.cache.cache = cachetools.TTLCache(
            maxsize=cfg.cache.max_size, ttl=cfg.cache.ttl
        )

    logging.info(
        f"cache-enable: {str(cfg.cache.enable).lower()}"
        + f", max-size: {cfg.cache.max_size}, ttl: {cfg.cache.ttl}"
    )

    try:
        server = DNSServer(
            (cfg.dns.listen_on, cfg.dns.listen_port),
            DNSHandler,
            adsblock.blocked_domains,
            cfg.cache,
            cfg.dns.target_hostname,
        )
        logging.info(
            f"local dns server running on {cfg.dns.listen_on}:{cfg.dns.listen_port}"
        )
        # server.serve_forever()

        threads = threading.Thread(target=server.serve_forever)
        threads.daemon = True
        threads.start()

        # set up a list of domains to be blocked
        adsblock.load_lists(cfg.adsblock.list_uri)
        adsblock.load_custom(cfg.adsblock.custom)
        adsblock.load_whitelist(cfg.adsblock.whitelist)
        server.blocked_domains = adsblock.blocked_domains

        httpd = HTTPServer((cfg.doh.listen_on, cfg.doh.listen_port), DOHHandler)
        httpd.blocked_domains = adsblock.blocked_domains
        httpd.cache_enable = cfg.cache.enable
        httpd.cache_wip = cfg.cache.wip
        httpd.cache = cfg.cache.cache
        httpd.target_hostname = cfg.dns.target_hostname

        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(certfile="certs/cert.pem", keyfile="certs/key.pem")
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)

        logging.info(
            f"local doh server running on {cfg.doh.listen_on}:{cfg.doh.listen_port}"
        )
        httpd.serve_forever()

    except KeyboardInterrupt:
        server.shutdown()
        httpd.shutdown()

    # adapter.reset_dns(cfg.dns.interface_name)

    session.close()
    logging.info("sayonara!")


# ##############################################################################
# where it all begins


if __name__ == "__main__":
    main()
