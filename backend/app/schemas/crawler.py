from pydantic import BaseModel, HttpUrl


class CrawlRequest(BaseModel):
    url: HttpUrl