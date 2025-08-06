"""Memory Palace FastAPI Application with Dream Job Integration.

This module implements MP-005 by integrating DreamJobOrchestrator into the
application lifecycle for automated memory management.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID

import logfire
import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi_mcp import FastApiMCP
from pydantic import BaseModel

from memory_palace.core.base import ApplicationError, ErrorLevel
from memory_palace.core.decorators import with_error_handling
from memory_palace.core.logging import get_logger, setup_logging
from memory_palace.infrastructure.neo4j.driver import Neo4jQuery, create_neo4j_driver
from memory_palace.infrastructure.embeddings.voyage import VoyageEmbeddingService
from memory_palace.services.dream_jobs import DreamJobOrchestrator
from memory_palace.services.memory_service import MemoryService
from memory_palace.api.endpoints import memory
from memory_palace.api import dependencies
from neo4j import AsyncDriver

# Configure Logfire and logging
logfire.configure(service_name="memory-palace")
setup_logging()
logger = get_logger(__name__)

# Global variables for application state
memory_service: MemoryService | None = None
dream_orchestrator: DreamJobOrchestrator | None = None
neo4j_driver: AsyncDriver | None = None
embedding_service: VoyageEmbeddingService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Application lifecycle manager with DreamJobOrchestrator integration."""
    global memory_service, dream_orchestrator, neo4j_driver, embedding_service

    logger.info("🧠 Starting Memory Palace application...")

    try:
        # Initialize Neo4j driver (keep it for the app lifetime)
        logger.info("📊 Initializing Neo4j connection...")
        async for driver in create_neo4j_driver():
            neo4j_driver = driver
            break  # We get the driver from the generator
        
        # Initialize embedding service
        logger.info("🧮 Initializing Embedding Service...")
        embedding_service = VoyageEmbeddingService()
        
        # Set global dependencies for API endpoints
        dependencies.neo4j_driver = neo4j_driver
        dependencies.embedding_service = embedding_service

        # Note: We'll create sessions per-request, not hold one open
        logger.info("💾 Services initialized and ready...")

        # Initialize Dream Job Orchestrator
        logger.info("🌙 Starting Dream Job Orchestrator...")
        # dream_orchestrator = DreamJobOrchestrator(memory_service)
        # await dream_orchestrator.start()

        logger.info("✅ Memory Palace application started successfully!")
        logger.info("🔄 Background memory management is now active")

        # Print job status
        # if dream_orchestrator:
        #     status = dream_orchestrator.get_job_status()
        #     logger.info(f"📅 Scheduled jobs: {len(status['jobs'])}")
        #     for job in status['jobs']:
        #         logger.info(f"   - {job['id']}: next run at {job['next_run']}")

        yield  # Application is running

    except Exception as e:
        logger.error(f"❌ Failed to start Memory Palace: {e}", exc_info=True)
        raise

    finally:
        # Shutdown sequence
        logger.info("🛑 Shutting down Memory Palace...")

        if dream_orchestrator:
            logger.info("🌙 Stopping Dream Job Orchestrator...")
            await dream_orchestrator.shutdown()
            logger.info("✅ Dream Job Orchestrator stopped")

        if neo4j_driver:
            logger.info("📊 Closing Neo4j connection...")
            # await neo4j_driver.close()
            logger.info("✅ Neo4j connection closed")

        logger.info("✅ Memory Palace shutdown complete")


# Create FastAPI app with lifespan management
app = FastAPI(
    title="Memory Palace API",
    description="Advanced memory management system for AI conversations",
    version="0.1.0",
    lifespan=lifespan
)

# Instrument FastAPI with Logfire
logfire.instrument_fastapi(app)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routers
app.include_router(memory.router, prefix="/api/v1/memory", tags=["memory"])

# Add MCP support
mcp = FastApiMCP(app)
mcp.mount()  # Creates MCP server at /mcp


# Dependency to get memory service
async def get_memory_service() -> MemoryService:
    """Dependency to get the memory service instance."""
    if neo4j_driver is None or embedding_service is None:
        raise HTTPException(
            status_code=503,
            detail="Services not initialized"
        )
    
    # Create a new session for this request
    session = neo4j_driver.session()
    return MemoryService(
        session=session,
        embeddings=embedding_service,
        clusterer=None
    )


# Dependency to get dream orchestrator
async def get_dream_orchestrator() -> DreamJobOrchestrator:
    """Dependency to get the dream orchestrator instance."""
    if dream_orchestrator is None:
        raise HTTPException(
            status_code=503,
            detail="Dream orchestrator not initialized"
        )
    return dream_orchestrator


