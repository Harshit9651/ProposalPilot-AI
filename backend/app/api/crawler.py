from fastapi import APIRouter

from app.crawler.crawler_service import CrawlerService
from app.schemas.crawler import CrawlRequest
from app.schemas.response import ApiResponse

router = APIRouter(prefix="/crawler", tags=["Crawler"])

crawler_service = CrawlerService()


@router.post("", response_model=ApiResponse)
async def crawl_website(request: CrawlRequest):

    result = await crawler_service.crawl(str(request.url))

    return ApiResponse(
        success=True,
        message="Website crawled successfully",
        data=result.model_dump(),
    )