"""API module."""

from fastapi import APIRouter

from .endpoints import memory

router = APIRouter()

# Include endpoint routers
router.include_router(memory.router, prefix="/memory", tags=["memory"])
