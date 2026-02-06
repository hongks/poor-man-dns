import asyncio
import io
import logging
import os
import time

from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

import click
import psutil

from .sqlite import SQLiteHandler


# ################################################################################
# typing annotations to avoid circular imports


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .adapter import Adapter
    from .config import Config
    from .sqlite import SQLite


# ################################################################################
# utilties


def echo(level: str, message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
    click.echo(f"{timestamp}  {level.upper():7}  root  main      {message}")


def setup_adapter(config: "Config", adapter: "Adapter"):
    # connect the wifi and nice the process
    process = psutil.Process(os.getpid())

    if adapter.is_platform_supported("windows"):
        if config.adapter.enable:
            adapter.set_dns()
            time.sleep(1)

            adapter.connect()
            time.sleep(3)

        adapter.get_dns()
        process.nice(psutil.HIGH_PRIORITY_CLASS)  # type: ignore[attr-defined]

    else:
        try:
            process.nice(-5)
        except psutil.AccessDenied:
            logging.warning(
                "unable to nice, access denied, possibly running under user privilege!",
            )
        except Exception as err:
            logging.exception(f"unexpected {err=}, {type(err)=}")


def setup_logging(config: "Config", sqlite: "SQLite"):
    # set up logging
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.stream = io.TextIOWrapper(
        console_handler.stream.detach(),  # type: ignore[attr-defined]
        encoding="utf-8",
        errors="replace",
        newline="\n",
    )

    logging.basicConfig(
        format=config.logging.format,
        level=getattr(logging, config.logging.level, logging.INFO),
        handlers=[
            console_handler,
            TimedRotatingFileHandler(
                config.logging.filename,
                when="midnight",
                backupCount=3,
                encoding="utf-8",
            ),
            SQLiteHandler(sqlite),
        ],
    )

    # ... and silent the others
    for logger in ["aiohttp", "asyncio", "httpcore", "httpx", "urllib3"]:
        logging.getLogger(logger).setLevel(logging.WARNING)

    # misc
    # logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)


async def wait_or_timeout(event: asyncio.Event, timeout: float) -> bool:
    # wait until the event is set, or until timeout expires.
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
        return True

    except asyncio.TimeoutError:
        return False
