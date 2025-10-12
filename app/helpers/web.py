import asyncio
import hashlib
import logging
import re
import ssl

from collections import OrderedDict
from datetime import datetime
from html import escape
from pathlib import Path

import jinja2

from aiohttp import web
from aiohttp_jinja2 import setup as setup_jinja2, render_template
from sqlalchemy import or_, func

from .sqlite import AdsBlockDomain, AdsBlockList, Log, Setting


# ################################################################################
# typing annotations to avoid circular imports


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from helpers.config import Config
    from helpers.sqlite import SQLite


# ################################################################################
# aiohttp


# define typed app keys
CONFIG_KEY: str = web.AppKey("config")
SQLITE_KEY: str = web.AppKey("sqlite")


# helper
def compute_file_sha256(path: str | Path) -> str:
    sha256 = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256.update(chunk)

    return sha256.hexdigest()


async def config_handler(request: web.Request):
    config = request.app[CONFIG_KEY]
    sqlite = request.app[SQLITE_KEY]

    # config.xml
    config_file = {
        "lastmodified": None,
        "sha256": None,
        "data": None,
        "mismatched": False,
    }

    file = Path(config.filename).resolve()
    if file.exists():
        config_file["lastmodified"] = datetime.fromtimestamp(file.stat().st_mtime)
        config_file["data"] = file.read_text()
        config_file["sha256"] = compute_file_sha256(file)

    row = sqlite.Session().query(Setting).filter_by(key="config-sha256").first()
    if row and row.value != config_file["sha256"]:
        config_file["mismatched"] = True

    # adsblock list
    adsblock_list = [
        {
            "url": row.url,
            "count": row.count,
            "status": row.status,
            "updated_on": row.updated_on,
        }
        for row in sqlite.Session()
        .query(AdsBlockList)
        .order_by(AdsBlockList.updated_on.desc())
        .all()
    ]

    return render_template(
        "config.html", request, {"config": config_file, "adsblock": adsblock_list}
    )


async def help_handler(request):
    config = request.app[CONFIG_KEY]

    file = Path(config.template)
    configs = escape(file.read_text()) if file.exists() else ""

    return render_template("help.html", request, {"configs": configs})


async def home_handler(request):
    config = request.app[CONFIG_KEY]
    sqlite = request.app[SQLITE_KEY]

    # get the latest log
    file = Path(config.logging.filename)
    logs = file.read_text().splitlines() if file.exists() else []

    # services
    rows = (
        sqlite.Session()
        .query(Log)
        .filter(
            or_(
                Log.value.ilike("% running on %"),
                Log.value.ilike("%cache-enable:%"),
            )
        )
        .order_by(Log.updated_on.desc())
        .all()
    )

    services = {}
    pattern = re.compile(r" on (.+)\.")

    for row in rows:
        name = "cache" if "cache-enable:" in row.value else row.module
        match = pattern.search(row.value.lower())
        listening = match.group(1) if match else None

        if name not in services:
            services[name] = {
                "name": name,
                "started_on": row.updated_on,
                "listening_on": listening,
            }

    return render_template(
        "home.html",
        request,
        {
            "services": dict(sorted(services.items())),
            "logs": "\n".join(escape(line) for line in logs),
        },
    )


async def license_handler(request):
    return render_template("license.html", request, {})


async def query_handler(request):
    sqlite = request.app[SQLITE_KEY]
    value = request.match_info.get("value")
    rows = None

    if value:
        rows = (
            sqlite.Session()
            .query(AdsBlockList.url)
            .filter(AdsBlockList.contents.ilike(f"%{value}%"))
            .order_by(AdsBlockList.updated_on.desc())
            .all()
        )

    return web.json_response({"results": [row.url for row in rows]})


async def service_handler(request):
    return web.json_response({"message": "service handler not implemented!"})


async def stats_handler(request):
    sqlite = request.app[SQLITE_KEY]

    buffers = OrderedDict(
        [
            ("upstream", None),
            ("cache-hit", None),
            ("blacklisted", None),
            ("forward", None),
            ("custom-hit", None),
        ]
    )

    for key in buffers.keys():
        buffers[key] = (
            sqlite.Session()
            .query(AdsBlockDomain)
            .filter_by(type=key)
            .order_by(AdsBlockDomain.count.desc())
            .limit(30)
            .all()
        )

    buffers["heatmap (utc)"] = (
        sqlite.Session()
        .query(
            func.date(AdsBlockDomain.updated_on).label("domain"),
            func.sum(AdsBlockDomain.count).label("count"),
        )
        .group_by(func.date(AdsBlockDomain.updated_on))
        .order_by(AdsBlockDomain.updated_on.desc())
        .all()
    )
    buffers.move_to_end("heatmap (utc)", last=False)

    return render_template("stats.html", request, {"buffers": buffers})


def create_app(config: "Config", sqlite: "SQLite") -> web.Application:
    # initialize aiohttp app
    app = web.Application()
    app[CONFIG_KEY] = config
    app[SQLITE_KEY] = sqlite

    setup_jinja2(
        app,
        autoescape=True,
        loader=jinja2.FileSystemLoader(f"{config.filepath}/app/templates"),
    )

    # static file handling
    app.router.add_static(
        "/static/", path=f"{config.filepath}/app/static", name="static"
    )

    routes = [
        ("/config", config_handler),
        ("/help", help_handler),
        ("/", home_handler),
        ("/home", home_handler),
        ("/license", license_handler),
        ("/query", query_handler),
        ("/query/{value}", query_handler),
        ("/service", service_handler),
        ("/stats", stats_handler),
    ]

    for path, handler in routes:
        app.router.add_get(path, handler)

    return app


class WEBServer:
    def __init__(self, config: "Config", sqlite: "SQLite"):
        self.config = config
        self.sqlite = sqlite

        self.enable = config.web.enable
        self.hostname = config.web.hostname
        self.port = config.web.port

        self.debug = True if config.logging.level == logging.debug else False
        self.runner = None

        self.app = create_app(self.config, self.sqlite)
        self.shutdown_event = asyncio.Event()

        self.ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self.ssl_context.load_cert_chain(
            certfile=config.ssl.certfile, keyfile=config.ssl.keyfile
        )

    async def close(self):
        self.shutdown_event.set()

        if self.runner:
            await self.runner.cleanup()

        logging.info("local service shutting down!")

    async def listen(self):
        if not self.enable:
            return

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        site = web.TCPSite(
            self.runner,
            host=self.hostname,
            port=self.port,
            ssl_context=self.ssl_context,
        )
        await site.start()

        logging.info(f"local service running on {self.hostname}:{self.port}.")
        await self.shutdown_event.wait()
