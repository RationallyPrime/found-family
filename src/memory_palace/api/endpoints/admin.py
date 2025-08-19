"""Admin endpoints for Memory Palace management."""

from fastapi import APIRouter, Depends, HTTPException
from neo4j import AsyncDriver
from pydantic import BaseModel

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
async def get_job_status(orchestrator: DreamJobOrchestrator = Depends(get_dream_orchestrator)):
    """Get dream job orchestrator status."""
    try:
        status = orchestrator.get_job_status()
        return JobStatusResponse(
            scheduler_running=status["scheduler_running"], active_jobs=len(status["jobs"]), jobs=status["jobs"]
        )

    except Exception as e:
        logger.error(f"Failed to get job status: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/jobs/trigger/{job_id}", operation_id="trigger")
async def trigger_job(job_id: str, orchestrator: DreamJobOrchestrator = Depends(get_dream_orchestrator)):
    """Manually trigger a specific dream job."""
    try:
        if job_id == "salience_refresh":
            await orchestrator.refresh_salience()
        elif job_id == "cluster_recent":
            await orchestrator.cluster_recent()
        elif job_id == "nightly_recluster":
            await orchestrator.nightly_recluster()
        else:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        return {"message": f"Job {job_id} triggered successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to trigger job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/cache/stats", operation_id="cache_stats")
async def get_cache_stats(driver: AsyncDriver = Depends(get_neo4j_driver)):
    """Get basic statistics about the embedding cache."""
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (e:EmbeddingCache)
            RETURN count(e) AS size,
                   sum(coalesce(e.hit_count,0)) AS total_hits
            """
        )
        record = await result.single()
        return {
            "size": record.get("size", 0),
            "total_hits": record.get("total_hits", 0),
        }
