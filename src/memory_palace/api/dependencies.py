"""API dependencies."""
from functools import lru_cache

from memory_palace.core.config import settings
from memory_palace.infrastructure.embeddings import VoyageEmbeddingService
from memory_palace.infrastructure.neo4j.simple_driver import get_driver
from memory_palace.services.memory_service import MemoryService


@lru_cache()
def get_embedding_service() -> VoyageEmbeddingService:
    """Get embedding service singleton."""
    return VoyageEmbeddingService(api_key=settings.voyage_api_key)


@lru_cache()
def get_neo4j_driver():
    """Get Neo4j driver singleton."""
    return get_driver(
        uri=settings.neo4j_uri,
        username=settings.neo4j_user,
        password=settings.neo4j_password,
    )


def get_memory_service() -> MemoryService:
    """Get memory service instance."""
    return MemoryService(
        neo4j_driver=get_neo4j_driver(),
        embedding_service=get_embedding_service(),
    )