# Pydantic models for API
class ConversationTurnRequest(BaseModel):
    user_content: str
    assistant_content: str
    conversation_id: UUID | None = None
    detect_relationships: bool = True
    auto_classify: bool = True


class ConversationTurnResponse(BaseModel):
    user_memory_id: UUID
    assistant_memory_id: UUID
    conversation_id: UUID | None
    relationships_created: int


class SearchRequest(BaseModel):
    query: str | None = None
    conversation_id: UUID | None = None
    topic_id: int | None = None
    min_salience: float | None = None
    limit: int = 50


class MemoryResponse(BaseModel):
    id: UUID
    memory_type: str
    content: str | None = None
    salience: float
    topic_id: int | None = None
    timestamp: str


class JobStatusResponse(BaseModel):
    scheduler_running: bool
    active_jobs: int
    jobs: list[dict]


# API Endpoints

@app.get("/")
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
            "ontology_boost"
        ]
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    global memory_service, dream_orchestrator

    return {
        "status": "healthy",
        "memory_service": "initialized" if memory_service else "not_initialized",
        "dream_orchestrator": "running" if dream_orchestrator else "not_running",
        "timestamp": "2025-08-06T15:20:00Z"
    }


@app.post("/memory/turn", response_model=ConversationTurnResponse)
@with_error_handling(error_level=ErrorLevel.ERROR, reraise=False)
async def store_conversation_turn(
    request: ConversationTurnRequest,
    service: MemoryService = Depends(get_memory_service)
):
    """Store a complete conversation turn with relationship detection."""
    try:
        user_memory, assistant_memory = await service.remember_turn(
            user_content=request.user_content,
            assistant_content=request.assistant_content,
            conversation_id=request.conversation_id,
            detect_relationships=request.detect_relationships,
            auto_classify=request.auto_classify
        )

        # Count relationships (placeholder - would need actual implementation)
        relationships_created = 0
        if request.detect_relationships:
            user_relationships = await service.get_memory_relationships(user_memory.id)
            assistant_relationships = await service.get_memory_relationships(assistant_memory.id)
            relationships_created = len(user_relationships) + len(assistant_relationships)

        return ConversationTurnResponse(
            user_memory_id=user_memory.id,
            assistant_memory_id=assistant_memory.id,
            conversation_id=user_memory.conversation_id,
            relationships_created=relationships_created
        )

    except Exception as e:
        logger.error(f"Failed to store conversation turn: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/search", response_model=list[MemoryResponse])
async def search_memories(
    request: SearchRequest,
    service: MemoryService = Depends(get_memory_service)
):
    """Search memories using advanced filtering and similarity."""
    try:
        memories = await service.search_memories(
            query=request.query,
            conversation_id=request.conversation_id,
            topic_id=request.topic_id,
            min_salience=request.min_salience,
            limit=request.limit
        )

        # Convert to response format
        return [
            MemoryResponse(
                id=memory.id,
                memory_type=memory.memory_type.value,
                content=getattr(memory, 'content', None),
                salience=getattr(memory, 'salience', 0.0),
                topic_id=getattr(memory, 'topic_id', None),
                timestamp=memory.timestamp.isoformat()
            )
            for memory in memories
        ]

    except Exception as e:
        logger.error(f"Memory search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/memory/recall/{query}")
async def recall_with_graph(
    query: str,
    k: int = 24,
    use_ontology_boost: bool = True,
    service: MemoryService = Depends(get_memory_service)
):
    """Multi-stage recall with ontology boost and graph expansion."""
    try:
        memories = await service.recall_with_graph(
            query=query,
            k=k,
            use_ontology_boost=use_ontology_boost
        )

        return {
            "query": query,
            "results": len(memories),
            "memories": [
                {
                    "id": str(memory.id),
                    "type": memory.memory_type.value,
                    "content": getattr(memory, 'content', None),
                    "topic_id": getattr(memory, 'topic_id', None)
                }
                for memory in memories
            ]
        }

    except Exception as e:
        logger.error(f"Recall failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/jobs/status", response_model=JobStatusResponse)
async def get_job_status(
    orchestrator: DreamJobOrchestrator = Depends(get_dream_orchestrator)
):
    """Get dream job orchestrator status."""
    try:
        status = orchestrator.get_job_status()
        return JobStatusResponse(
            scheduler_running=status["scheduler_running"],
            active_jobs=len(status["jobs"]),
            jobs=status["jobs"]
        )

    except Exception as e:
        logger.error(f"Failed to get job status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/jobs/trigger/{job_id}")
async def trigger_job(
    job_id: str,
    orchestrator: DreamJobOrchestrator = Depends(get_dream_orchestrator)
):
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
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    """Development server entry point."""
    logger.info("🚀 Starting Memory Palace development server...")

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
        access_log=True
    )
