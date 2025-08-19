"""Core API endpoints for Memory Palace."""

from fastapi import APIRouter

from memory_palace.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.get("/")
async def root():
    """Root endpoint with application status."""
    return {
        "message": "Memory Palace API",
        "version": "0.1.0",
        "status": "running",
        "features": [
            "discriminated_unions",
            "specification_support",
            "dream_jobs",
            "graph_expansion",
            "ontology_boost",
        ],
    }


@router.get("/health", operation_id="health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "timestamp": "2025-08-07T15:20:00Z"}
