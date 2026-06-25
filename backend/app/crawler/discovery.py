from collections import deque
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.crawler.constants import (
    DEFAULT_MAX_PAGES,
    IGNORED_EXTENSIONS,
    PRIORITY_PATHS,
    SKIP_PATHS,
)
from app.crawler.fetcher import AsyncFetcher


class DiscoveryService:
    """
    Responsible for discovering internal pages of a website.
    """

    @staticmethod
    def normalize_url(url: str) -> str:
        parsed = urlparse(url)

        path = parsed.path.rstrip("/")

        return f"{parsed.scheme}://{parsed.netloc}{path}"

    @staticmethod
    def is_valid_link(base_domain: str, url: str) -> bool:

        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            return False

        if parsed.netloc != base_domain:
            return False

        if any(parsed.path.endswith(ext) for ext in IGNORED_EXTENSIONS):
            return False

        if any(parsed.path.startswith(path) for path in SKIP_PATHS):
            return False

        return True

    @staticmethod
    def extract_links(base_url: str, html: str) -> list[str]:

        soup = BeautifulSoup(html, "html.parser")

        base_domain = urlparse(base_url).netloc

        links = set()

        for anchor in soup.find_all("a", href=True):

            href = anchor["href"]

            absolute = urljoin(base_url, href)

            absolute = DiscoveryService.normalize_url(absolute)

            if DiscoveryService.is_valid_link(
                base_domain,
                absolute,
            ):
                links.add(absolute)

        return list(links)

    @staticmethod
    def prioritize(urls: list[str]) -> list[str]:

        priority = []

        normal = []

        for url in urls:

            path = urlparse(url).path.lower()

            if path in PRIORITY_PATHS:
                priority.append(url)
            else:
                normal.append(url)

        return priority + normal

    @classmethod
    async def discover(
        cls,
        start_url: str,
        fetcher: AsyncFetcher,
        max_pages: int = DEFAULT_MAX_PAGES,
    ) -> list[str]:

        visited = set()

        queue = deque(
            [cls.normalize_url(start_url)]
        )

        discovered = []

        while queue and len(discovered) < max_pages:

            current_url = queue.popleft()

            if current_url in visited:
                continue

            visited.add(current_url)

            html = await fetcher.fetch(current_url)

            if not html:
                continue

            discovered.append(current_url)

            links = cls.extract_links(
                current_url,
                html,
            )

            links = cls.prioritize(links)

            for link in links:

                if (
                    link not in visited
                    and link not in queue
                ):
                    queue.append(link)

        return discovered