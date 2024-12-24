import hashlib
import logging

from datetime import datetime
from pathlib import Path

import flask.cli

from flask import Flask, jsonify, request, render_template
from flask.logging import default_handler

from .configs import Config
from .dns import DNSServer
from .doh import DOHServer
from .sqlite import AdsBlockList, SQLite, Setting


# todo:
# [x] 1. to get the secret key and sqlite database from config class.
# [x] 2. to set the create config for debug environment in the config class, hidden.
#
# features:
# [ ] 1. show the dns and doh services running, with slide option to stop and start back
# [ ] 2. show the status for running services
# [x] 3. show the loaded config.xml
# [x] 4. show the loaded adsblock list and domains
# [x] 5. the config changes should be at the file. cache.sqlite is again just a cache!
# [ ] 6. able to view the cache.sqlite content for troubleshooting
#


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

# Global variables to hold server instances
dns_server_instance = None
doh_server_instance = None


@app.route("/")
@app.route("/home")
def home():
    config = Config()
    config.load()

    file = Path(config.logging.filename)

    buffer = []
    if file.exists():
        with file.open("r") as f:
            buffer = [line.strip() for line in f.readlines() if "running" in line]

    data = []
    for service in ["adapter", "cache", "dns", "doh", "web"]:
        srv = {"name": service, "started_on": "", "listening_on": "", "is_enabled": ""}
        data.append(srv)

    return render_template("home.html", data=data, buffer=buffer)


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


class WEBServer:
    def __init__(self, config):
        self.enable = config.web.enable
        self.hostname = config.web.hostname
        self.port = config.web.port

        self.debug = True if config.logging.level == logging.debug else False

    def serve_forever(self):
        if not self.enable:
            return

        flask.cli.show_server_banner = lambda *args: None
        app.logger.removeHandler(default_handler)

        logging.info(f"local web server running on {self.hostname}:{self.port}.")
        app.run(
            host=self.hostname, port=self.port, debug=self.debug, use_reloader=False
        )
