import logging
import os
import threading
import time

import cachetools
import psutil

from helpers.configs import Config
from helpers.adapter import Adapter
from helpers.adsblock import AdsBlock
from helpers.dns import DNSServer
from helpers.doh import DOHServer
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

    # connect the wifi and nice the process
    p = psutil.Process(os.getpid())
    adapter = Adapter(config.adapter)

    if adapter.supported_platform():
        if config.adapter.connect:
            adapter.connect()
            time.sleep(3)

        adapter.get_dns()
        p.nice(psutil.HIGH_PRIORITY_CLASS)

    else:
        p.nice(5)

    # set up the caching
    if config.cache.enable:
        config.cache.cache = cachetools.TTLCache(
            maxsize=config.cache.max_size, ttl=config.cache.ttl
        )

    logging.info(
        f"cache-enable: {str(config.cache.enable).lower()}"
        + f", max-size: {config.cache.max_size}, ttl: {config.cache.ttl}."
    )

    # set up the threading
    event = threading.Event()
    threads = []

    try:
        # load cache first!
        adsblock.load_cache()

        dns_server = DNSServer(config.cache, config.dns, adsblock.blocked_domains)
        doh_server = DOHServer(
            config.cache,
            config.dns,
            config.doh,
            config.filepath,
            adsblock.blocked_domains,
        )

        for server in [dns_server, doh_server]:
            thread = threading.Thread(target=server.serve_forever)
            thread.daemon = True
            thread.start()

            threads.append(thread)

        # re-set up the list of domains to be blocked
        adsblock.load_blacklist(config.adsblock.blacklist)
        adsblock.load_custom(config.adsblock.custom)
        adsblock.load_whitelist(config.adsblock.whitelist)

        for server in [dns_server, doh_server]:
            server.blocked_domains = adsblock.blocked_domains

        while True:
            pass

    except KeyboardInterrupt:
        event.set()
        for server in [dns_server, doh_server]:
            server.shutdown()

        logging.error("ctrl-c pressed!")

    # adapter.reset_dns(cfg.dns.interface_name)
    sqlite.session.close()
    logging.info("sayonara!")


# ################################################################################
# where it all begins


if __name__ == "__main__":
    main()
