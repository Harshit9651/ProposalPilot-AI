from fastapi import APIRouter

from app.schemas.response import ApiResponse
from app.schemas.scraper import ScrapeRequest
from app.scraper.scraper_service import Scraper

router = APIRouter(
    prefix="/scraper",
    tags=["Scraper"],
)

scraper_service = Scraper()


@router.post("", response_model=ApiResponse)
async def scrape_website(request: ScrapeRequest):

    result = await scraper_service.scrape(
        url=str(request.url),
        max_pages=request.max_pages,
    )

    return ApiResponse(
        success=True,
        message="Website scraped successfully",
        data=result.model_dump(),
    )