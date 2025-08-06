"""API dependencies."""

from fastapi import HTTPException
from neo4j import AsyncDriver

from memory_palace.infrastructure.embeddings.voyage import VoyageEmbeddingService
from memory_palace.services.memory_service import MemoryService

# These will be set by the main.py lifespan
neo4j_driver: AsyncDriver | None = None
embedding_service: VoyageEmbeddingService | None = None


async def get_memory_service() -> MemoryService:
    """Get memory service instance with per-request session."""
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
