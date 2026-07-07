import hashlib

from neo4j import AsyncDriver

from memory_palace.core.decorators import with_session
from memory_palace.infrastructure.neo4j.queries import CacheQueries


class EmbeddingCache:
    """Neo4j-backed cache for embedding vectors with model awareness.

    The cache tracks which model generated each embedding to prevent
    serving stale embeddings when models are switched.
    """

    def __init__(self, driver: AsyncDriver):
        """Initialize cache with a driver instead of session for proper lifecycle."""
        self.driver = driver

    @staticmethod
    def _cache_key(text: str, model: str) -> str:
        """Model-scoped cache key to prevent cross-model contamination."""
        return hashlib.md5(f"{model}::{text}".encode()).hexdigest()

    @with_session()
    async def get_cached(self, session, text: str, model: str) -> list[float] | None:
        """Retrieve a cached embedding if available, not expired, and from the same model."""
        query, _ = CacheQueries.get_cached_embedding()
        result = await session.run(query, key=self._cache_key(text, model), model=model)
        record = await result.single()
        return record["embedding"] if record else None

    @with_session()
    async def store(self, session, text: str, model: str, embedding: list[float], dimensions: int) -> None:
        """Store an embedding in the cache with model metadata."""
        query, _ = CacheQueries.store_embedding()
        await session.run(
            query,
            key=self._cache_key(text, model),
            model=model,
            embedding=embedding,
            dimensions=dimensions,
            text=text,
        )
