"""System health endpoint."""

from fastapi import APIRouter

from api import __version__
from api.schemas import HealthResponse

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)
