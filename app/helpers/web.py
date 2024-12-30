import hashlib
import logging

from collections import OrderedDict
from datetime import datetime
from pathlib import Path

import flask.cli

from flask import Flask, jsonify, render_template
from flask.logging import default_handler
from sqlalchemy import or_

from .configs import Config
from .dns import DNSServer
from .doh import DOHServer
from .models import AdsBlockList, AdsBlockLog, Setting
from .sqlite import SQLite


config = Config()

app = Flask(
    __name__,
    static_folder=f"{config.filepath}/app/static/",
    template_folder=f"{config.filepath}/app/templates/",
)
app.config.from_mapping(
    SECRET_KEY=config.secret_key,
    SQLALCHEMY_ECHO=config.sqlite.echo,
    SQLALCHEMY_DATABASE_URI=config.sqlite.uri,
    SQLALCHEMY_TRACK_MODIFICATIONS=config.sqlite.track_modifications,
)


@app.route("/config")
def config():
    config = Config()
    sqlite = SQLite(config.sqlite.uri)

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

    row = sqlite.session.query(Setting).filter_by(key="config-sha256").first()
    if row.value != config_file["sha256"]:
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

    return render_template("config.html", config=config_file, adsblock=adsblock_list)


@app.route("/help")
def help():
    return render_template("help.html")


@app.route("/")
@app.route("/home")
def home():
    config = Config()
    sqlite = SQLite(config.sqlite.uri)

    # get the latest log
    file = Path(config.logging.filename)

    logs = []
    if file.exists():
        with file.open("r") as f:
            logs = [line.strip() for line in f.readlines()]

    # services
    rows = (
        sqlite.session.query(AdsBlockLog)
        .filter(
            or_(
                AdsBlockLog.value.ilike("% running on %"),
                AdsBlockLog.value.ilike("%cache-enable:%"),
            )
        )
        .order_by(AdsBlockLog.updated_on.desc())
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
    return render_template("home.html", services=services, logs="\n".join(logs))


@app.route("/license")
def license():
    return render_template("license.html")


@app.route("/query", defaults={"value": None})
@app.route("/query/<string:value>")
def query(value):
    config = Config()
    sqlite = SQLite(config.sqlite.uri)

    rows = None
    if value:
        rows = (
            sqlite.session.query(AdsBlockList)
            .filter(AdsBlockList.contents.ilike(f"%{value}%"))
            .order_by(AdsBlockList.updated_on.desc())
            .all()
        )
        rows = [row.url for row in rows]

    return jsonify({"results": rows})


@app.route("/service", defaults={"name": None, "state": None})
@app.route("/service/<string:name>/string:state")
def service(name, state):
    pass


class WEBServer:
    def __init__(self, config, sqlite):
        self.enable = config.web.enable
        self.hostname = config.web.hostname
        self.port = config.web.port

        self.session = sqlite.session
        self.sqlite = sqlite

        self.debug = True if config.logging.level == logging.debug else False

    def serve_forever(self):
        if not self.enable:
            return

        app.logger.removeHandler(default_handler)
        flask.cli.show_server_banner = lambda *args: None

        logging.info(f"local web server running on {self.hostname}:{self.port}.")
        app.run(host=self.hostname, port=self.port, debug=False, use_reloader=False)

    def shutdown(self):
        pass
