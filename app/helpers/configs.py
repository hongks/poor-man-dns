import hashlib

from pathlib import Path

import yaml


# default configs, overide as needed
class Config:
    class AdsBlock:
        def __init__(self):
            self.custom = []
            self.list_uri = (["https://v.firebog.net/hosts/easyprivacy.txt"],)
            self.reload = False
            self.whitelist = []

        def __str__(self):
            return str([{i: f"{self.__dict__[i]}"} for i in self.__dict__])

    class Cache:
        def __init__(self):
            self.cache = None
            self.enable = True
            self.max_size = 1000
            self.ttl = 180
            self.wip = set()

        def __str__(self):
            return str([{i: f"{self.__dict__[i]}"} for i in self.__dict__])

    class DNS:
        def __init__(self):
            self.listen_on = "127.0.0.1"
            self.listen_port = 53

            self.interface_name = "wi-fi"
            self.target_hostname = ["https://1.1.1.1/dns-query"]

        def __str__(self):
            return str([{i: f"{self.__dict__[i]}"} for i in self.__dict__])

    class DOH:
        def __init__(self):
            self.listen_on = "0.0.0.0"
            self.listen_port = 5053

    class Logging:
        def __init__(self):
            self.filename = "poor-man-dns.log"
            self.format = "[%(asctime)s] %(levelname)s in %(module)s: %(message)s"
            self.level = "INFO"

        def __str__(self):
            return str([{i: f"{self.__dict__[i]}"} for i in self.__dict__])

    def __init__(self):
        self.filename = "config.yml"
        self.secret_key = "the-quick-brown-fox-jumps-over-the-lazy-dog!"
        self.sqlite_uri = "sqlite:///cache.sqlite"

        self.adsblock = self.AdsBlock()
        self.cache = self.Cache()
        self.dns = self.DNS()
        self.doh = self.DOH()
        self.logging = self.Logging()

    def __str__(self):
        return str([{i: f"{self.__dict__[i]}"} for i in self.__dict__])

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

            self.adsblock.custom = configs["adblock"]["custom"]
            self.adsblock.list_uri = configs["adblock"]["list_uri"]
            self.adsblock.reload = configs["adblock"]["reload"]
            self.adsblock.whitelist = configs["adblock"]["whitelist"]

            self.cache.enable = configs["cache"]["enable"]
            self.cache.max_size = configs["cache"]["max_size"]
            self.cache.ttl = configs["cache"]["ttl"]

            self.dns.listen_on = configs["dns"]["listen_on"]
            self.dns.listen_port = configs["dns"]["listen_port"]
            self.dns.interface_name = configs["dns"]["interface_name"]
            self.dns.target_hostname = configs["dns"]["target_hostname"]

            self.doh.listen_on = configs["doh"]["listen_on"]
            self.doh.listen_port = configs["doh"]["listen_port"]

            self.logging.level = configs["logging"]["level"].upper()

        return sha256.hexdigest()
