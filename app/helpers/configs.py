import hashlib
import socket

from datetime import datetime
from pathlib import Path

import yaml

from .sqlite import Setting


class Base:
    def __str__(self):
        return str([{i: f"{self.__dict__[i]}"} for i in self.__dict__])


# default configs, overide as needed
class Config(Base):
    class AdsBlock(Base):
        def __init__(self):
            self.blacklist = (["https://v.firebog.net/hosts/easyprivacy.txt"],)
            self.custom = ()
            self.reload = False
            self.whitelist = ()

    class Cache(Base):
        def __init__(self):
            self.cache = None
            self.enable = True
            self.max_size = 1000
            self.ttl = 180
            self.wip = set()

    class DNS(Base):
        def __init__(self):
            self.hostname = "127.0.0.1"
            self.port = 53

            self.interface = "wi-fi"
            self.target_doh = ["https://1.1.1.1/dns-query"]
            self.target_mode = "dns-message"

            self.custom = {
                "1.0.0.127.in-addr.arpa.": "127.0.0.1",
                "localhost.": "127.0.0.1",
                f"{socket.gethostname().lower()}.": "127.0.0.1",
            }

    class DOH(Base):
        def __init__(self):
            self.hostname = "0.0.0.0"
            self.port = 5053

    class Logging(Base):
        def __init__(self):
            self.filename = "poor-man-dns.log"
            self.format = "[%(asctime)s] %(levelname)s in %(module)s: %(message)s"
            self.level = "INFO"

    def __init__(self):
        self.filename = "config.yml"
        self.secret_key = "the-quick-brown-fox-jumps-over-the-lazy-dog!"
        self.sqlite_uri = "sqlite:///cache.sqlite"

        self.adsblock = self.AdsBlock()
        self.cache = self.Cache()
        self.dns = self.DNS()
        self.doh = self.DOH()
        self.logging = self.Logging()

    # override default configs
    def load(self):
        if not Path(self.filename).exists():
            return None

        sha256 = hashlib.sha256()
        with open(self.filename, "rb") as f:
            while True:
                chunk = f.read(1000000)  # 1MB
                if not chunk:
                    break
                sha256.update(chunk)

        with open(self.filename, "r") as f:
            configs = yaml.load(f, Loader=yaml.loader.SafeLoader)

            self.adsblock.blacklist = sorted(configs["adblock"]["blacklist"])
            self.adsblock.custom = set(configs["adblock"]["custom"])
            self.adsblock.reload = configs["adblock"]["reload"]
            self.adsblock.whitelist = set(configs["adblock"]["whitelist"])

            self.cache.enable = configs["cache"]["enable"]
            self.cache.max_size = configs["cache"]["max_size"]
            self.cache.ttl = configs["cache"]["ttl"]

            self.dns.hostname = configs["dns"]["hostname"]
            self.dns.port = configs["dns"]["port"]
            self.dns.interface = configs["dns"]["interface"]
            self.dns.target_doh = configs["dns"]["target_doh"]
            self.dns.target_mode = configs["dns"]["target_mode"]

            buffers = {
                "1.0.0.127.in-addr.arpa.": "127.0.0.1",
                "localhost.": "127.0.0.1",
                f"{socket.gethostname().lower()}.": "127.0.0.1",
            }
            for item in configs["dns"]["custom"]:
                key, value = item.split(":")
                buffers[f"{key.lower()}."] = value

            self.dns.custom = [{key: value} for key, value in sorted(buffers.items())]

            self.doh.hostname = configs["doh"]["hostname"]
            self.doh.port = configs["doh"]["port"]

            self.logging.level = configs["logging"]["level"].upper()

        return sha256.hexdigest()

    # in case config file is different
    def sync(self, session):
        row = session.query(Setting).filter_by(key="config-sha256").first()
        dt = datetime.utcnow()

        sha256 = self.load()
        if row and sha256:
            if sha256 != row.value:
                row.value = sha256
                row.updated_on = dt

        else:
            row = Setting(
                key="config-sha256",
                value=sha256,
                created_on=dt,
                updated_on=dt,
            )
            session.add(row)

        session.commit()
