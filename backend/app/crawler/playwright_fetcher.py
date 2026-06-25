from playwright.async_api import async_playwright


class PlaywrightFetcher:

    @staticmethod
    async def fetch(url: str) -> str | None:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)

            page = await browser.new_page()

            await page.goto(
                url,
                wait_until="networkidle",
                timeout=30000,
            )

            html = await page.content()

            await browser.close()

            return html