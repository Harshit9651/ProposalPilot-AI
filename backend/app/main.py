from fastapi import FastAPI

from app.api.routes import router
from app.core.config import settings
from app.core.logger import logger

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    debug=settings.DEBUG,
    description="AI Powered Business Analysis Platform"
)

app.include_router(router, prefix="/api/v1", tags=["System"])

logger.info("🚀 ProposalPilot AI Started Successfully")