import asyncio
import logging

from datetime import datetime, timedelta, timezone

import httpx

from cachetools import TTLCache

from .sqlite import AdsBlockList, Setting


# ################################################################################
# typing annotations to avoid circular imports


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config
    from .sqlite import SQLite


# ################################################################################
# adsblock cache wrapper


class AdsBlock:
    def __init__(self, config: "Config", sqlite: "SQLite"):
        self.sqlite = sqlite
        self.session = sqlite.Session()

        self.reload = config.adsblock.reload
        self.blacklist = config.adsblock.blacklist
        self.custom = config.adsblock.custom
        self.whitelist = config.adsblock.whitelist

        self.blocked = set()
        self.total = 0

    def cache(self) -> datetime | None:
        # blocked_domains
        row = self.session.query(Setting).filter_by(key="blocked-domains").first()
        self.blocked = set(row.value.split("\n")) if row else set()

        # blocked_stats
        row = self.session.query(Setting).filter_by(key="blocked-stats").first()
        stats = row.value if row else "0 out of 0"

        logging.info(f"cached blocked domains loaded, {stats}!")

        self.parse("custom", self.custom)
        self.parse("whitelist", self.whitelist)
        return row.updated_on if row else None

    def extract(self, contents: str, buffer: set) -> int:
        count = 0
        for line in contents.splitlines():
            line = line.strip()

            if line and not line.startswith(("!", "#")):
                domain = line.split()
                domain = domain[1] if len(domain) > 1 and not domain[1].startswith(("!", "#")) else domain[0]
                domain = domain.replace("||", "").replace("^", "") + "."

                buffer.add(domain)
                count += 1
                # logging.debug(f"parsed {domain} from {line}")

        return count

    async def fetch(self):
        logging.info(f"parsing {len(self.blacklist)} adblock lists ...")
        buffer = set()

        async with httpx.AsyncClient(
            verify=False,
            timeout=9.0,
            follow_redirects=True,
            transport=httpx.AsyncHTTPTransport(retries=3),
        ) as client:
            for url in self.blacklist:
                try:
                    response = await client.get(url)
                    response.raise_for_status()

                    count = self.extract(response.text, buffer)
                    self.total += count
                    logging.debug(f"+{count}, {url}")

                    self.sqlite.update(
                        AdsBlockList(
                            url=str(response.url),
                            contents=response.text,
                            count=count,
                            status="success",
                        )
                    )

                except (
                    httpx.ConnectError,
                    httpx.ConnectTimeout,
                    httpx.ReadError,
                    httpx.ReadTimeout,
                    httpx.HTTPStatusError,
                ) as err:
                    row = self.session.query(AdsBlockList).filter_by(url=url).first()
                    count = self.extract(row.contents, buffer) if row else 0

                    logging.warning(f"+{type(err).__name__}: {url}")
                    logging.debug(f"+{count}, {url}")
                    self.sqlite.update(AdsBlockList(url=url, status=type(err).__name__))

                except Exception as err:
                    logging.exception(f"unexpected {err=}, {type(err)=}, {url}")

        self.blocked.clear()
        self.blocked = buffer

        # blocked_domains
        self.sqlite.update(
            Setting(
                key="blocked-domains",
                value="\n".join(sorted(self.blocked)),
            )
        )

        # blocked_stats
        stats = f"{len(self.blocked)} out of {self.total}"
        self.sqlite.update(Setting(key="blocked-stats", value=stats))

        logging.info(f"... done, loaded {stats}!")

    # parse blacklist, custom, whitelist domains
    def parse(self, type: str, domains: list[str]):
        count, total = 0, 0
        label = "custom blacklist" if type == "custom" else type

        for domain in domains:
            if not domain:
                continue

            total += 1
            buffer = f"{domain}."

            # blacklist / custom
            if type in ["blacklist", "custom"] and buffer not in self.blocked:
                self.blocked.add(buffer)
                count += 1
                logging.debug(f"+{label}ed {buffer}")

            # whitelist
            elif type == "whitelist" and buffer in self.blocked:
                self.blocked.remove(buffer)
                count += 1
                logging.debug(f"+whitelisted {buffer}")

        logging.info(f"loaded {label}ed, {count} out of {total}!")


# ################################################################################
# adsblock wrapper server


class ADSServer:
    def __init__(self, config: "Config", sqlite: "SQLite"):
        self.config = config
        self.sqlite = sqlite
        self.session = sqlite.Session()

        self.adsblock = AdsBlock(self.config, self.sqlite)
        self.cache = TTLCache(
            maxsize=self.config.cache.max_size, ttl=self.config.cache.ttl
        )
        self.locks = {}
        self.running = True

    def get_blocked_domains(self):
        return self.adsblock.blocked

    def lock(self, key: str):
        if key not in self.locks:
            self.locks[key] = asyncio.Lock()

        return self.locks[key]

    def set(self, key: str, value: str):
        self.cache[key] = value
        self.unlock(key)

    def unlock(self, key):
        if key in self.locks:
            self.locks.pop(key, None)

    async def close(self):
        self.running = False
        logging.info("listener is shutting down!")

    async def load(self):
        now = datetime.now(tz=timezone.utc)
        updated_on = self.adsblock.cache()

        if (
            not self.adsblock.reload
            and updated_on
            and now.date() < (updated_on + timedelta(days=1)).date()
        ):
            return

        logging.info(
            "generating new cache, or cache is empty, or cache is older than a day!"
        )

        await self.adsblock.fetch()
        self.adsblock.parse("custom", self.adsblock.custom)
        self.adsblock.parse("whitelist", self.adsblock.whitelist)

    async def listen(self):
        logging.info(
            f"cache-enable: {str(self.config.cache.enable).lower()}, "
            f"max-size: {self.config.cache.max_size}, ttl: {self.config.cache.ttl}."
        )
        await self.load()
        logging.info("listener is up and running.")

        while self.running:
            if self.config.sync(self.session):
                logging.info(f"{self.config.filename} has changed, reloading ...")
                self.adsblock = AdsBlock(self.config, self.sqlite)
                await self.load()
                logging.info("... done reload!")

            # await asyncio.sleep(30)
            try:
                await asyncio.wait_for(asyncio.Event().wait(), timeout=30)
            except asyncio.TimeoutError:
                pass

    async def get_or_set(self, key: str, fetch_func: callable) -> any:
        async with self.lock(key):
            result = await fetch_func()

        self.unlock(key)
        return result

    async def get(self, key):
        lock = self.locks.get(key)
        if lock and lock.locked():
            async with lock:
                pass  # wait for any ongoing fetch

        return self.cache.get(key)
