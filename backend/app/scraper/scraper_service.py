"""
scraper.py — Universal website scraper (single file, API-first).

Handles: static HTML, WordPress, Shopify, GHL/GoHighLevel funnels,
and JS-rendered SPAs (React / Next.js / Vue / Vite / Gatsby / Angular).

Why it is fast:
  - Discovery merges sitemap URLs + rendered-homepage nav links
    (SPA sitemaps often list only "/", so homepage links are essential).
  - One shared, *warm* Chromium browser is reused across all requests.
  - httpx is tried first; Playwright runs ONLY when a page looks like an
    un-rendered SPA shell (thin text + SPA mount markers).
  - Images / fonts / CSS are blocked during browser render.
  - Pages are fetched concurrently with bounded semaphores.
  - A per-crawl cache prevents fetching the same URL twice.

Run as API:
    uvicorn scraper:app --host 0.0.0.0 --port 8000
    POST /scrape   body: {"url": "https://example.com", "max_pages": 25}

Run from CLI:
    python scraper.py https://example.com

Install once:
    pip install "httpx[http2]" beautifulsoup4 markdownify playwright fastapi uvicorn pydantic
    playwright install chromium
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from markdownify import markdownify as md
from playwright.async_api import Browser, Playwright, Route, async_playwright
from pydantic import BaseModel, Field, HttpUrl

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("scraper")


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
DEFAULT_TIMEOUT: int = 20          # seconds, httpx
MAX_RETRIES: int = 2
HTTP_CONCURRENCY: int = 12         # parallel http fetches
BROWSER_CONCURRENCY: int = 3       # parallel playwright pages (heavy, keep low)
MAX_REDIRECTS: int = 5
DEFAULT_MAX_PAGES: int = 25        # we want the IMPORTANT pages, not the whole site
MAX_CONTENT_LENGTH: int = 200_000  # markdown char cap per page
SPA_TEXT_THRESHOLD: int = 600      # below this visible-text len => looks un-rendered
NAV_TIMEOUT_MS: int = 30_000
RENDER_TEXT_TIMEOUT_MS: int = 6_000   # wait this long for SPA text to paint
MIN_CONTENT_WORDS: int = 15           # drop near-empty pages (deep-link/404 shells)
SITEMAP_CHILD_LIMIT: int = 6       # how many child sitemaps to expand
SITEMAP_URL_CAP: int = 400         # safety cap on urls pulled from sitemaps

# A realistic UA gets past most basic bot walls (Shopify / Cloudflare lite).
# Swap to an honest bot UA if you prefer politeness over compatibility.
USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

IGNORED_EXTENSIONS: tuple[str, ...] = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
    ".css", ".js", ".mjs", ".xml", ".json", ".zip", ".rar", ".7z",
    ".tar", ".gz", ".mp3", ".mp4", ".avi", ".mov", ".woff", ".woff2",
    ".ttf", ".otf",
)

REMOVE_TAGS: tuple[str, ...] = (
    "script", "style", "noscript", "iframe", "svg", "canvas", "select",
    "header", "footer", "nav",      # boilerplate (forms kept: contact pages!)
    "img", "picture", "source",     # images: drop base64 blobs + /assets links
)

# Container class/id tokens that mark boilerplate (nav menus, footers, cookie bars).
# Matched against EXACT class tokens to avoid false positives like "service-header".
BOILERPLATE_TOKENS: frozenset[str] = frozenset({
    "nav", "navbar", "navigation", "menu", "menubar", "header", "footer",
    "site-header", "site-footer", "topbar", "sidebar", "breadcrumb",
    "cookie", "cookies", "cookie-banner", "cookie-notice", "cookie-consent",
})

# Path keywords that mark a "high value" page. Matched as substring,
# so /our-services, /about-the-firm, /practice-areas all count.
PRIORITY_KEYWORDS: tuple[str, ...] = (
    "about", "service", "product", "solution", "pricing", "price",
    "contact", "portfolio", "case-stud", "case_stud", "project",
    "overview", "team", "work", "practice", "who-we-are", "what-we-do",
)

SKIP_PATHS: tuple[str, ...] = (
    "/login", "/signin", "/sign-in", "/signup", "/sign-up", "/register",
    "/logout", "/cart", "/checkout", "/account", "/wp-admin", "/wp-login",
    "/privacy", "/terms", "/cookie", "/email-policy", "/legal",
    "/disclaimer", "/accessibility", "/feed", "/rss", "/tag/", "/author/",
    "/category/", "/page/", "/sitemap",
)

# SPA mount points -> if present AND text is thin, render with browser.
SPA_MARKERS: tuple[str, ...] = (
    'id="root"', "id='root'", 'id="__next"', 'id="app"', "id='app'",
    'id="___gatsby"', "data-reactroot", "ng-version", "__nuxt",
)

# Common high-value paths to PROBE directly when a site's nav isn't crawlable
# (JS-only onClick navigation). Bogus probes render empty and get dropped.
COMMON_PATHS: tuple[str, ...] = (
    "/about", "/about-us", "/services", "/our-services", "/solutions",
    "/products", "/contact", "/contact-us", "/projects", "/portfolio",
    "/work", "/case-studies", "/team", "/pricing",
)

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"\+?\d[\d\s().\-]{8,}\d")
SOCIAL_DOMAINS = (
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com",
    "x.com", "youtube.com", "tiktok.com", "wa.me", "t.me",
)


def _is_error_page(html: str) -> bool:
    """True if `html` is a CDN/host error page (origin down, bad SSL, 5xx, etc.)
    rather than real site content. These must never be returned as page content."""
    if not html:
        return True
    low = html[:4000].lower()  # signatures live in <head>/top of the error page
    if "cloudflare" in low and any(s in low for s in (
        "invalid ssl certificate", "ssl handshake failed", "web server is down",
        "the origin web server", "origin is unreachable", "connection timed out",
        "gateway time-out", "host error", "error 52", "error 100", "error 1101",
    )):
        return True
    generic = (
        "525:", "526:", "524:", "523:", "522:", "521:", "520:",
        "503 service unavailable", "502 bad gateway", "504 gateway",
        "account suspended", "this site can", "site can’t be reached",
        "domain is for sale", "error establishing a database connection",
    )
    return any(t in low for t in generic)


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class Contacts(BaseModel):
    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    socials: list[str] = Field(default_factory=list)


class CrawledPage(BaseModel):
    url: str
    title: str = ""
    description: str = ""
    markdown: str = ""
    word_count: int = 0
    rendered_with: str = "http"   # "http" or "browser"
    contacts: Contacts = Field(default_factory=Contacts)


class ScrapeResult(BaseModel):
    base_url: str
    pages: list[CrawledPage] = Field(default_factory=list)
    total_pages: int = 0
    discovery_method: str = ""
    discovered_urls: list[str] = Field(default_factory=list)  # found before fetch
    crawl_time_ms: float = 0.0
    note: str = ""  # set when the site looks unreachable / down
    contacts: Contacts = Field(default_factory=Contacts)  # aggregated, deduped
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# --------------------------------------------------------------------------- #
# Browser manager — one shared, lazily-launched, reused Chromium
# --------------------------------------------------------------------------- #
class BrowserManager:
    def __init__(self) -> None:
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(BROWSER_CONCURRENCY)

    async def _ensure(self) -> Browser:
        if self._browser and self._browser.is_connected():
            return self._browser
        async with self._lock:
            if self._browser and self._browser.is_connected():
                return self._browser
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-gpu",
                ],
            )
            logger.info("Chromium launched (shared, warm instance)")
            return self._browser

    @staticmethod
    async def _block_assets(route: Route) -> None:
        if route.request.resource_type in {"image", "media", "font", "stylesheet"}:
            await route.abort()
        else:
            await route.continue_()

    async def render(self, url: str) -> Optional[str]:
        browser = await self._ensure()
        async with self._sem:
            context = None
            try:
                context = await browser.new_context(
                    user_agent=USER_AGENT,
                    viewport={"width": 1366, "height": 900},
                )
                await context.route("**/*", self._block_assets)
                page = await context.new_page()
                # domcontentloaded is reliable; networkidle alone can hang.
                await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                # Wait for the SPA to actually paint real text (fast-exits once
                # content appears; times out quietly on genuinely empty routes).
                try:
                    await page.wait_for_function(
                        "() => ((document.body && document.body.innerText) || '')"
                        ".trim().length > 150",
                        timeout=RENDER_TEXT_TIMEOUT_MS,
                    )
                except Exception:
                    pass
                # Nudge lazy-loaded sections (GSAP/IntersectionObserver), then settle.
                try:
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(400)
                except Exception:
                    pass
                try:
                    await page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    pass
                return await page.content()
            except Exception as e:
                logger.warning(f"Browser render failed for {url}: {e}")
                return None
            finally:
                if context:
                    await context.close()

    @staticmethod
    async def _settle(page) -> None:
        """Wait for the SPA to paint, nudge lazy sections, let the network calm."""
        try:
            await page.wait_for_function(
                "() => ((document.body && document.body.innerText) || '')"
                ".trim().length > 120",
                timeout=RENDER_TEXT_TIMEOUT_MS,
            )
        except Exception:
            pass
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(350)
            await page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=2500)
        except Exception:
            pass

    async def _spa_goto(self, page, target_url: str, path: str) -> bool:
        """Navigate to `path` *inside* the running SPA (no hard reload).

        Tries, in order: click a real <a> to the path, click a nav element whose
        text matches the slug, then the History API + popstate trick. This is how
        we reach routes that render blank on a direct URL load (deep-link-broken
        SPAs).
        """
        # 1) click a real anchor pointing at this path
        try:
            sel = f'a[href="{path}"], a[href="{target_url}"], a[href$="{path}"]'
            el = await page.query_selector(sel)
            if el:
                await el.click(timeout=4000)
                await page.wait_for_timeout(500)
                return True
        except Exception:
            pass
        # 2) click a link/button whose visible text matches the slug words
        label = path.strip("/").replace("-", " ").replace("/", " ").strip()
        if label:
            for role in ("link", "button"):
                try:
                    loc = page.get_by_role(role, name=re.compile(re.escape(label), re.I))
                    if await loc.count() > 0:
                        await loc.first.click(timeout=4000)
                        await page.wait_for_timeout(500)
                        return True
                except Exception:
                    pass
        # 3) History API push + popstate (works for many client routers)
        try:
            await page.evaluate(
                "(p) => { window.history.pushState({}, '', p);"
                " window.dispatchEvent(new PopStateEvent('popstate')); }",
                path,
            )
            await page.wait_for_timeout(500)
            return True
        except Exception:
            return False

    async def crawl_spa(
        self, home_url: str, target_urls: list[str]
    ) -> list[tuple[str, str]]:
        """Open the homepage once, then client-side navigate to each target so
        the SPA router renders real content. Returns [(url, html), ...]."""
        browser = await self._ensure()
        out: list[tuple[str, str]] = []
        home_key = home_url.rstrip("/")
        async with self._sem:
            context = None
            try:
                context = await browser.new_context(
                    user_agent=USER_AGENT,
                    viewport={"width": 1366, "height": 900},
                )
                await context.route("**/*", self._block_assets)
                page = await context.new_page()
                await page.goto(home_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                await self._settle(page)
                out.append((home_url, await page.content()))
                try:
                    home_text = await page.evaluate("() => document.body.innerText.trim()")
                except Exception:
                    home_text = ""

                seen = {home_key}
                for target in target_urls:
                    tkey = target.rstrip("/")
                    if tkey in seen:
                        continue
                    seen.add(tkey)
                    path = urlparse(target).path or "/"
                    if path in ("", "/"):
                        continue
                    try:
                        if not await self._spa_goto(page, target, path):
                            continue
                        await self._settle(page)
                        text = await page.evaluate("() => document.body.innerText.trim()")
                    except Exception as e:
                        logger.warning(f"SPA nav failed {target}: {e}")
                        continue
                    # only keep if the route actually rendered something new
                    if text and text != home_text and len(text) > 120:
                        out.append((target, await page.content()))
            except Exception as e:
                logger.warning(f"SPA session failed for {home_url}: {e}")
            finally:
                if context:
                    await context.close()
        return out

    async def close(self) -> None:
        try:
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
        finally:
            self._browser, self._pw = None, None


# --------------------------------------------------------------------------- #
# Fetcher — httpx first, browser fallback only when needed
# --------------------------------------------------------------------------- #
class Fetcher:
    def __init__(self, browser: BrowserManager) -> None:
        self.browser = browser
        self._cache: dict[str, tuple[Optional[str], str]] = {}
        self.client = httpx.AsyncClient(
            http2=True,
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            timeout=httpx.Timeout(DEFAULT_TIMEOUT),
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )

    def reset_cache(self) -> None:
        """Clear the per-crawl cache so each scrape() gets fresh data."""
        self._cache.clear()

    @staticmethod
    def _text_len(html: str) -> int:
        try:
            return len(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
        except Exception:
            return len(html)

    def _needs_render(self, html: str) -> bool:
        text_len = self._text_len(html)
        if text_len < SPA_TEXT_THRESHOLD:
            return True  # basically empty shell -> render
        low = html.lower()
        if any(m in low for m in SPA_MARKERS) and text_len < 1500:
            return True  # SPA mount point + suspiciously thin content
        return False

    async def raw_get(self, url: str) -> Optional[str]:
        """Plain GET, no SPA logic. Used for sitemap / robots."""
        try:
            r = await self.client.get(url)
            if r.status_code == 200:
                return r.text
        except Exception:
            pass
        return None

    async def _http_get(self, url: str) -> tuple[Optional[str], int]:
        """Returns (html, status). html is None for errors / non-HTML."""
        last_status = 0
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                r = await self.client.get(url)
                last_status = r.status_code
                if r.status_code >= 400:
                    # only bot-block / rate-limit codes are worth a retry
                    if r.status_code in (403, 429, 503) and attempt < MAX_RETRIES:
                        await asyncio.sleep(attempt)
                        continue
                    return None, r.status_code
                ctype = r.headers.get("content-type", "")
                if "text/html" not in ctype and "application/xhtml" not in ctype:
                    return None, r.status_code
                return r.text, r.status_code
            except Exception:
                logger.warning(f"http attempt {attempt}/{MAX_RETRIES} failed: {url}")
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(attempt)
        return None, last_status

    async def fetch(self, url: str) -> tuple[Optional[str], str]:
        """Returns (html, method): 'http' | 'browser' | 'failed'. Cached per crawl."""
        key = url.rstrip("/")
        if key in self._cache:
            return self._cache[key]
        result = await self._fetch_uncached(url)
        self._cache[key] = result
        return result

    async def _fetch_uncached(self, url: str) -> tuple[Optional[str], str]:
        html, status = await self._http_get(url)

        if html is None:
            # Only worth a browser attempt for bot-blocks / connection issues —
            # NOT for definite server errors (5xx, 404), which a browser can't fix.
            if status in (0, 403, 429, 503):
                rendered = await self.browser.render(url)
                if rendered and not _is_error_page(rendered):
                    return rendered, "browser"
            return None, "failed"

        if _is_error_page(html):
            return None, "failed"

        if self._needs_render(html):
            rendered = await self.browser.render(url)
            if (
                rendered
                and not _is_error_page(rendered)
                and self._text_len(rendered) > self._text_len(html)
            ):
                return rendered, "browser"

        return html, "http"

    async def aclose(self) -> None:
        await self.client.aclose()


# --------------------------------------------------------------------------- #
# Discovery — sitemap + rendered-homepage links, merged
# --------------------------------------------------------------------------- #
class Discovery:
    @staticmethod
    def root_domain(netloc: str) -> str:
        return netloc.lower().split(":")[0].removeprefix("www.")

    @staticmethod
    def normalize(url: str) -> str:
        p = urlparse(url)
        path = p.path.rstrip("/")
        return f"{p.scheme.lower()}://{p.netloc.lower()}{path}"  # drop query + fragment

    @classmethod
    def is_valid(cls, base_root: str, url: str) -> bool:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False
        if cls.root_domain(p.netloc) != base_root:
            return False
        low = p.path.lower()
        if any(low.endswith(ext) for ext in IGNORED_EXTENSIONS):
            return False
        if any(s in low for s in SKIP_PATHS):
            return False
        return True

    @classmethod
    def extract_links(cls, base_url: str, html: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        base_root = cls.root_domain(urlparse(base_url).netloc)
        links: set[str] = set()
        for a in soup.find_all("a", href=True):
            absolute = cls.normalize(urljoin(base_url, a["href"]))
            if cls.is_valid(base_root, absolute):
                links.add(absolute)
        return list(links)

    @staticmethod
    def prioritize(home: str, urls: list[str]) -> list[str]:
        home_n = home.rstrip("/")
        seen, priority, normal = set(), [], []
        for u in urls:
            if u in seen:
                continue
            seen.add(u)
            path = urlparse(u).path.lower()
            if u.rstrip("/") == home_n or path in ("", "/"):
                priority.insert(0, u)  # homepage always first
            elif any(k in path for k in PRIORITY_KEYWORDS):
                priority.append(u)
            else:
                normal.append(u)
        ordered = priority + normal
        if home_n not in {u.rstrip("/") for u in ordered}:
            ordered.insert(0, home)
        return ordered

    @staticmethod
    def _parse_sitemap_xml(text: str) -> tuple[list[str], list[str]]:
        """Returns (child_sitemaps, page_urls), namespace-agnostic."""
        try:
            root = ElementTree.fromstring(text.encode("utf-8"))
        except Exception:
            return [], []
        root_tag = root.tag.split("}")[-1].lower()
        locs = [
            el.text.strip()
            for el in root.iter()
            if el.tag.split("}")[-1].lower() == "loc" and el.text
        ]
        if root_tag == "sitemapindex":
            return locs, []
        return [], locs

    @classmethod
    async def _from_sitemap(cls, start_url: str, fetcher: Fetcher) -> list[str]:
        p = urlparse(start_url)
        base = f"{p.scheme}://{p.netloc}"
        base_root = cls.root_domain(p.netloc)
        candidates = [
            f"{base}/sitemap.xml",
            f"{base}/sitemap_index.xml",
            f"{base}/sitemap-index.xml",
        ]

        robots = await fetcher.raw_get(f"{base}/robots.txt")
        if robots:
            for line in robots.splitlines():
                if line.lower().startswith("sitemap:"):
                    candidates.append(line.split(":", 1)[1].strip())

        pages: list[str] = []
        for sm in dict.fromkeys(candidates):  # dedupe, keep order
            text = await fetcher.raw_get(sm)
            if not text:
                continue
            children, urls = cls._parse_sitemap_xml(text)
            pages.extend(urls)
            for child in children[:SITEMAP_CHILD_LIMIT]:
                ctext = await fetcher.raw_get(child)
                if ctext:
                    _, curls = cls._parse_sitemap_xml(ctext)
                    pages.extend(curls)
            if pages:
                break

        valid = [cls.normalize(u) for u in pages]
        valid = [u for u in valid if cls.is_valid(base_root, u)]
        return list(dict.fromkeys(valid))[:SITEMAP_URL_CAP]

    @staticmethod
    def _slugify(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")

    @classmethod
    def _candidate_paths(cls, base: str, html: Optional[str], cap: int = 20) -> list[str]:
        """Common high-value paths + slugified nav labels, for JS-only navs."""
        cands = [cls.normalize(base + cp) for cp in COMMON_PATHS]
        if html:
            soup = BeautifulSoup(html, "html.parser")
            for el in soup.find_all(["a", "li", "button", "span"]):
                txt = el.get_text(" ", strip=True)
                if txt and len(txt) <= 28 and 1 <= len(txt.split()) <= 4:
                    slug = cls._slugify(txt)
                    if slug and slug != "home":
                        cands.append(cls.normalize(f"{base}/{slug}"))
        return list(dict.fromkeys(cands))[:cap]

    @classmethod
    async def discover(cls, start_url: str, fetcher: Fetcher) -> tuple[list[str], str]:
        home = cls.normalize(start_url)
        p = urlparse(start_url)
        base = f"{p.scheme}://{p.netloc}"
        base_root = cls.root_domain(p.netloc)
        collected: list[str] = [home]
        methods: list[str] = []

        # 1) Sitemap (fast). On SPAs it often only lists "/".
        sitemap_urls = await cls._from_sitemap(start_url, fetcher)
        if sitemap_urls:
            collected.extend(sitemap_urls)
            methods.append("sitemap")

        # 2) Links from the rendered homepage (catches normal SPA nav routes).
        html, _ = await fetcher.fetch(start_url)
        homepage_links = cls.extract_links(start_url, html) if html else []
        if homepage_links:
            collected.extend(homepage_links)
            methods.append("homepage")

        # 3) If crawlable discovery is sparse (e.g. JS-only onClick nav like some
        #    React headers), PROBE common paths + nav-label slugs directly. The
        #    server serves the SPA shell for any route, the browser renders it,
        #    and bogus probes get dropped later for being empty.
        home_key = home.rstrip("/")
        real = [u for u in dict.fromkeys(collected) if u.rstrip("/") != home_key]
        if len(real) < 4:
            probes = [
                u for u in cls._candidate_paths(base, html)
                if cls.is_valid(base_root, u) and u.rstrip("/") != home_key
            ]
            if probes:
                collected.extend(probes)
                methods.append("probe")

        method = "+".join(methods) if methods else "fallback"
        return cls.prioritize(home, collected), method


# --------------------------------------------------------------------------- #
# Document processing — clean markdown + contact extraction for leads
# --------------------------------------------------------------------------- #
class DocumentProcessor:
    @staticmethod
    def _extract_contacts(soup: BeautifulSoup) -> Contacts:
        emails: set[str] = set()
        phones: set[str] = set()
        socials: set[str] = set()

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            low = href.lower()
            if low.startswith("mailto:"):
                emails.add(href[7:].split("?")[0].strip())
            elif low.startswith("tel:"):
                phones.add(href[4:].strip())
            elif any(d in low for d in SOCIAL_DOMAINS):
                socials.add(href.split("?")[0])

        text = soup.get_text(" ", strip=True)
        emails.update(EMAIL_RE.findall(text))
        for m in PHONE_RE.findall(text):
            # reject ISO dates and times that look numeric (e.g. "2026-06-28 17:32")
            if re.search(r"\d{4}-\d{2}-\d{2}", m) or re.search(r"\d{1,2}:\d{2}", m):
                continue
            digits = re.sub(r"\D", "", m)
            if 9 <= len(digits) <= 15:
                phones.add(m.strip())

        clean_emails = sorted(e for e in emails if "." in e.split("@")[-1])[:20]
        return Contacts(
            emails=clean_emails,
            phones=sorted(phones)[:20],
            socials=sorted(socials)[:20],
        )

    @staticmethod
    def _strip_boilerplate(soup: BeautifulSoup) -> None:
        # 1) semantic + listed non-content tags (images included)
        for tag in REMOVE_TAGS:
            for el in soup.find_all(tag):
                el.decompose()
        # 2) containers whose class token marks boilerplate
        for el in soup.find_all(attrs={"class": True}):
            classes = el.get("class") or []
            if any(c.lower() in BOILERPLATE_TOKENS for c in classes):
                el.decompose()
        # 3) containers whose id marks boilerplate
        for el in soup.find_all(attrs={"id": True}):
            idl = (el.get("id") or "").lower()
            if idl in BOILERPLATE_TOKENS or "cookie" in idl:
                el.decompose()

    @staticmethod
    def process(url: str, html: str, rendered_with: str) -> CrawledPage:
        # CDN/host error pages (bad SSL, origin down, 5xx) are never real content.
        if _is_error_page(html):
            return CrawledPage(url=url, rendered_with=rendered_with)

        soup = BeautifulSoup(html, "html.parser")

        title = soup.title.get_text(strip=True) if soup.title else ""

        description = ""
        meta = soup.find("meta", attrs={"name": "description"}) or soup.find(
            "meta", attrs={"property": "og:description"}
        )
        if meta:
            description = (meta.get("content") or "").strip()

        # Contacts come from the FULL page (footer mailto/tel/socials too),
        # so extract them BEFORE stripping boilerplate away.
        contacts = DocumentProcessor._extract_contacts(soup)

        # Strip everything that isn't real content, then pick the main region.
        DocumentProcessor._strip_boilerplate(soup)
        root = soup.find("main") or soup.find("article") or soup.body or soup

        # strip=["a","img","picture"] => no link URLs, no images in the body text.
        markdown = md(str(root), heading_style="ATX", strip=["a", "img", "picture"])
        markdown = "\n".join(
            line.strip() for line in markdown.splitlines() if line.strip()
        )[:MAX_CONTENT_LENGTH]

        return CrawledPage(
            url=url,
            title=title,
            description=description,
            markdown=markdown,
            word_count=len(markdown.split()),
            rendered_with=rendered_with,
            contacts=contacts,
        )


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
class Scraper:
    def __init__(self) -> None:
        self.browser = BrowserManager()
        self.fetcher = Fetcher(self.browser)

    @staticmethod
    def _strip_shared_boilerplate(
        pages: list[CrawledPage], threshold: float = 0.6
    ) -> None:
        """Remove lines that repeat across most pages (nav menus, footers, CTAs).

        Generic + framework-agnostic: if a line shows up on >= `threshold`
        fraction of pages, it is boilerplate, not page content. Needs >= 3
        pages to have enough signal.
        """
        if len(pages) < 3:
            return
        counts: dict[str, int] = {}
        for p in pages:
            for line in set(p.markdown.splitlines()):
                counts[line] = counts.get(line, 0) + 1
        cutoff = max(2, round(len(pages) * threshold))
        common = {ln for ln, c in counts.items() if c >= cutoff}
        if not common:
            return
        for p in pages:
            kept = [ln for ln in p.markdown.splitlines() if ln not in common]
            p.markdown = "\n".join(kept).strip()
            p.word_count = len(p.markdown.split())

    async def scrape(self, url: str, max_pages: int = DEFAULT_MAX_PAGES) -> ScrapeResult:
        start = time.perf_counter()
        self.fetcher.reset_cache()  # fresh data each request

        urls, method = await Discovery.discover(url, self.fetcher)
        urls = urls[:max_pages]
        logger.info(f"Discovered {len(urls)} pages via {method}")

        home_key = Discovery.normalize(url).rstrip("/")

        # ---- Phase 1: normal concurrent fetch (httpx -> render). Works for most
        # sites, including SPAs whose direct deep-links render fine. ----
        sem = asyncio.Semaphore(HTTP_CONCURRENCY)

        async def work(page_url: str) -> Optional[CrawledPage]:
            async with sem:
                try:
                    html, rendered = await self.fetcher.fetch(page_url)
                    if not html:
                        return None
                    return DocumentProcessor.process(page_url, html, rendered)
                except Exception as e:
                    logger.warning(f"Failed processing {page_url}: {e}")
                    return None

        results = await asyncio.gather(*(work(u) for u in urls), return_exceptions=True)
        pages = [p for p in results if isinstance(p, CrawledPage)]
        by_url = {p.url.rstrip("/"): p for p in pages}

        # ---- Phase 2: for SPAs, recover routes that rendered empty on direct load
        # (deep-link-broken routers) by navigating client-side from the homepage. ----
        _, home_method = await self.fetcher.fetch(url)  # cached
        is_spa = home_method == "browser"
        if is_spa:
            empty_targets = [
                u for u in urls
                if u.rstrip("/") != home_key
                and (
                    u.rstrip("/") not in by_url
                    or by_url[u.rstrip("/")].word_count < MIN_CONTENT_WORDS
                )
            ]
            if empty_targets:
                logger.info(f"SPA recovery for {len(empty_targets)} empty route(s)")
                session = await self.browser.crawl_spa(url, empty_targets)
                for page_url, html in session:
                    if page_url.rstrip("/") == home_key:
                        continue  # keep the phase-1 homepage
                    try:
                        rec = DocumentProcessor.process(page_url, html, "browser")
                        if rec.word_count >= MIN_CONTENT_WORDS:
                            by_url[page_url.rstrip("/")] = rec
                    except Exception as e:
                        logger.warning(f"Recovery processing failed {page_url}: {e}")
                pages = list(by_url.values())

        # Drop nav/footer/CTA lines that repeat across most pages.
        self._strip_shared_boilerplate(pages)

        # Drop empty / near-empty pages (routes that never rendered, error pages),
        # but keep the homepage if it has any real content.
        pages = [
            p for p in pages
            if p.word_count >= MIN_CONTENT_WORDS
            or (p.url.rstrip("/") == home_key and p.word_count >= 1)
        ]

        note = ""
        if not pages:
            note = (
                "No readable content found. The site may be down, blocking bots, "
                "or have an origin SSL/CDN error (e.g. Cloudflare 5xx)."
            )

        agg = Contacts()
        for p in pages:
            agg.emails.extend(p.contacts.emails)
            agg.phones.extend(p.contacts.phones)
            agg.socials.extend(p.contacts.socials)
        agg.emails = sorted(set(agg.emails))
        agg.phones = sorted(set(agg.phones))
        agg.socials = sorted(set(agg.socials))

        elapsed = (time.perf_counter() - start) * 1000
        logger.info(f"Crawl done: {len(pages)} pages in {elapsed:.0f} ms")

        return ScrapeResult(
            base_url=url,
            pages=pages,
            total_pages=len(pages),
            discovery_method=method,
            discovered_urls=urls,
            crawl_time_ms=round(elapsed, 2),
            note=note,
            contacts=agg,
        )

    async def aclose(self) -> None:
        await self.fetcher.aclose()
        await self.browser.close()


# --------------------------------------------------------------------------- #
# FastAPI app — scraper is a singleton so the browser stays warm
# --------------------------------------------------------------------------- #
_scraper: Optional[Scraper] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scraper
    _scraper = Scraper()
    logger.info("Scraper ready.")
    yield
    if _scraper:
        await _scraper.aclose()


app = FastAPI(title="Universal Scraper", lifespan=lifespan)


class ScrapeRequest(BaseModel):
    url: HttpUrl
    max_pages: int = DEFAULT_MAX_PAGES


@app.post("/scrape", response_model=ScrapeResult)
async def scrape_endpoint(req: ScrapeRequest) -> ScrapeResult:
    assert _scraper is not None
    try:
        return await _scraper.scrape(str(req.url), req.max_pages)
    except Exception as e:
        logger.exception("scrape failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python scraper.py <url> [max_pages]")
        raise SystemExit(1)

    target = sys.argv[1]
    pages = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_MAX_PAGES

    async def _main() -> None:
        s = Scraper()
        try:
            res = await s.scrape(target, pages)
            print(res.model_dump_json(indent=2))
        finally:
            await s.aclose()

    asyncio.run(_main())