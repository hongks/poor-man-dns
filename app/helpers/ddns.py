import asyncio
import logging

import httpx

from .utility import wait_or_timeout


# ################################################################################
# typing annotations to avoid circular imports


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config
    from .sqlite import SQLite


# ################################################################################
# ddns server


class DDNSServer:
    def __init__(self, config: "Config", sqlite: "SQLite"):
        self.config = config.ddns
        self.sqlite = sqlite

        self.headers = {
            "accept": "application/json",
            "API-Key": config.ddns.api_key,
            "Content-Type": "application/json",
        }
        self.url = self.provider(config.ddns.provider, config.ddns.domain_id)

        self.last_ipv4 = None
        self.running = True
        self.shutdown_event = asyncio.Event()

    def provider(self, provider: str, domain_id: int) -> str:
        url = ""
        if provider == "dynu":
            url = f"https://api.dynu.com/v2/dns/{domain_id}"

        return url

    async def close(self):
        self.running = False
        self.shutdown_event.set()

        logging.info("listener is shutting down!")

    async def update(self) -> bool:
        async with httpx.AsyncClient(
            timeout=9.0,
            transport=httpx.AsyncHTTPTransport(retries=3),
        ) as client:
            try:
                resp = await client.get("https://api.ipify.org?format=json")
                resp.raise_for_status()

                current_ipv4 = resp.json().get("ip")
                if current_ipv4 == self.last_ipv4:
                    return False

                logging.info(
                    f"ip changed from {self.last_ipv4 or 'None'} to {current_ipv4}, updating ..."
                )

                data = {
                    "name": self.config.domain_name,
                    "group": "",
                    "ttl": 300,
                    "ipv4": "true",
                    "ipv4Address": current_ipv4,
                }
                resp = await client.post(self.url, headers=self.headers, json=data)
                resp.raise_for_status()

                self.last_ipv4 = current_ipv4
                logging.info("... updated!")
                return True

            except (
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.HTTPStatusError,
                httpx.ReadTimeout,
            ) as err:
                logging.warning(f"error failed: {type(err).__name__}")
                return False

            except Exception as err:
                logging.exception(f"error unhandled: {err}")
                return False

    async def listen(self):
        logging.info("listener is up and running.")

        while self.running:
            if self.config.enable:
                await self.update()

            await wait_or_timeout(self.shutdown_event, self.config.interval)
