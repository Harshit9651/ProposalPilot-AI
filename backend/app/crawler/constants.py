from typing import Final
# HTTP
DEFAULT_TIMEOUT: Final[int] = 20

MAX_RETRIES: Final[int] = 2

CONCURRENT_REQUESTS: Final[int] = 10

MAX_REDIRECTS: Final[int] = 5
# Crawling
DEFAULT_MAX_PAGES: Final[int] = 100

MAX_CONTENT_LENGTH: Final[int] = 100_000

MAX_LINKS_PER_PAGE: Final[int] = 200
# User Agent
USER_AGENT: Final[str] = (
    "ProposalPilotBot/1.0 (+https://proposalpilot.ai)"
)
# File Extensions to Ignore
IGNORED_EXTENSIONS: Final[tuple[str, ...]] = (
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".css",
    ".js",
    ".mjs",
    ".xml",
    ".json",
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
    ".mp3",
    ".mp4",
    ".avi",
    ".mov",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
)

# HTML Tags to Remove
REMOVE_TAGS: Final[tuple[str, ...]] = (
    "script",
    "style",
    "noscript",
    "iframe",
    "svg",
    "canvas",
    "form",
)



# Priority Pages

PRIORITY_PATHS: Final[tuple[str, ...]] = (
    "",
    "/",
    "/about",
    "/about-us",
    "/services",
    "/products",
    "/solutions",
    "/pricing",
    "/contact",
    "/portfolio",
    "/case-studies",
)


# Skip Paths


SKIP_PATHS: Final[tuple[str, ...]] = (
    "/login",
    "/signup",
    "/register",
    "/logout",
    "/cart",
    "/checkout",
    "/privacy",
    "/privacy-policy",
    "/terms",
    "/terms-of-service",
    "/cookie-policy",
)