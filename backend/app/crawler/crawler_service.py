import asyncio
import time

from app.core.config import settings
from app.core.logger import logger
from app.crawler.discovery import DiscoveryService
from app.crawler.document_processor import DocumentProcessor
from app.crawler.fetcher import AsyncFetcher
from app.crawler.models import CrawlResult


class CrawlerService:
    """
    Main orchestrator for the crawling pipeline.

    Flow:
        Discover URLs
            ↓
        Fetch HTML
            ↓
        Process Document
            ↓
        Return CrawlResult
    """

    def __init__(self):
        self.fetcher = AsyncFetcher()

    async def crawl(self, url: str) -> CrawlResult:

        start_time = time.perf_counter()

        try:
            # Discover internal pages
            urls = await DiscoveryService.discover(
                start_url=url,
                fetcher=self.fetcher,
                max_pages=settings.CRAWLER_MAX_PAGES,
            )

            logger.info(f"Discovered {len(urls)} pages")

            semaphore = asyncio.Semaphore(
                settings.CRAWLER_CONCURRENCY
            )

            async def process(page_url: str):

                async with semaphore:

                    try:

                        html = await asyncio.wait_for(
                            self.fetcher.fetch(page_url),
                            timeout=settings.CRAWLER_TIMEOUT,
                        )
                        print("HTML CONTENT IS",html[:1000])

                        if not html:
                            return None

                        return DocumentProcessor.process(
                            page_url,
                            html,
                        )

                    except Exception as e:

                        logger.exception(
                            f"Failed processing {page_url}: {e}"
                        )

                        return None

            pages = await asyncio.gather(
                *(process(page) for page in urls),
                return_exceptions=True,
            )

            pages = [
                page
                for page in pages
                if page is not None
                and not isinstance(page, Exception)
            ]

            elapsed = (
                time.perf_counter() - start_time
            ) * 1000

            logger.info(
                f"Crawl completed in {elapsed:.2f} ms"
            )

            return CrawlResult(
                base_url=url,
                pages=pages,
                total_pages=len(pages),
                crawl_time_ms=elapsed,
            )

        finally:
            await self.fetcher.close()