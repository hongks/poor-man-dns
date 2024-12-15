import os
import threading
import time

import logging
from logging.handlers import TimedRotatingFileHandler

import cachetools
import psutil

from helpers.configs import Config
from helpers.adapter import Adapter
from helpers.adsblock import AdsBlock
from helpers.dns import DNSServer
from helpers.doh import DOHServer
from helpers.sqlite import SQLite


# ################################################################################
# sub routines


def setup_adapter(config):
    # connect the wifi and nice the process
    adapter = Adapter(config.adapter)
    p = psutil.Process(os.getpid())

    if adapter.supported_platform():
        if config.adapter.connect:
            adapter.connect()
            time.sleep(3)

        adapter.get_dns()
        p.nice(psutil.HIGH_PRIORITY_CLASS)

    else:
        p.nice(5)


def setup_cache(config):
    # set up the caching
    if config.cache.enable:
        config.cache.cache = cachetools.TTLCache(
            maxsize=config.cache.max_size, ttl=config.cache.ttl
        )

    logging.info(
        f"cache-enable: {str(config.cache.enable).lower()}"
        + f", max-size: {config.cache.max_size}, ttl: {config.cache.ttl}."
    )


def setup_logging(config):
    # set up logging
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    file_handler = TimedRotatingFileHandler(
        config.logging.filename, when="midnight", backupCount=3
    )
    file_handler.setLevel(logging.DEBUG)

    logging.basicConfig(
        format=config.logging.format,
        level=logging.getLevelName(config.logging.level),
        handlers=[console_handler, file_handler],
    )

    # ... and silent the others
    for logger in ["httpx", "httpcore", "urllib3", "watchdog"]:
        logging.getLogger(logger).setLevel(logging.WARNING)


# ################################################################################
# main routine


def main():
    config = Config()
    sqlite = SQLite(config.sqlite_uri)

    config.sync(sqlite.session)
    adsblock = AdsBlock(sqlite, config.adsblock.reload)

    setup_logging(config)
    logging.info("initialized!")

    setup_adapter(config)
    setup_cache(config)

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
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            threads.append(thread)

        # re-set up the list of domains to be blocked
        adsblock.load_blacklist(config.adsblock.blacklist)
        adsblock.load_custom(config.adsblock.custom)
        adsblock.load_whitelist(config.adsblock.whitelist)

        for server in [dns_server, doh_server]:
            server.blocked_domains = adsblock.blocked_domains

        while not event.is_set():
            time.sleep(1)

    except KeyboardInterrupt:
        logging.warning("ctrl-c pressed!")

    finally:
        for server in [dns_server, doh_server]:
            if server:
                server.shutdown()

        for thread in threads:
            thread.join()

        # adapter.reset_dns(cfg.dns.interface_name)
        sqlite.session.close()
        logging.info("sayonara!")


# ################################################################################
# where it all begins


if __name__ == "__main__":
    main()
