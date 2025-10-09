import hashlib
import logging
import socket

from asyncio import DefaultEventLoopPolicy, SelectorEventLoop
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from selectors import SelectSelector
from typing import Any, TypeVar

import yaml

from .sqlite import Setting

T = TypeVar("T")


# ################################################################################
# overrides


class ConfigSelectorPolicy(DefaultEventLoopPolicy):
    def new_event_loop(self):
        selector = SelectSelector()
        return SelectorEventLoop(selector)


# ################################################################################
# default configs, override as needed


@dataclass
class Config:
    @dataclass
    class Adapter:
        enable: bool = False
        reset_on_exit: bool = False

        interface: str = "wi-fi"
        ssid: str = "default"

    @dataclass
    class AdsBlock:
        reload: bool = False
        custom: set[str] = field(default_factory=set)
        whitelist: set[str] = field(default_factory=set)

        blacklist: list[str] = field(
            default_factory=lambda: ["https://v.firebog.net/hosts/easyprivacy.txt"]
        )

    @dataclass
    class Base:
        custom: dict[str, str] = field(
            default_factory=lambda: {
                "1.0.0.127.in-addr.arpa.": "localhost.",
                "localhost.": "127.0.0.1",
                f"{socket.gethostname().lower()}.": "127.0.0.1",
            }
        )
        forward: set[str] = field(default_factory=set)
        target_mode: str = "dns-message"  # or "dns-json"
        target_doh: list[str] = field(
            default_factory=lambda: ["https://1.1.1.1/dns-query"]
        )

    @dataclass
    class Cache:
        enable: bool = True
        max_size: int = 1000
        ttl: int = 600

    @dataclass
    class DDNS:
        enable: bool = False
        api_key: str | None = None
        interval: int = 60
        provider: str = "dynu"

        domain_id: int | None = None
        domain_name: str | None = None

    @dataclass
    class DNS:
        hostname: str = "127.0.0.1"
        port: int = 53

    @dataclass
    class DOH:
        hostname: str = "127.0.0.1"
        port: int = 5053

    @dataclass
    class DOT:
        hostname: str = "127.0.0.1"
        port: int = 853

    @dataclass
    class Logging:
        level: str = "INFO"
        retention: int = 7

        filename: str = "./run/poor-man-dns.log"
        format: str = (
            "%(asctime)s  %(levelname)-7s  %(name)-4s  %(module)-8s  %(message)s"
        )

        def __post_init__(self):
            self.level = str(self.level).upper()

    @dataclass
    class SQLite:
        echo: bool = False
        track_modifications: bool = False
        uri: str = "sqlite:///./run/cache.sqlite"

    @dataclass
    class SSL:
        certfile: str = "certs/cert.pem"
        keyfile: str = "certs/key.pem"

    @dataclass
    class Web:
        enable: bool = True
        hostname: str = "127.0.0.1"
        port: int = 5000

    filepath: str = "."
    filename: str = "./run/config.yml"
    secret_key: str = "the-quick-brown-fox-jumps-over-the-lazy-dog!"
    template: str = "./app/templates/config.yml"
    version: str = "1.9.0"

    adapter: Adapter = field(default_factory=Adapter)
    adsblock: AdsBlock = field(default_factory=AdsBlock)
    base: Base = field(default_factory=Base)
    cache: Cache = field(default_factory=Cache)
    ddns: DDNS = field(default_factory=DDNS)
    dns: DNS = field(default_factory=DNS)
    doh: DOH = field(default_factory=DOH)
    dot: DOT = field(default_factory=DOT)
    logging: Logging = field(default_factory=Logging)
    sqlite: SQLite = field(default_factory=SQLite)
    ssl: SSL = field(default_factory=SSL)
    web: Web = field(default_factory=Web)

    # override default configs
    def load(self) -> str | None:
        sha256 = hashlib.sha256()
        file = Path(self.filename)

        try:
            data = file.read_bytes()
            sha256.update(data)
            configs: dict[str, Any] = yaml.safe_load(data.decode()) or {}

            dataclass_map = {
                "adapter": self.adapter,
                "adsblock": self.adsblock,
                "base": self.base,
                "cache": self.cache,
                "ddns": self.ddns,
                "dns": self.dns,
                "doh": self.doh,
                "dot": self.dot,
                "logging": self.logging,
                "ssl": self.ssl,
                "web": self.web,
            }

            for key, cls in dataclass_map.items():
                cfg = configs.get(key, {})
                setattr(self, key, self.parse(cls, cfg))

            # special handling for dns custom entries
            buffers = {
                "1.0.0.127.in-addr.arpa.": "localhost.",
                "localhost.": "127.0.0.1",
                f"{socket.gethostname().lower()}.": "127.0.0.1",
            }

            for item in configs.get("dns", {}).get("custom", []):
                try:
                    key, value = item.split(":")
                    buffers[f"{key.lower()}."] = value
                except ValueError:
                    logging.error(f"invalid custom dns: {item}")

            self.dns.custom = {key: value for key, value in sorted(buffers.items())}

            return sha256.hexdigest()

        except FileNotFoundError:
            logging.error(f"config file {self.filename} not found, using defaults.")

        except yaml.YAMLError as err:
            logging.error(f"error parsing yml file: {err}")

        except Exception as err:
            logging.exception(f"unexpected {err=}, {type(err)=}")

        return None

    # helpers
    def parse(self, instance: T, cfg: dict) -> T:
        updates = {
            f.name: cfg[f.name]
            for f in fields(instance)
            if f.name in cfg and cfg[f.name] is not None
        }
        return replace(instance, **updates)

    # in case config file is different
    def sync(self, session) -> datetime | None:
        sha256 = self.load()
        if not sha256:
            return None

        row = session.query(Setting).filter_by(key="config-sha256").first()
        if row and sha256 == row.value:
            return None  # no changes detected

        now = datetime.now(tz=timezone.utc)
        if not row:
            row = Setting(key="config-sha256", created_on=now)
            session.add(row)

        row.value = sha256
        row.updated_on = now

        session.commit()
        return now
