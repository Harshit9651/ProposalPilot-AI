from datetime import datetime

from app.core.config import settings


class HealthService:

    @staticmethod
    def get_health():
        return {
            "status": "healthy",
            "application": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "timestamp": datetime.utcnow().isoformat()
        }