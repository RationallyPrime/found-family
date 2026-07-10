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
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_mcp import AuthConfig, FastApiMCP
from neo4j import AsyncDriver
from starlette.middleware.trustedhost import TrustedHostMiddleware

from memory_palace.api import dependencies
from memory_palace.api.auth import require_remote_auth
from memory_palace.api.endpoints import admin, core, memory, oauth
from memory_palace.api.middleware import HTTPBoundaryMiddleware
from memory_palace.core.config import settings
from memory_palace.core.logging import get_logger, setup_logging
from memory_palace.domain.protocols import EmbeddingService
from memory_palace.infrastructure.embeddings.factory import create_embedding_service
from memory_palace.infrastructure.neo4j.driver import (
    ensure_embedding_compatibility,
    ensure_schema,
    ensure_vector_index,
    open_neo4j_driver,
)
from memory_palace.services.clustering import DBSCANClusteringService
from memory_palace.services.dream_jobs import DreamJobOrchestrator
from memory_palace.services.memory_service import MemoryService

# Enhanced Logfire configuration with proper instrumentation. Ambient CLI
# credentials must never turn local imports into implicit telemetry exports.
logfire_token = settings.logfire_token.get_secret_value()
logfire.configure(
    service_name="memory-palace",
    environment=settings.environment.value,
    token=logfire_token or None,
    send_to_logfire=bool(logfire_token),
    inspect_arguments=False,
    scrubbing=logfire.ScrubbingOptions(
        extra_patterns=["authorization", "cookie", "jwt", "memory.content", "oauth", "token"]
    ),
)

# Install auto-tracing with ignore for already imported modules
logfire.install_auto_tracing(
    modules=["memory_palace"],
    min_duration=0.01,  # Only trace operations over 0.01 seconds
    check_imported_modules="ignore",  # Ignore already imported modules
)
setup_logging()
logger = get_logger(__name__)

