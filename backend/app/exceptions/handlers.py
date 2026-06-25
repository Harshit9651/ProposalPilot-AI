from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.exceptions.custom_exceptions import ProposalPilotException


def register_exception_handlers(app: FastAPI):

    @app.exception_handler(ProposalPilotException)
    async def proposal_pilot_exception_handler(
        request: Request,
        exc: ProposalPilotException,
    ):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "message": exc.message,
                "data": None,
            },
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(
        request: Request,
        exc: Exception,
    ):
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "Internal Server Error",
                "data": None,
            },
        )