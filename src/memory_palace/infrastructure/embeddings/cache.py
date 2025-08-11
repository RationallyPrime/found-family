import hashlib

from neo4j import AsyncSession


class EmbeddingCache:
    """Neo4j-backed cache for embedding vectors."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_cached(self, text: str) -> list[float] | None:
        """Retrieve a cached embedding if available and not expired."""
        text_hash = hashlib.md5(text.encode()).hexdigest()
        result = await self.session.run(
            """
            MATCH (e:EmbeddingCache {text_hash: $hash})
            WHERE e.created > datetime() - duration('P30D')
            SET e.hit_count = COALESCE(e.hit_count, 0) + 1
            RETURN e.vector AS embedding
            """,
            hash=text_hash,
        )
        record = await result.single()
        return record["embedding"] if record else None

    async def store(self, text: str, embedding: list[float]) -> None:
        """Store an embedding in the cache."""
        text_hash = hashlib.md5(text.encode()).hexdigest()
        await self.session.run(
            """
            MERGE (e:EmbeddingCache {text_hash: $hash})
            ON CREATE SET e.hit_count = 0
            SET e.vector = $embedding,
                e.created = datetime()
            """,
            hash=text_hash,
            embedding=embedding,
        )
