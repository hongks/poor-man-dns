import logging

from datetime import datetime, timedelta

import httpx

from .sqlite import AdsBlockList, Setting


class AdsBlock:
    def update_adsblock_settings(self, key, value):
        row = self.session.query(Setting).filter_by(key=key).first()
        dt = datetime.utcnow()

        if row:
            row.value = value
            row.updated_on = dt

        else:
            row = Setting(
                key=key,
                value=value,
                created_on=dt,
                updated_on=dt,
            )
            self.session.add(row)

        self.session.commit()

    def __init__(self, session, reload=False):
        self.blocked_domains = set()
        self.total_domains = 0

        self.reload = reload
        self.session = session

    def load_custom(self, lists):
        count = 0
        for domain in lists:
            if domain:
                count += 1
                self.blocked_domains.add(f"{domain}.")

        logging.info(f"loaded custom blacklist, {count}!")

    def load_lists(self, lists):
        logging.info(f"loading {len(lists)} adblock lists ...")

        # cache freshness
        row = self.session.query(Setting).filter_by(key="blocked_stats").first()
        stats = None

        if (
            not row
            or self.reload
            or datetime.utcnow().date() > (row.updated_on + timedelta(days=1)).date()
        ):
            for url in lists:
                self.load(url)

            # blocked_stats
            stats = f"{len(self.blocked_domains)} out of {self.total_domains}"
            self.update_adsblock_settings("blocked_stats", stats)

            # blocked_domains
            self.blocked_domains = sorted(self.blocked_domains)
            self.update_adsblock_settings(
                "blocked_domains", "\n".join(self.blocked_domains)
            )

        else:
            # blocked_stats
            stats = row.value

            # blocked_domains
            row = self.session.query(Setting).filter_by(key="blocked_domains").first()
            self.blocked_domains = row.value.split("\n")

            logging.info("++ cache less than a day old!")

        logging.info(f"... done, loaded {stats}!")

    def load_whitelist(self, lists):
        countA = 0
        countB = 0

        for domain in lists:
            if domain:
                countB += 1
            if domain[:-1] in self.blocked_domains:
                countA += 1
                self.blocked_domains.remove(domain)

        logging.info(f"loaded whitelist, {countA} out of {countB}!")

    def load(self, url):
        try:
            response = httpx.get(url, timeout=9)
            response.raise_for_status()

            count = 0
            for line in response.text.splitlines():
                line = line.strip()
                if line and not line.startswith(("!", "#")):
                    domain = line.split()
                    domain = (
                        domain[1]
                        if len(domain) > 1 and not domain[1].startswith("#")
                        else domain[0]
                    )
                    domain = domain.replace("||", "").replace("^", "") + "."

                    count += 1
                    self.blocked_domains.add(domain)

            self.total_domains += count
            row = self.session.query(AdsBlockList).filter_by(url=url).first()
            dt = datetime.utcnow()

            if row:
                row.contents = response.text
                row.count = count
                row.updated_on = dt

            else:
                row = AdsBlockList(
                    url=url,
                    is_active=True,
                    contents=response.text,
                    count=count,
                    created_on=dt,
                    updated_on=dt,
                )
                self.session.add(row)

            self.session.commit()
            logging.debug(f"++ {count}, {url}")

        except Exception as err:
            logging.error(f"unexpected {err=}, {type(err)=}, {url}")
