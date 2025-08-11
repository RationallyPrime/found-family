import logging
from typing import Any, Generic, TypeVar
from uuid import UUID

from neo4j import AsyncSession

from memory_palace.domain.models.base import GraphModel
from memory_palace.domain.models.memories import Memory

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=GraphModel)


class GenericMemoryRepository(Generic[T]):
    """Generic repository for all memory types using discriminated unions and type safety."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def remember(self, memory: T) -> T:
        """Store any type of memory with full type safety."""
        try:
            labels = memory.labels()
            labels_str = ":".join(labels)
            properties = memory.to_neo4j_properties()

            logger.debug(f"Storing {memory.__class__.__name__} with labels: {labels}")

            # Use MERGE to handle both create and update scenarios
            query = f"""
            MERGE (m:{labels_str} {{id: $id}})
            SET m += $properties
            RETURN m
            """

            result = await self.session.run(
                query,
                id=str(memory.id),
                properties=properties
            )

            # Verify the memory was stored
            record = await result.single()
            if not record:
                raise RuntimeError(f"Failed to store memory {memory.id}")

            logger.debug(f"Successfully stored memory {memory.id}")
            return memory

        except Exception as e:
            logger.error(f"Failed to store memory {memory.id}: {e}")
            raise

    async def recall(
        self,
        memory_type: type[T],
        filters: dict[str, Any] | None = None,
        similarity_search: tuple[list[float], float] | None = None,
        limit: int = 100,
        offset: int = 0
    ) -> list[T]:
        """Recall memories with type safety and optional similarity search."""
        try:
            # Get labels from the memory type class
            labels = memory_type.labels()
            labels_str = ":".join(labels)

            if similarity_search:
                embedding, threshold = similarity_search
                query = f"""
                CALL db.index.vector.queryNodes('memory_embeddings', $k, $embedding)
                YIELD node, score
                WHERE node:{labels_str} AND score > $threshold
                {self._build_filter_clause(filters, alias='node') if filters else ''}
                RETURN node as m, score as similarity
                ORDER BY similarity DESC
                SKIP $offset LIMIT $limit
                """
                params = {
                    "embedding": embedding,
                    "threshold": threshold,
                    "offset": offset,
                    "limit": limit,
                    "k": limit,
                    **(filters or {})
                }
            else:
                query = f"""
                MATCH (m:{labels_str})
                {self._build_where_clause(filters)}
                RETURN m
                ORDER BY m.timestamp DESC
                SKIP $offset LIMIT $limit
                """
                params = {"offset": offset, "limit": limit, **(filters or {})}

            result = await self.session.run(query, **params)

            memories = []
            async for record in result:
                try:
                    memory = self._record_to_memory(record["m"], memory_type)
                    memories.append(memory)
                except Exception as e:
                    logger.warning(f"Failed to deserialize memory record: {e}")
                    continue

            logger.debug(f"Recalled {len(memories)} memories of type {memory_type.__name__}")
            return memories

        except Exception as e:
            logger.error(f"Failed to recall memories of type {memory_type.__name__}: {e}")
            return []

    async def get_by_id(self, memory_id: UUID, memory_type: type[T]) -> T | None:
        """Get a specific memory by ID with type safety."""
        try:
            labels = memory_type.labels()
            labels_str = ":".join(labels)

            query = f"""
            MATCH (m:{labels_str} {{id: $id}})
            RETURN m
            """

            result = await self.session.run(query, id=str(memory_id))
            record = await result.single()

            if record:
                return self._record_to_memory(record["m"], memory_type)
            return None

        except Exception as e:
            logger.error(f"Failed to get memory {memory_id}: {e}")
            return None

    async def connect(
        self,
        source_id: UUID,
        target_id: UUID,
        relationship_type: str,
        properties: dict[str, Any] | None = None
    ):
        """Create a relationship between two memories."""
        try:
            query = """
            MATCH (source:Memory {id: $source_id})
            MATCH (target:Memory {id: $target_id})
            MERGE (source)-[r:`{rel_type}`]->(target)
            SET r += $properties
            RETURN r
            """.replace("{rel_type}", relationship_type)

            await self.session.run(
                query,
                source_id=str(source_id),
                target_id=str(target_id),
                properties=properties or {}
            )

            logger.debug(f"Created {relationship_type} relationship: {source_id} -> {target_id}")

        except Exception as e:
            logger.error(f"Failed to create relationship {source_id} -> {target_id}: {e}")
            raise

    async def disconnect(
        self,
        source_id: UUID,
        target_id: UUID,
        relationship_type: str | None = None
    ):
        """Remove relationship(s) between two memories."""
        try:
            if relationship_type:
                query = f"""
                MATCH (source:Memory {{id: $source_id}})-[r:`{relationship_type}`]->(target:Memory {{id: $target_id}})
                DELETE r
                RETURN count(r) as deleted
                """
            else:
                # Delete all relationships between the nodes
                query = """
                MATCH (source:Memory {id: $source_id})-[r]->(target:Memory {id: $target_id})
                DELETE r
                RETURN count(r) as deleted
                """

            result = await self.session.run(
                query,
                source_id=str(source_id),
                target_id=str(target_id)
            )

            record = await result.single()
            deleted_count = record["deleted"] if record else 0
            logger.debug(f"Deleted {deleted_count} relationships between {source_id} and {target_id}")

        except Exception as e:
            logger.error(f"Failed to delete relationships {source_id} -> {target_id}: {e}")
            raise

    async def delete(self, memory_id: UUID, memory_type: type[T] | None = None):
        """Delete a memory and all its relationships."""
        try:
            if memory_type:
                labels = memory_type.labels()
                labels_str = ":".join(labels)
                query = f"""
                MATCH (m:{labels_str} {{id: $id}})
                DETACH DELETE m
                """
            else:
                # Delete by ID regardless of type
                query = """
                MATCH (m:Memory {id: $id})
                DETACH DELETE m
                """

            await self.session.run(query, id=str(memory_id))
            logger.debug(f"Deleted memory {memory_id}")

        except Exception as e:
            logger.error(f"Failed to delete memory {memory_id}: {e}")
            raise

    def _build_where_clause(self, filters: dict[str, Any] | None) -> str:
        """Build WHERE clause from filters."""
        if not filters:
            return ""

        conditions = []
        for key, value in filters.items():
            if isinstance(value, str):
                conditions.append(f"m.{key} = '{value}'")
            elif isinstance(value, int | float):
                conditions.append(f"m.{key} = {value}")
            elif isinstance(value, list):
                str_values = [f"'{v}'" if isinstance(v, str) else str(v) for v in value]
                conditions.append(f"m.{key} IN [{', '.join(str_values)}]")

        return f"WHERE {' AND '.join(conditions)}" if conditions else ""

    def _build_filter_clause(self, filters: dict[str, Any] | None, alias: str = "m") -> str:
        """Build filter clause for similarity search (assumes WHERE already used)."""
        if not filters:
            return ""

        conditions = []
        for key, value in filters.items():
            if isinstance(value, str):
                conditions.append(f"{alias}.{key} = '{value}'")
            elif isinstance(value, int | float):
                conditions.append(f"{alias}.{key} = {value}")
            elif isinstance(value, list):
                str_values = [f"'{v}'" if isinstance(v, str) else str(v) for v in value]
                conditions.append(f"{alias}.{key} IN [{', '.join(str_values)}]")

        return f"AND {' AND '.join(conditions)}" if conditions else ""

    def _record_to_memory(self, record: dict, memory_type: type[T]) -> T:
        """Convert Neo4j record to memory object."""
        try:
            return memory_type.from_neo4j_record(record)
        except Exception as e:
            logger.error(f"Failed to convert record to {memory_type.__name__}: {e}")
            logger.debug(f"Problematic record: {record}")
            raise


class MemoryRepository(GenericMemoryRepository[Memory]):
    """Specialized repository for the Memory discriminated union."""

    async def recall_any(
        self,
        filters: dict[str, Any] | None = None,
        similarity_search: tuple[list[float], float] | None = None,
        limit: int = 100,
        offset: int = 0
    ) -> list[Memory]:
        """Recall memories of any type using the discriminated union."""
        try:
            if similarity_search:
                embedding, threshold = similarity_search
                query = f"""
                CALL db.index.vector.queryNodes('memory_embeddings', $k, $embedding)
                YIELD node, score
                WHERE score > $threshold
                {self._build_filter_clause(filters, alias='node')}
                RETURN node as m, score as similarity
                ORDER BY similarity DESC
                SKIP $offset LIMIT $limit
                """

                params = {
                    "embedding": embedding,
                    "threshold": threshold,
                    "offset": offset,
                    "limit": limit,
                    "k": limit + offset,
                    **(filters or {})
                }
            else:
                query = f"""
                MATCH (m:Memory)
                {self._build_where_clause(filters)}
                RETURN m
                ORDER BY m.timestamp DESC
                SKIP $offset LIMIT $limit
                """
                params = {"offset": offset, "limit": limit, **(filters or {})}

            result = await self.session.run(query, **params)

            memories = []
            async for record in result:
                try:
                    # Use the discriminated union to automatically determine type
                    memory_data = record["m"]
                    if "memory_type" in memory_data:
                        # Use TypeAdapter for discriminated union parsing
                        from pydantic import TypeAdapter

                        from memory_palace.domain.models.memories import Memory

                        adapter = TypeAdapter(Memory)
                        memory = adapter.validate_python(memory_data)
                        memories.append(memory)
                except Exception as e:
                    logger.warning(f"Failed to deserialize memory record: {e}")
                    continue

            logger.debug(f"Recalled {len(memories)} memories of mixed types")
            return memories

        except Exception as e:
            logger.error(f"Failed to recall mixed memory types: {e}")
            return []
