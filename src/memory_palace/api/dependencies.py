"""API dependencies."""

from collections.abc import AsyncGenerator

from fastapi import HTTPException
from neo4j import AsyncDriver

from memory_palace.infrastructure.embeddings.voyage import VoyageEmbeddingService
from memory_palace.infrastructure.neo4j.driver import Neo4jQuery
from memory_palace.services.clustering import DBSCANClusteringService
from memory_palace.services.memory_service import MemoryService

# These will be set by the main.py lifespan
neo4j_driver: AsyncDriver | None = None
neo4j_query: Neo4jQuery | None = None
embedding_service: VoyageEmbeddingService | None = None
clustering_service: DBSCANClusteringService | None = None


async def get_memory_service() -> AsyncGenerator[MemoryService, None]:
    """Get memory service instance with per-request session and proper cleanup.
    
    Uses async generator pattern to ensure session is properly closed after request.
    Injects the global clustering service to avoid reloading the model.
    """
    if neo4j_driver is None or embedding_service is None:
        raise HTTPException(
            status_code=503,
            detail="Services not initialized"
        )
    if clustering_service is None:
        raise HTTPException(
            status_code=503,
            detail="Clustering service not initialized"
        )
    
    # Create a new session for this request with proper lifecycle management
    async with neo4j_driver.session() as session:
        service = MemoryService(
            session=session,
            embeddings=embedding_service,
        )
        # Inject the global clustering service instead of creating a new one
        service.clusterer = clustering_service
        # No need to call initialize() since clusterer is already loaded
        
        try:
            yield service
        finally:
            # Session will be automatically closed by the context manager
            pass
