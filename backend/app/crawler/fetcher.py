import asyncio
from typing import Optional

import httpx
from playwright.async_api import async_playwright

from app.core.logger import logger
from app.crawler.constants import (
    DEFAULT_TIMEOUT,
    MAX_REDIRECTS,
    MAX_RETRIES,
    USER_AGENT,
)


class AsyncFetcher:

    def __init__(self) -> None:

        self.client = httpx.AsyncClient(
            http2=True,
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            timeout=httpx.Timeout(DEFAULT_TIMEOUT),
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
            ),
        )

    async def fetch_dynamic(self, url: str) -> Optional[str]:
        try:
            async with async_playwright() as p:

                browser = await p.chromium.launch(
                    headless=True
                )

                page = await browser.new_page()

                await page.goto(
                    url,
                    wait_until="networkidle",
                    timeout=30000,
                )

                html = await page.content()

                await browser.close()

                return html

        except Exception as e:

            logger.exception(
                f"Playwright failed for {url}: {e}"
            )

            return None

    async def fetch(self, url: str) -> Optional[str]:

        for attempt in range(1, MAX_RETRIES + 1):

            try:

                response = await self.client.get(url)

                response.raise_for_status()

                if "text/html" not in response.headers.get(
                    "content-type", ""
                ):
                    return None

                html = response.text

                # Detect React / Next / Vue
                if (
                    'id="root"' in html
                    or 'id="__next"' in html
                    or 'id="app"' in html
                ):

                    logger.info(
                        f"Dynamic website detected: {url}"
                    )

                    dynamic_html = await self.fetch_dynamic(
                        url
                    )

                    if dynamic_html:
                        return dynamic_html

                return html

            except Exception:

                logger.warning(
                    f"Attempt {attempt}/{MAX_RETRIES} failed : {url}"
                )

                if attempt < MAX_RETRIES:
                    await asyncio.sleep(attempt)

        logger.error(f"Failed to fetch : {url}")

        return None

    async def close(self):

        await self.client.aclose()