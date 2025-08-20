from typing import Any, Generic, LiteralString, TypeVar, cast
from uuid import UUID

from neo4j import AsyncSession

from memory_palace.core.base import ErrorLevel
from memory_palace.core.decorators import with_error_handling
from memory_palace.core.logging import get_logger
from memory_palace.domain.models.base import GraphModel
from memory_palace.domain.models.memories import Memory
from memory_palace.infrastructure.neo4j.filter_compiler import compile_filters
from memory_palace.infrastructure.neo4j.queries import (
    MemoryQueries,
    QueryFactory,
)

logger = get_logger(__name__)

T = TypeVar("T", bound=GraphModel)


class GenericMemoryRepository(Generic[T]):
    """Generic repository for all memory types using discriminated unions and type safety."""

    def __init__(self, session: AsyncSession):
        self.session = session

    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def remember(self, memory: T) -> T:
        """Store any type of memory with full type safety."""
        labels = memory.labels()
        properties = memory.to_neo4j_properties()

        logger.debug(f"Storing {memory.__class__.__name__} with labels: {labels}")

        # Use centralized query for MERGE
        query, _ = MemoryQueries.store_memory_merge(labels)

        result = await self.session.run(query, id=str(memory.id), properties=properties)

        # Verify the memory was stored
        record = await result.single()
        if not record:
            from memory_palace.core.errors import ProcessingError

            raise ProcessingError(
                message="Failed to store memory in database",
                details={
                    "source": "memory_repository",
                    "operation": "store_memory",
                    "memory_id": str(memory.id),
                    "memory_type": memory.__class__.__name__,
                },
            )

        logger.debug(f"Successfully stored memory {memory.id}")
        return memory

    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def recall(
        self,
        memory_type: type[T],
        filters: dict[str, Any] | None = None,
        similarity_search: tuple[list[float], float] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[T]:
        """Recall memories with type safety and optional similarity search."""
        # Get labels from the memory type class
        labels = memory_type.labels()
        labels_str = ":".join(labels)

        if similarity_search:
            embedding, threshold = similarity_search
            # Use centralized query factory
            query, params = QueryFactory.build_similarity_search(
                embedding=embedding, threshold=threshold, limit=limit, offset=offset, labels=labels_str, filters=filters
            )
        else:
            # Use centralized query factory
            query, params = QueryFactory.build_filtered_recall(
                labels=labels, filters=filters, limit=limit, offset=offset
            )

        result = await self.session.run(cast(LiteralString, query), **params)

        memories = []
        async for record in result:
            # Let _record_to_memory handle errors with proper error handling
            # It will raise ProcessingError if it fails
            memory = self._record_to_memory(record["m"], memory_type)
            memories.append(memory)

        logger.debug(f"Recalled {len(memories)} memories of type {memory_type.__name__}")
        return memories

    @with_error_handling(error_level=ErrorLevel.WARNING, reraise=False)
    async def get_by_id(self, memory_id: UUID, memory_type: type[T]) -> T | None:
        """Get a specific memory by ID with type safety."""
        labels = memory_type.labels()

        # Use centralized query
        query, _ = MemoryQueries.get_memory_by_id(labels)

        result = await self.session.run(query, id=str(memory_id))
        record = await result.single()

        if record:
            return self._record_to_memory(record["m"], memory_type)
        return None

    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def connect(
        self, source_id: UUID, target_id: UUID, relationship_type: str, properties: dict[str, Any] | None = None
    ):
        """Create a relationship between two memories."""
        # Use centralized query with proper relationship type parameter
        query, _ = MemoryQueries.create_relationship(relationship_type)

        await self.session.run(query, source_id=str(source_id), target_id=str(target_id), properties=properties or {})

        logger.debug(f"Created {relationship_type} relationship: {source_id} -> {target_id}")

    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def disconnect(self, source_id: UUID, target_id: UUID, relationship_type: str | None = None):
        """Remove relationship(s) between two memories."""
        # Use centralized query
        query, _ = MemoryQueries.delete_relationship(relationship_type)

        result = await self.session.run(query, source_id=str(source_id), target_id=str(target_id))

        record = await result.single()
        deleted_count = record["deleted"] if record else 0
        logger.debug(f"Deleted {deleted_count} relationships between {source_id} and {target_id}")

    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def delete(self, memory_id: UUID, memory_type: type[T] | None = None):
        """Delete a memory and all its relationships."""
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

        await self.session.run(cast(LiteralString, query), id=str(memory_id))
        logger.debug(f"Deleted memory {memory_id}")

    def _build_where_clause(self, filters: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
        """Build WHERE clause from filters using safe parameterization.

        Returns:
            Tuple of (WHERE clause, parameters dict)
        """
        return compile_filters(filters, alias="m")

    def _build_filter_clause(self, filters: dict[str, Any] | None, alias: str = "m") -> tuple[str, dict[str, Any]]:
        """Build filter clause for similarity search (assumes WHERE already used).

        Returns:
            Tuple of (filter clause, parameters dict)
        """
        where_clause, params = compile_filters(filters, alias=alias)
        # Remove "WHERE " prefix since similarity search already has WHERE
        filter_clause = " AND " + where_clause[6:] if where_clause.startswith("WHERE ") else ""
        return filter_clause, params

    def _record_to_memory(self, record: dict, memory_type: type[T]) -> T:
        """Convert Neo4j record to memory object."""
        from memory_palace.core.errors import ProcessingError

        try:
            return memory_type.from_neo4j_record(record)
        except Exception as e:
            logger.error(f"Failed to convert record to {memory_type.__name__}", exc_info=True)
            logger.debug(f"Problematic record: {record}")
            raise ProcessingError(
                message=f"Failed to deserialize {memory_type.__name__} from Neo4j record: {e}",
                details={
                    "source": "memory_repository",
                    "operation": "_record_to_memory",
                    "field": "record",
                    "actual_value": str(record)[:200],  # Truncate for readability
                    "expected_type": memory_type.__name__,
                    "constraint": f"Must be valid {memory_type.__name__} record",
                },
            ) from e


class MemoryRepository(GenericMemoryRepository[Memory]):
    """Specialized repository for the Memory discriminated union."""

    async def recall_any(
        self,
        filters: dict[str, Any] | None = None,
        similarity_search: tuple[list[float], float] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Memory]:
        """Recall memories of any type using the discriminated union."""
        try:
            if similarity_search:
                embedding, threshold = similarity_search
                # Use centralized query factory (no specific labels for Memory union)
                query, params = QueryFactory.build_similarity_search(
                    embedding=embedding,
                    threshold=threshold,
                    limit=limit,
                    offset=offset,
                    labels=None,  # No specific labels for discriminated union
                    filters=filters,
                )
            else:
                # Use centralized query factory
                query, params = QueryFactory.build_filtered_recall(
                    labels=["Memory"],  # Base Memory label
                    filters=filters,
                    limit=limit,
                    offset=offset,
                )

            result = await self.session.run(cast(LiteralString, query), **params)

            memories = []
            async for record in result:
                # Use the discriminated union to automatically determine type
                memory_data = record["m"]
                if "memory_type" in memory_data:
                    # Use TypeAdapter for discriminated union parsing
                    from pydantic import TypeAdapter

                    from memory_palace.domain.models.memories import Memory

                    adapter = TypeAdapter(Memory)
                    # Use Pydantic's validation which will raise ValidationError
                    # We'll let those bubble up as they indicate data integrity issues
                    memory = adapter.validate_python(memory_data)
                    memories.append(memory)
                else:
                    # Log missing memory_type as a warning
                    logger.warning(
                        "Memory record missing memory_type field", extra={"record_id": memory_data.get("id")}
                    )

            logger.debug(f"Recalled {len(memories)} memories of mixed types")
            return memories

        except Exception as e:
            from memory_palace.core.base import DatabaseErrorDetails
            from memory_palace.core.errors import ProcessingError

            logger.error("Failed to recall mixed memory types", exc_info=True)
            raise ProcessingError(
                message=f"Failed to recall memories from database: {e}",
                details=DatabaseErrorDetails(
                    source="memory_repository",
                    operation="recall_any",
                    service_name="neo4j",
                    endpoint="bolt://localhost:7687",
                    status_code=500,
                    query_type="recall",
                    table="Memory",
                ),
            ) from e
