import asyncio
import logging

from datetime import datetime, timedelta, timezone

import httpx

from .sqlite import AdsBlockList, Setting


class AdsBlock:
    def __init__(self, config, sqlite):
        self.blocked_domains = set()
        self.total_domains = 0

        self.blacklist = config.adsblock.blacklist
        self.custom = config.adsblock.custom
        self.whitelist = config.adsblock.whitelist
        self.reload = config.adsblock.reload
        self.session = sqlite.session
        self.sqlite = sqlite

    async def load_blacklist(self, urls):
        row = self.session.query(Setting).filter_by(key="blocked-stats").first()
        dt = datetime.now(tz=timezone.utc)

        if (
            not self.reload
            and row
            and dt.date() < (row.updated_on + timedelta(days=1)).date()
        ):
            return False

        logging.info(
            "generating new cache, or cache is empty, or cache is older than a day!"
        )
        logging.info(f"parsing {len(urls)} adblock lists ...")

        self.blocked_domains = set()
        async with httpx.AsyncClient(verify=False, timeout=9.0) as client:
            for url in urls:
                for i in range(1, 4):
                    try:
                        response = await client.get(url, follow_redirects=True)
                        response.raise_for_status()

                        url, contents, count = self.parse(response)
                        if not (url and contents and count):
                            raise ValueError("unable to parse file content!")

                        self.sqlite.update(
                            AdsBlockList(url=url, contents=contents, count=count)
                        )
                        break

                    except httpx.ConnectError:
                        logging.error(f"failed to connect: {url}")

                    except ValueError as err:
                        logging.error(f"unexpected {err=}, {type(err)=}, {url}")
                        break

                    except Exception as err:
                        logging.exception(f"unexpected {err=}, {type(err)=}, {url}")
                        await asyncio.sleep(3)

        # blocked_stats
        stats = f"{len(self.blocked_domains)} out of {self.total_domains}"
        self.sqlite.update(Setting(key="blocked-stats", value=stats))

        # blocked_domains
        self.blocked_domains = sorted(self.blocked_domains)
        self.sqlite.update(
            Setting(key="blocked-domains", value="\n".join(self.blocked_domains))
        )

        logging.info(f"... done, loaded {stats}!")
        return True

    def load_cache(self):
        # blocked_stats
        row = self.session.query(Setting).filter_by(key="blocked-stats").first()
        stats = row.value if row else "0 out of 0"

        # blocked_domains
        row = self.session.query(Setting).filter_by(key="blocked-domains").first()
        if row:
            self.blocked_domains = set(row.value.split("\n"))

        logging.info(f"loaded cached blocked domains, {stats}!")

    def load_custom(self, lists):
        count = 0
        total = 0

        for domain in lists:
            if domain:
                total += 1
                buffer = f"{domain}."

                if buffer not in self.blocked_domains:
                    self.blocked_domains.add(buffer)
                    count += 1
                    logging.debug(f"blacklisted {buffer}")

        logging.info(f"loaded custom blacklist, {count} out of {total}!")

    def load_whitelist(self, lists):
        count = 0
        total = 0

        for domain in lists:
            if domain:
                total += 1
                buffer = f"{domain}."

                if buffer in self.blocked_domains:
                    self.blocked_domains.remove(buffer)
                    count += 1
                    logging.debug(f"whitelisted {buffer}")

        logging.info(f"loaded whitelist, {count} out of {total}!")

    def parse(self, response):
        url = str(response.url)
        count = 0

        for line in response.text.splitlines():
            line = line.strip()

            if line and not line.startswith(("!", "#")):
                domain = line.split()[0].replace("||", "").replace("^", "") + "."
                self.blocked_domains.add(domain)
                count += 1
                # logging.debug(f"parsed {domain} from {line}")

        self.total_domains += count
        logging.debug(f"+{count}, {url}")

        return url, response.text, count

    async def setup(self, reload=False, force=False):
        if reload:
            if force:
                # re-set up the list of domains to be blocked on config change detected
                self.reload = True

            if await self.load_blacklist(self.blacklist):
                self.load_custom(self.custom)
                self.load_whitelist(self.whitelist)

                # re-set reload to false to prevent repeatative reload
                self.reload = False

        else:
            # load cache first!
            self.load_cache()
            self.load_custom(self.custom)
            self.load_whitelist(self.whitelist)
