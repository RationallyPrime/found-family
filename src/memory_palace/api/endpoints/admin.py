"""Admin endpoints for Memory Palace management."""

from fastapi import APIRouter, Depends, HTTPException
from neo4j import AsyncDriver
from pydantic import BaseModel

from memory_palace.core.decorators import with_error_handling
from memory_palace.core.logging import get_logger
from memory_palace.services.dream_jobs import DreamJobOrchestrator

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


class JobStatusResponse(BaseModel):
    scheduler_running: bool
    active_jobs: int
    jobs: list[dict]


# Dependency to get dream orchestrator
async def get_dream_orchestrator() -> DreamJobOrchestrator:
    """Dependency to get the dream orchestrator instance."""
    # Import here to avoid circular dependency
    from memory_palace.main import dream_orchestrator

    if dream_orchestrator is None:
        raise HTTPException(status_code=503, detail="Dream orchestrator not initialized")
    return dream_orchestrator


# Dependency to get Neo4j driver
async def get_neo4j_driver():
    """Get the global Neo4j driver instance."""
    from memory_palace.main import neo4j_driver

    if neo4j_driver is None:
        raise HTTPException(status_code=503, detail="Neo4j driver not initialized")
    return neo4j_driver


@router.get("/jobs/status", response_model=JobStatusResponse, operation_id="job_status")
@with_error_handling(reraise=True)
async def get_job_status(orchestrator: DreamJobOrchestrator = Depends(get_dream_orchestrator)):
    """Get dream job orchestrator status."""
    status = orchestrator.get_job_status()
    return JobStatusResponse(
        scheduler_running=status["scheduler_running"], active_jobs=len(status["jobs"]), jobs=status["jobs"]
    )


@router.post("/jobs/trigger/{job_id}", operation_id="trigger")
@with_error_handling(reraise=True)
async def trigger_job(job_id: str, orchestrator: DreamJobOrchestrator = Depends(get_dream_orchestrator)):
    """Manually trigger a specific dream job."""
    jobs = {
        "salience_decay": orchestrator.decay_and_archive,
        "cluster_recent": orchestrator.cluster_recent,
        "nightly_recluster": orchestrator.nightly_recluster,
    }
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found. Available: {sorted(jobs)}")

    await job()
    return {"message": f"Job {job_id} triggered successfully"}


@router.get("/cache/stats", operation_id="cache_stats")
async def get_cache_stats(driver: AsyncDriver = Depends(get_neo4j_driver)):
    """Get basic statistics about the embedding cache."""
    from memory_palace.infrastructure.neo4j.queries import CacheQueries

    query, params = CacheQueries.get_cache_stats()

    async with driver.session() as session:
        result = await session.run(query, params)
        record = await result.single()
        if record is None:
            return {"size": 0, "total_hits": 0}
        return {
            "size": record.get("size", 0),
            "total_hits": record.get("total_hits", 0),
        }