# Global variables for application state
memory_service: MemoryService | None = None
dream_orchestrator: DreamJobOrchestrator | None = None
neo4j_driver: AsyncDriver | None = None
embedding_service: EmbeddingService | None = None
clustering_service: DBSCANClusteringService | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Application lifecycle manager with DreamJobOrchestrator integration."""
    global memory_service, dream_orchestrator, neo4j_driver, embedding_service, clustering_service

    logger.info("🧠 Starting Memory Palace application...")

    try:
        settings.validate_runtime()

        # Initialize Neo4j driver (keep it for the app lifetime)
        logger.info("📊 Initializing Neo4j connection...")
        neo4j_driver = await open_neo4j_driver()
        await ensure_schema(neo4j_driver)

        # Initialize embedding service with dependency injection
        logger.info("🧮 Initializing Embedding Service...")
        embedding_service = create_embedding_service(neo4j_driver=neo4j_driver, use_cache=True)

        # Get the actual embedding dimensions from the service
        embedding_dims = embedding_service.get_model_dimensions()
        logger.info(f"📏 Using embedding model with {embedding_dims} dimensions")

        await ensure_embedding_compatibility(
            neo4j_driver,
            model=settings.voyage_model,
            dimensions=embedding_dims,
        )

        # Ensure vector index exists with correct dimensions
        await ensure_vector_index(neo4j_driver, dimensions=embedding_dims)
        logger.info("✅ Vector index initialized with correct dimensions")

        # Initialize clustering service and load model
        logger.info("🔍 Initializing Clustering Service...")
        clustering_service = DBSCANClusteringService()

        # Set global dependencies for API endpoints
        dependencies.neo4j_driver = neo4j_driver
        dependencies.embedding_service = embedding_service
        dependencies.clustering_service = clustering_service

        # OAuth state store (client registrations survive restarts)
        from memory_palace.infrastructure.oauth import Neo4jOAuthStateStore

        _app.state.oauth_store = Neo4jOAuthStateStore(neo4j_driver)

        # Note: We'll create sessions per-request, not hold one open
        logger.info("💾 Services initialized and ready...")

        # Initialize Dream Job Orchestrator (optional)
        if os.getenv("DISABLE_DREAM_JOBS", "false").lower() != "true":
            logger.info("🌙 Starting Dream Job Orchestrator...")
            dream_orchestrator = DreamJobOrchestrator(
                driver=neo4j_driver,
                embeddings=embedding_service,
                clusterer=clustering_service,
            )
            await dream_orchestrator.start()
        else:
            logger.info("🌙 Dream Job Orchestrator disabled by environment variable")

        logger.info("✅ Memory Palace application started successfully!")
        logger.info("🔄 Background memory management is now active")

        yield  # Application is running

    except Exception as e:
        from memory_palace.core.base import ServiceErrorDetails
        from memory_palace.core.errors import ServiceError

        logger.error("❌ Failed to start Memory Palace", exc_info=True)
        raise ServiceError(
            message=f"Failed to start Memory Palace application: {e}",
            details=ServiceErrorDetails(
                source="main", operation="lifespan_startup", service_name="memory_palace", endpoint="/", status_code=500
            ),
        ) from e

    finally:
        # Shutdown sequence
        logger.info("🛑 Shutting down Memory Palace...")

        if dream_orchestrator:
            logger.info("🌙 Stopping Dream Job Orchestrator...")
            await dream_orchestrator.shutdown()
            logger.info("✅ Dream Job Orchestrator stopped")

        if neo4j_driver:
            logger.info("📊 Closing Neo4j connection...")
            await neo4j_driver.close()
            logger.info("✅ Neo4j connection closed")

        logger.info("✅ Memory Palace shutdown complete")


# Create FastAPI app with lifespan management
app = FastAPI(
    title="Memory Palace API",
    description="Advanced memory management system for AI conversations",
    version="0.1.0",
    lifespan=lifespan,
    debug=settings.debug,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
)

# Enable FastAPI instrumentation for request tracing
logfire.instrument_fastapi(app)

# Add CORS middleware. Wildcard origins + credentials is a spec-invalid
# combination browsers reject; MCP clients are not browsers and ignore CORS,
# so this only needs to cover actual browser frontends.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_values,
    allow_credentials=True,
    allow_methods=["GET", "HEAD", "POST"],
    allow_headers=["Authorization", "Content-Type", "Mcp-Protocol-Version", "X-Correlation-ID", "X-Request-ID"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
app.add_middleware(HTTPBoundaryMiddleware, max_request_body_bytes=settings.max_request_body_bytes)

# Mount API routers.
# Memory and admin routes are gated: requests arriving through the
# Cloudflare tunnel must carry a valid Bearer JWT; local traffic is trusted.
# OAuth/well-known and health stay public — the flow needs them.
app.include_router(memory.router, prefix="/api/v1/memory", tags=["memory"])
app.include_router(core.router)
app.include_router(admin.router)
app.include_router(oauth.router)  # Include OAuth endpoints for Claude.ai MCP

# Add MCP support — expose only the memory verbs as tools.
# OAuth/discovery endpoints stay HTTP-only; a memory palace's tool list
# should read like memory: remember, recall, awaken, forget.
mcp = FastApiMCP(
    app,
    name="Memory Palace",
    description="Persistent memory for AI continuity of experience: remember, recall, awaken, forget.",
    include_operations=[
        "remember",
        "remember_batch",
        "recall",
        "awaken",
        "forget",
        "health",
    ],
    # Same gate as the REST routers: tunnel traffic needs a Bearer JWT.
    # Internal tool execution forwards only the Authorization header
    # (fastapi-mcp allowlist), never the tunnel headers, so tool calls
    # authenticated at /mcp pass through cleanly.
    auth_config=AuthConfig(dependencies=[Depends(require_remote_auth)]),
)
mcp.mount_http()  # Creates MCP server at /mcp with HTTPS support


if __name__ == "__main__":
    """Development server entry point."""
    logger.info("🚀 Starting Memory Palace development server...")

    uvicorn.run("memory_palace.main:app", host="127.0.0.1", port=8000, reload=True, log_level="info", access_log=True)
