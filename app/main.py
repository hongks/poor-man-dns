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
    await adsblock.setup()  # use cache first

    dns_server = DNSServer(config, sqlite, adsblock.blocked_domains)
    doh_server = DOHServer(config, sqlite, adsblock.blocked_domains)
    web_server = WEBServer(config, sqlite)
    config_server = ConfigServer(config, sqlite, [dns_server, doh_server])

    servers = [sqlite, dns_server, doh_server, web_server, config_server]
    tasks = [asyncio.create_task(server.listen()) for server in servers]

    sqlite.purge()
    await asyncio.sleep(1)
    await adsblock.setup(reload=True)

    logging.info("press ctrl+c to quit!")

    try:
        await asyncio.gather(*tasks, return_exceptions=True)

    except asyncio.CancelledError:
        logging.debug("service tasks cancelled!")

    except Exception as err:
        logging.exception(f"unexpected {err=}, {type(err)=}")

    finally:
        for server in servers:
            if server:
                await server.close()


# ################################################################################
# sub routines


def setup_adapter(config, adapter):
    # connect the wifi and nice the process
    process = psutil.Process(os.getpid())

    if adapter.supported_platform():
        if config.adapter.enable:
            adapter.set_dns()
            time.sleep(1)

            adapter.connect()
            time.sleep(3)

        adapter.get_dns()
        process.nice(psutil.HIGH_PRIORITY_CLASS)

    else:
        try:
            process.nice(-5)
        except psutil.AccessDenied:
            logging.warning("access denied, possibly running as user privilege!")


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
        level=getattr(logging, config.logging.level, logging.INFO),
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
    logging.info("initialized!")

    adapter = Adapter(config.adapter)
    setup_adapter(config, adapter)
    setup_cache(config)

    asyncio.set_event_loop_policy(ConfigSelectorPolicy())
    try:
        asyncio.run(service(config, sqlite, adsblock), debug=False)

    except KeyboardInterrupt:
        logging.info("ctrl-c pressed!")

    except Exception as err:
        logging.exception(f"unexpected {err=}, {type(err)=}")

    finally:
        if adapter.supported_platform():
            if config.adapter.enable and config.adapter.reset_on_exit:
                adapter.reset_dns()

        logging.info("sayonara!")


# ################################################################################
# where it all begins


if __name__ == "__main__":
    main()
