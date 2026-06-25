from fastapi import APIRouter

router = APIRouter()


@router.get("/")
def root():
    return {
        "message": "ProposalPilot AI Running "
    }


@router.get("/health")
def health():
    return {
        "status": "healthy"
    }