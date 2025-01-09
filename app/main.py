import asyncio
import os
import logging
import time

from logging.handlers import TimedRotatingFileHandler

import cachetools
import psutil

from helpers.configs import Config, ConfigSelectorPolicy, ConfigServer
from helpers.adapter import Adapter
from helpers.adsblock import AdsBlock
from helpers.dns import DNSServer
from helpers.doh import DOHServer
from helpers.web import WEBServer
from helpers.sqlite import SQLite, SQLiteHandler


# ################################################################################
# service routines


async def service(config, sqlite, adsblock):
    await sqlite.purge()
    await adsblock.setup()  # use cache first

    dns_server = DNSServer(config, sqlite, adsblock.blocked_domains)
    doh_server = DOHServer(config, sqlite, adsblock.blocked_domains)
    web_server = WEBServer(config, sqlite)
    config_server = ConfigServer(config, sqlite, [dns_server, doh_server])

    servers = [sqlite, dns_server, doh_server, web_server, config_server]
    tasks = []

    for server in servers:
        tasks.append(asyncio.create_task(server.listen()))

    await asyncio.sleep(1)
    await adsblock.setup(reload=True)

    logging.info("press ctrl+c to quit!")

    try:
        await asyncio.gather(*tasks, return_exceptions=True)

    except Exception as err:
        logging.error(f"unexpected {err=}, {type(err)=}")

    finally:
        for server in servers:
            if server:
                await server.close()


# ################################################################################
# sub routines


def setup_adapter(config):
    # connect the wifi and nice the process
    adapter = Adapter(config.adapter)
    p = psutil.Process(os.getpid())

    if adapter.supported_platform():
        if config.adapter.enable:
            adapter.connect()
            time.sleep(3)

        adapter.get_dns()
        p.nice(psutil.HIGH_PRIORITY_CLASS)

    else:
        p.nice(5)


def setup_cache(config, sqlite):
    # set up the caching
    if config.cache.enable:
        config.cache.cache = cachetools.TTLCache(
            maxsize=config.cache.max_size, ttl=config.cache.ttl
        )

    logging.info(
        f"cache-enable: {str(config.cache.enable).lower()}"
        + f", max-size: {config.cache.max_size}, ttl: {config.cache.ttl}."
    )


def setup_logging(config, sqlite):
    # set up logging
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    file_handler = TimedRotatingFileHandler(
        config.logging.filename, when="midnight", backupCount=3
    )

    # log to sqlite
    sqlite_handler = SQLiteHandler(sqlite)

    logging.basicConfig(
        format=config.logging.format,
        level=logging.getLevelName(config.logging.level),
        handlers=[console_handler, file_handler, sqlite_handler],
    )

    # ... and silent the others
    for logger in ["httpx", "httpcore", "urllib3", "werkzeug", "watchdog"]:
        logging.getLogger(logger).setLevel(logging.WARNING)

    # misc
    # logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)


# ################################################################################
# main routine


def main():
    config = Config()
    sqlite = SQLite(config)
    config.sync(sqlite.session)

    adsblock = AdsBlock(config, sqlite)
    setup_logging(config, sqlite)

    asyncio.set_event_loop_policy(ConfigSelectorPolicy())
    logging.info("initialized!")

    setup_adapter(config)
    setup_cache(config, sqlite)

    try:
        asyncio.run(service(config, sqlite, adsblock), debug=False)

    except KeyboardInterrupt:
        logging.info("ctrl-c pressed!")

    except Exception as err:
        logging.error(f"unexpected {err=}, {type(err)=}")

    finally:
        # adapter.reset_dns(cfg.dns.interface_name)
        logging.info("sayonara!")


# ################################################################################
# where it all begins


if __name__ == "__main__":
    main()
