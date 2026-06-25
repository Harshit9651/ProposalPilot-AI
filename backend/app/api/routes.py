from fastapi import APIRouter

from app.schemas.response import ApiResponse
from app.services.health_service import HealthService
from app.exceptions.custom_exceptions import ProposalPilotException

router = APIRouter()


@router.get("/", response_model=ApiResponse)
def root():
    return ApiResponse(
        success=True,
        message="Application started successfully",
        data={
            "application": "ProposalPilot AI"
        }
    )


@router.get("/health", response_model=ApiResponse)
def health():

    health_data = HealthService.get_health()

    return ApiResponse(
        success=True,
        message="Health check successful",
        data=health_data
    )


@router.get("/test-error", response_model=ApiResponse)
def test_error():
    raise ProposalPilotException(
        message="This is a custom exception.",
        status_code=400
    )