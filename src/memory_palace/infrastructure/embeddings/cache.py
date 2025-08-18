import hashlib

from neo4j import AsyncDriver


class EmbeddingCache:
    """Neo4j-backed cache for embedding vectors with model awareness.
    
    The cache now tracks which model generated each embedding to prevent
    serving stale embeddings when models are switched.
    """

    def __init__(self, driver: AsyncDriver):
        """Initialize cache with a driver instead of session for proper lifecycle."""
        self.driver = driver

    async def get_cached(self, text: str, model: str) -> list[float] | None:
        """Retrieve a cached embedding if available, not expired, and from the same model.
        
        Args:
            text: The text that was embedded
            model: The model name used for embedding
            
        Returns:
            The cached embedding vector or None if not found/expired/wrong model
        """
        # Include model in cache key to prevent cross-model contamination
        cache_key = hashlib.md5(f"{model}::{text}".encode()).hexdigest()
        
        async with self.driver.session() as session:
            result = await session.run(
                """
                MATCH (e:EmbeddingCache {cache_key: $key, model: $model})
                WHERE e.created > datetime() - duration('P30D')
                SET e.hit_count = COALESCE(e.hit_count, 0) + 1
                RETURN e.vector AS embedding
                """,
                key=cache_key,
                model=model,
            )
            record = await result.single()
            return record["embedding"] if record else None

    async def store(self, text: str, model: str, embedding: list[float], dimensions: int) -> None:
        """Store an embedding in the cache with model metadata.
        
        Args:
            text: The text that was embedded
            model: The model name used for embedding
            embedding: The embedding vector
            dimensions: The number of dimensions in the embedding
        """
        # Include model in cache key
        cache_key = hashlib.md5(f"{model}::{text}".encode()).hexdigest()
        
        async with self.driver.session() as session:
            await session.run(
                """
                MERGE (e:EmbeddingCache {cache_key: $key, model: $model})
                ON CREATE SET e.hit_count = 0
                SET e.vector = $embedding,
                    e.dimensions = $dimensions,
                    e.created = datetime(),
                    e.text_preview = LEFT($text, 100)
                """,
                key=cache_key,
                model=model,
                embedding=embedding,
                dimensions=dimensions,
                text=text,
            )
