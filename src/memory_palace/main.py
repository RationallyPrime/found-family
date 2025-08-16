"""Memory Palace FastAPI Application with Dream Job Integration.

This module implements MP-005 by integrating DreamJobOrchestrator into the
application lifecycle for automated memory management.
"""

# Configure Logfire and logging
# Pass token from environment if available
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import logfire
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from neo4j import AsyncDriver

from memory_palace.api import dependencies
from memory_palace.api.endpoints import admin, core, memory, unified_query
from memory_palace.api.oauth import router as oauth_router
from memory_palace.core.logging import get_logger, setup_logging
from memory_palace.infrastructure.embeddings.cache import EmbeddingCache
from memory_palace.infrastructure.embeddings.voyage import VoyageEmbeddingService
from memory_palace.infrastructure.neo4j.driver import (
    Neo4jQuery,
    create_neo4j_driver,
    ensure_vector_index,
)
from memory_palace.mcp.mcp_server import MemoryPalaceMCPServer
from memory_palace.services.clustering import DBSCANClusteringService
from memory_palace.services.dream_jobs import DreamJobOrchestrator
from memory_palace.services.memory_service import MemoryService

# Disable Logfire for now to avoid auth issues
# logfire.configure(
#     service_name="memory-palace",
#     token=os.getenv("LOGFIRE_TOKEN")
# )
setup_logging()
logger = get_logger(__name__)

# Global variables for application state
memory_service: MemoryService | None = None
dream_orchestrator: DreamJobOrchestrator | None = None
neo4j_driver: AsyncDriver | None = None
embedding_service: VoyageEmbeddingService | None = None
clustering_service: DBSCANClusteringService | None = None
mcp_server: MemoryPalaceMCPServer | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Application lifecycle manager with DreamJobOrchestrator integration."""
    global memory_service, dream_orchestrator, neo4j_driver, embedding_service, clustering_service, mcp_server

    logger.info("🧠 Starting Memory Palace application...")

    try:
        # Initialize Neo4j driver (keep it for the app lifetime)
        logger.info("📊 Initializing Neo4j connection...")
        neo4j_driver = None
        async for driver in create_neo4j_driver():
            neo4j_driver = driver
            break  # We get the driver from the generator

        if neo4j_driver is None:
            raise RuntimeError("Failed to initialize Neo4j driver")

        await ensure_vector_index(neo4j_driver)
        logger.info("✅ Vector index initialized")

        # Initialize embedding service with cache
        logger.info("🧮 Initializing Embedding Service...")
        embedding_cache = EmbeddingCache(neo4j_driver.session())
        embedding_service = VoyageEmbeddingService(cache=embedding_cache)

        # Initialize clustering service and load model
        logger.info("🔍 Initializing Clustering Service...")
        clustering_service = DBSCANClusteringService()
        async with neo4j_driver.session() as session:
            await clustering_service.load_model(session)

        # Set global dependencies for API endpoints
        dependencies.neo4j_driver = neo4j_driver
        dependencies.embedding_service = embedding_service
        dependencies.neo4j_query = Neo4jQuery(neo4j_driver)

        # Note: We'll create sessions per-request, not hold one open
        logger.info("💾 Services initialized and ready...")

        # Initialize Dream Job Orchestrator
        logger.info("🌙 Starting Dream Job Orchestrator...")
        dream_orchestrator = DreamJobOrchestrator(
            driver=neo4j_driver,
            embeddings=embedding_service,
            clusterer=clustering_service,
        )
        await dream_orchestrator.start()

        # Initialize MCP Server
        logger.info("🌐 Starting MCP Server...")
        mcp_server = MemoryPalaceMCPServer(base_url="http://localhost:8000")
        mcp_server.create_session_manager()
        
        # Mount the MCP server to the FastAPI app
        from starlette.routing import Mount
        app.router.routes.append(
            Mount("/mcp", app=mcp_server.session_manager.handle_request)
        )
        
        # Start the MCP server's session manager
        async with mcp_server.run():
            logger.info("✅ Memory Palace application started successfully!")
            logger.info("🔄 Background memory management is now active")
            logger.info("🌐 MCP Server ready for connections at /mcp")

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

# Disable Logfire instrumentation for now
# logfire.instrument_fastapi(app)

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
app.include_router(unified_query.router, prefix="/api/v1/unified", tags=["unified_query"])
app.include_router(core.router)
app.include_router(admin.router)
app.include_router(oauth_router)

# We'll add MCP mount after initialization in lifespan


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
