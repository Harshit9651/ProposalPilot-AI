from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class CrawledPage(BaseModel):
    url: HttpUrl
    title: str = ""
    description: str = ""
    content: str = ""
    markdown: str = ""

    status_code: int = 200
    word_count: int = 0

    metadata: dict[str, Any] = Field(default_factory=dict)


class CrawlResult(BaseModel):
    base_url: HttpUrl

    pages: list[CrawledPage] = Field(default_factory=list)

    total_pages: int = 0

    crawl_time_ms: float = 0

    created_at: datetime = Field(default_factory=datetime.utcnow)