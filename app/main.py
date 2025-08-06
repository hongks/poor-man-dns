import asyncio
import logging
import os
import shutil
import time

from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import click
import psutil

from helpers.adsblock import ADSServer
from helpers.ddns import DDNSServer
from helpers.dns import DNSServer
from helpers.doh import DOHServer
from helpers.web import WEBServer

from helpers.adapter import Adapter
from helpers.configs import Config, ConfigSelectorPolicy
from helpers.sqlite import SQLite, SQLiteHandler


# ################################################################################
# service routines


async def service(config, sqlite, *, ddns, dns, doh, web):
    servers = [sqlite]

    if web:
        servers.append(WEBServer(config, sqlite))

    if ddns:
        servers.append(DDNSServer(config, sqlite))

    ads_server = ADSServer(config, sqlite)
    if dns or doh:
        servers.append(ads_server)

    if dns:
        servers.append(DNSServer(config, sqlite, ads_server))

    if doh:
        servers.append(DOHServer(config, sqlite, ads_server))

    try:
        tasks = [asyncio.create_task(server.listen()) for server in servers]
        await asyncio.sleep(5)
        logging.info("press ctrl+c to quit!")

        await asyncio.gather(*tasks, return_exceptions=True)

    except asyncio.CancelledError:
        logging.debug("service tasks cancelled!")

    except Exception as err:
        logging.exception(f"unexpected {err=}, {type(err)=}")

    finally:
        for server in reversed(servers):
            if server:
                await server.close()


# ################################################################################
# sub routines


def echo(level, message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
    click.echo(f"{timestamp} | {level.upper():7} | main: {message}")


def setup_adapter(config, adapter):
    # connect the wifi and nice the process
    process = psutil.Process(os.getpid())

    if adapter.is_platform_supported():
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
            logging.warning(
                "unable to nice, access denied, possibly running under user privilege!",
            )
        except Exception as err:
            logging.exception(f"unexpected {err=}, {type(err)=}")


def setup_logging(config, sqlite):
    # set up logging
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    logging.basicConfig(
        format=config.logging.format,
        level=getattr(logging, config.logging.level, logging.INFO),
        handlers=[
            console_handler,
            TimedRotatingFileHandler(
                config.logging.filename, when="midnight", backupCount=3
            ),
            SQLiteHandler(sqlite),
        ],
    )

    # ... and silent the others
    for logger in ["httpcore", "httpx", "paramiko", "urllib3", "watchdog", "werkzeug"]:
        logging.getLogger(logger).setLevel(logging.WARNING)

    # misc
    # logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)


# ################################################################################
# main routine


@click.command()
@click.option(
    "--adapter",
    "-a",
    is_flag=True,
    help="get computer adapter status.",
)
@click.option(
    "--ddns",
    "-n",
    is_flag=True,
    help="register ipv4 to configured ddns provider.",
)
@click.option(
    "--dns",
    "-s",
    is_flag=True,
    help="start up the dns server.",
)
@click.option(
    "--doh",
    "-o",
    is_flag=True,
    help="start up the doh server.",
)
@click.option(
    "--web",
    "-w",
    is_flag=True,
    help="start up the experimental web server.",
)
@click.option(
    "--generate",
    "-g",
    is_flag=True,
    help="generate skeleton config.xml.",
)
@click.option(
    "--debug",
    "-d",
    is_flag=True,
    help="enable debug mode, overriding the debug level in config.xml.",
)
@click.option(
    "--reset",
    "-e",
    is_flag=True,
    help="remove caches and logs in the run folder.",
)
@click.option(
    "--version",
    "-v",
    is_flag=True,
    help="show the version and exit.",
)
@click.help_option(
    "--help",
    "-h",
    help="show this message and exit.",
)
def main(adapter, ddns, dns, doh, web, generate, debug, reset, version):
    """poor-man-dns: a simple, lightweight ddns, dns and doh server"""

    config = Config()
    if version:  # ###############################################################
        click.echo(f"version {config.version}")
        return

    if reset:  # #################################################################
        echo("info", "resetting, removing caches and logs ...")
        tic = time.time()

        Path("./run").mkdir(exist_ok=True)
        for pattern in ["cache.sqlite*", "poor-man-dns.log*"]:
            for file in Path("./run").glob(pattern):
                if file.exists():
                    file.unlink()
                    echo("info", f"- deleted: {file}")

        echo("info", f"... done, reset in {time.time() - tic:.3f}s!")
        return

    sqlite = SQLite(config)
    config.sync(sqlite.Session())

    if debug:  # #################################################################
        config.logging.level = "DEBUG"
        echo("info", "debug mode on!")

    setup_logging(config, sqlite)
    logging.info("initialized!")
    sqlite.purge()

    if generate:  # ##############################################################
        file = Path(config.filename)
        if file.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file.rename(f"./run/config_{timestamp}.yml")
            logging.info(f"existing config file renamed to config_{timestamp}.yml")

        Path("./run").mkdir(exist_ok=True)
        shutil.copyfile(config.template, config.filename)
        logging.info("skeleton config file generated!")
        return

    services = (ddns, dns, doh, web)
    _adapter = Adapter(config.adapter)

    if adapter or not any(services):  # ##############################################
        setup_adapter(config, _adapter)
        if adapter:
            return

    # If any service is unset, enable them all
    if not any(services):
        ddns = dns = doh = web = True

    asyncio.set_event_loop_policy(ConfigSelectorPolicy())
    try:
        asyncio.run(
            service(config, sqlite, ddns=ddns, dns=dns, doh=doh, web=web),
            debug=False,
        )

    except KeyboardInterrupt:
        logging.info("ctrl-c pressed!")

    except Exception as err:
        logging.exception(f"unexpected {err=}, {type(err)=}")

    finally:
        if config.adapter.enable and _adapter and _adapter.is_platform_supported():
            if config.adapter.reset_on_exit:
                _adapter.reset_dns()

    logging.info("sayonara!")


# ################################################################################
# where it all begins


if __name__ == "__main__":
    main()
