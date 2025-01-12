import asyncio
import hashlib
import logging

from collections import OrderedDict
from datetime import datetime
from pathlib import Path

import jinja2

from aiohttp import web
from aiohttp_jinja2 import setup as setup_jinja2, render_template
from sqlalchemy import or_, func

from .configs import Config
from .sqlite import SQLite, AdsBlockDomain, AdsBlockList, Log, Setting


# load configurations
config = Config()
sqlite = SQLite(config)

# initialize aiohttp app
app = web.Application()
setup_jinja2(app, loader=jinja2.FileSystemLoader(f"{config.filepath}/app/templates"))

# static file handling
app.router.add_static("/static/", path=f"{config.filepath}/app/static", name="static")


async def config_handler(request):
    # config.xml
    config_file = {
        "lastmodified": None,
        "sha256": None,
        "data": None,
        "mismatched": False,
    }

    file = Path(config.filename)
    config_file["lastmodified"] = datetime.fromtimestamp(file.stat().st_mtime)

    with file.open("r") as f:
        config_file["data"] = "".join(f.readlines())

    sha256 = hashlib.sha256()
    with file.open("rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256.update(chunk)

    config_file["sha256"] = sha256.hexdigest()

    rows = sqlite.session.query(Setting).filter_by(key="config-sha256").first()
    if rows.value != config_file["sha256"]:
        config_file["mismatched"] = True

    # adsblock list
    rows = (
        sqlite.session.query(AdsBlockList)
        .order_by(AdsBlockList.updated_on.desc())
        .all()
    )
    adsblock_list = [
        {"url": row.url, "counts": row.count, "updated_on": row.updated_on}
        for row in rows
    ]

    return render_template(
        "config.html", request, {"config": config_file, "adsblock": adsblock_list}
    )


async def help_handler(request):
    return render_template("help.html", request, {})


async def home_handler(request):
    # get the latest log
    file = Path(config.logging.filename)

    logs = []
    if file.exists():
        with file.open("r") as f:
            logs = [line.strip() for line in f.readlines()]

    # services
    rows = (
        sqlite.session.query(Log)
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
    for row in rows:
        service = {
            "name": row.module,
            "started_on": row.updated_on,
            "listening_on": None,
        }

        listening_on = row.value.lower()
        if row.module == "main" and "cache-enable:" in listening_on:
            service["name"] = "cache"
            service["listening_on"] = listening_on
        else:
            service["listening_on"] = listening_on[listening_on.find(" on ") + 4 : -1]

        if service["name"] not in services:
            services[service["name"]] = service

    services = OrderedDict(sorted(services.items()))
    return render_template(
        "home.html", request, {"services": services, "logs": "\n".join(logs)}
    )


async def license_handler(request):
    return render_template("license.html", request, {})


async def query_handler(request):
    value = request.match_info.get("value")
    rows = None

    if value:
        rows = (
            sqlite.session.query(AdsBlockList)
            .filter(AdsBlockList.contents.ilike(f"%{value}%"))
            .order_by(AdsBlockList.updated_on.desc())
            .all()
        )
        rows = [row.url for row in rows]

    return web.json_response({"results": rows})


async def service_handler(request):
    return web.json_response({"message": "service handler not implemented!"})


async def stats_handler(request):
    buffers = OrderedDict(
        [
            ("forward", None),
            ("cache-hit", None),
            ("blacklisted", None),
            ("custom-hit", None),
        ]
    )

    for key in buffers.keys():
        buffers[key] = (
            sqlite.session.query(AdsBlockDomain)
            .filter_by(type=key)
            .order_by(AdsBlockDomain.count.desc())
            .limit(20)
            .all()
        )

    buffers["heatmap (utc)"] = (
        sqlite.session.query(
            func.date(AdsBlockDomain.updated_on).label("domain"),
            func.sum(AdsBlockDomain.count).label("count"),
        )
        .group_by(func.date(AdsBlockDomain.updated_on))
        .order_by(AdsBlockDomain.updated_on)
        .all()
    )

    return render_template("stats.html", request, {"buffers": buffers})


app.router.add_get("/config", config_handler)
app.router.add_get("/help", help_handler)
app.router.add_get("/", home_handler)
app.router.add_get("/home", home_handler)
app.router.add_get("/license", license_handler)
app.router.add_get("/query", query_handler)
app.router.add_get("/query/{value}", query_handler)
app.router.add_get("/service", service_handler)
app.router.add_get("/stats", stats_handler)


class WEBServer:
    def __init__(self, config, sqlite):
        self.enable = config.web.enable
        self.hostname = config.web.hostname
        self.port = config.web.port

        self.session = sqlite.session
        self.sqlite = sqlite

        self.debug = True if config.logging.level == logging.debug else False
        self.running = True
        self.runner = None

    async def close(self):
        self.running = False
        await self.runner.cleanup()

        logging.debug("local web server shutting down!")

    async def listen(self):
        if not self.enable:
            return

        self.runner = web.AppRunner(app)
        await self.runner.setup()

        site = web.TCPSite(self.runner, host=self.hostname, port=self.port)
        await site.start()

        logging.getLogger("aiohttp").setLevel(logging.WARNING)
        logging.info(f"local web server running on {self.hostname}:{self.port}.")

        while self.running:
            await asyncio.sleep(3600)
