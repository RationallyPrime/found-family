"""Liveness, readiness, and minimal service metadata."""

import asyncio
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from memory_palace.api import dependencies
from memory_palace.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


class RootResponse(BaseModel):
    message: str
    version: str
    status: Literal["running"]
    features: list[str]


class HealthResponse(BaseModel):
    status: Literal["healthy"]
    timestamp: datetime


class ReadinessResponse(BaseModel):
    status: Literal["ready"]


@router.get("/", response_model=RootResponse)
async def root() -> RootResponse:
    """Root endpoint with application status."""
    return RootResponse(
        message="Memory Palace API",
        version="0.1.0",
        status="running",
        features=[
            "discriminated_unions",
            "specification_support",
            "dream_jobs",
            "graph_expansion",
            "ontology_boost",
        ],
    )


@router.get("/health", response_model=HealthResponse, operation_id="health")
async def health_check() -> HealthResponse:
    """Process liveness; intentionally independent of downstream services."""
    return HealthResponse(status="healthy", timestamp=datetime.now(UTC))


@router.get("/ready", response_model=ReadinessResponse, operation_id="readiness")
async def readiness_check() -> ReadinessResponse:
    """Return ready only when all request dependencies and Neo4j are usable."""
    driver = dependencies.neo4j_driver
    if driver is None or dependencies.embedding_service is None or dependencies.clustering_service is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    try:
        async with asyncio.timeout(2.0):
            await driver.verify_connectivity()
    except Exception:
        logger.warning("Readiness dependency check failed", exc_info=True)
        raise HTTPException(status_code=503, detail="Service not ready") from None

    return ReadinessResponse(status="ready")
