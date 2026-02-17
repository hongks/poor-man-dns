import asyncio
import gc
import logging
import shutil
import time

from datetime import datetime
from pathlib import Path
from typing import Any

import click

from helpers.adapter import Adapter
from helpers.config import Config, ConfigSelectorPolicy
from helpers.sqlite import SQLite
from helpers.utility import echo, setup_adapter, setup_logging

from helpers.adsblock import ADSServer
from helpers.ddns import DDNSServer
from helpers.web import WEBServer

from servers.dns import DNSServer
from servers.doh import DOHServer
from servers.dot import DOTServer


# ################################################################################
# service routines


async def service(
    config: Config,
    sqlite: SQLite,
    *,
    ddns: bool,
    dns: bool,
    doh: bool,
    dot: bool,
    web: bool,
):
    servers: list[Any] = [sqlite]

    ads = ADSServer(config, sqlite)
    if dns or doh or dot:
        servers.append(ads)

    if dns:
        servers.append(DNSServer(config, sqlite, ads))

    if doh:
        servers.append(DOHServer(config, sqlite, ads))

    if dot:
        servers.append(DOTServer(config, sqlite, ads))

    if web:
        servers.append(WEBServer(config, sqlite))

    if ddns:
        servers.append(DDNSServer(config, sqlite))

    try:
        tasks = [asyncio.create_task(server.listen()) for server in servers]
        await asyncio.sleep(9)

        logging.info("press ctrl+c to quit!")
        gc.collect()
        await asyncio.gather(*tasks, return_exceptions=True)

    except asyncio.CancelledError:
        logging.debug("service tasks cancelled!")
        raise

    except Exception as err:
        logging.exception(f"unexpected {err=}, {type(err)=}")

    finally:
        for server in reversed(servers):
            if server:
                await server.close()


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
    "--dot",
    "-t",
    is_flag=True,
    help="start up the dot server.",
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
def main(
    adapter: bool,
    ddns: bool,
    dns: bool,
    doh: bool,
    dot: bool,
    web: bool,
    generate: bool,
    debug: bool,
    reset: bool,
    version: bool,
):
    """poor-man-dns: a simple lightweight ddns, dns, doh, and dot server"""

    config = Config()
    if version:  # ###############################################################
        click.echo(f"version {config.version}")
        return

    if reset:  # #################################################################
        echo("info", "resetting, removing caches and logs ...")
        tic = time.time()

        Path("./run").mkdir(parents=True, exist_ok=True)
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

        Path("./run").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(config.template, config.filename)
        logging.info("skeleton config file generated!")
        return

    if any((dns, doh, dot)):  # ##################################################
        _adapter = Adapter(config.adapter)
        setup_adapter(config, _adapter)

    else:
        _adapter = Adapter(config.adapter)
        setup_adapter(config, _adapter)
        if adapter:
            return

        # if all services are unset, set them all
        adapter = ddns = dns = doh = dot = web = True

    asyncio.set_event_loop_policy(ConfigSelectorPolicy())
    try:
        asyncio.run(
            service(config, sqlite, ddns=ddns, dns=dns, doh=doh, dot=dot, web=web),
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
