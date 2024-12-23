import logging
import os
import threading

from pathlib import Path

import flask.cli

from flask import Flask, jsonify, request, render_template
from flask.logging import default_handler

from .configs import Config
from .dns import DNSServer
from .doh import DOHServer
from .sqlite import SQLite, Setting


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
    with file.open("r") as f:
        buffer = [line.strip() for line in f.readlines() if "running" in line]

    data = []
    for service in ["adapter", "cache", "dns", "doh", "web"]:
        srv = {"name": service, "started_on": "", "listening_on": "", "is_enabled": ""}
        data.append(srv)

    return render_template("home.html", data=data, buffer=buffer)


@app.route("/config")
def config():
    # todo: get the file content
    config = Config()
    file = Path(config.filename)
    with file.open("r") as f:
        data = f.readlines()

    sqlite = SQLite(config.sqlite_uri)
    row = sqlite.session.query(Setting).filter_by(key="config-sha256").first()
    cache = {row.key: row.value}

    row = sqlite.session.query(Setting).filter_by(key="blocked-domains").first()
    block = row.value

    return render_template("config.html", cache=cache, data="".join(data), block=block)


@app.route("/help")
def help():
    return render_template("help.html")


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
