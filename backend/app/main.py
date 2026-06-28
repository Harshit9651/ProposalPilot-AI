from fastapi import FastAPI
from app.api.routes import router
from app.core.config import settings
from app.core.logger import logger
from app.exceptions.handlers import register_exception_handlers
from app.middleware.request_logger import RequestLoggingMiddleware
from fastapi.middleware.cors import CORSMiddleware
# from app.api.crawler import router as crawler_router
from app.api.scraper import router as scraper_router


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="AI Powered Business Analysis Platform",
    debug=settings.DEBUG,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
register_exception_handlers(app)
app.add_middleware(RequestLoggingMiddleware)


app.include_router(
    router,
    prefix="/api/v1",
    tags=["System"]
)
# app.include_router(
#     crawler_router,
#     prefix="/api/v1",
# )
app.include_router(
    scraper_router,
    prefix="/api/v1",
)


logger.info("🚀 ProposalPilot AI Started Successfully")