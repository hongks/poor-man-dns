import logging
import time

from datetime import datetime, timedelta

import httpx

from .models import AdsBlockList, Setting


class AdsBlock:
    def __init__(self, sqlite, reload=False):
        self.blocked_domains = set()
        self.total_domains = 0

        self.reload = reload
        self.session = sqlite.session
        self.sqlite = sqlite

    def get_adsblock_file(self, client, url):
        for i in range(2):
            try:
                response = client.get(url)
                response.raise_for_status()
                return self.parse(response)

            except Exception as err:
                logging.error(f"unexpected {err=}, {type(err)=}, {url}")
                time.sleep(3)

        return None

    def load_blacklist(self, urls):
        row = self.session.query(Setting).filter_by(key="blocked-stats").first()

        if (
            not self.reload
            and row
            and datetime.utcnow().date() < (row.updated_on + timedelta(days=1)).date()
        ):
            return False

        logging.info(
            "generating new cache, or cache is empty, or cache is older than a day!"
        )
        logging.info(f"parsing {len(urls)} adblock lists ...")

        self.blocked_domains = set()
        with httpx.Client(verify=False, timeout=9.0) as client:
            buffers = []
            for url in urls:
                buffer = self.get_adsblock_file(client, url)
                if buffer:
                    buffers.append(buffer)

            self.sync(buffers)

        # blocked_stats
        stats = f"{len(self.blocked_domains)} out of {self.total_domains}"
        self.sqlite.update("blocked-stats", stats)

        # blocked_domains
        self.blocked_domains = sorted(self.blocked_domains)
        self.sqlite.update("blocked-domains", "\n".join(self.blocked_domains))

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

    def sync(self, buffers):
        if not buffers:
            return

        for url, contents, count in buffers:
            row = self.session.query(AdsBlockList).filter_by(url=url).first()
            dt = datetime.utcnow()

            if row:
                row.contents = contents
                row.count = count
                row.updated_on = dt

            else:
                row = AdsBlockList(
                    url=url,
                    is_active=True,
                    contents=contents,
                    count=count,
                    created_on=dt,
                    updated_on=dt,
                )

                self.session.add(row)

        self.session.commit()
