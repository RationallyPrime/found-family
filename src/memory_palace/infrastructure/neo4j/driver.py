"""Neo4j driver and connection management.

This module provides a clean, async-first Neo4j driver resource provider
with proper connection management and query execution.
"""

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, TypeVar

from neo4j import AsyncDriver, AsyncGraphDatabase

if TYPE_CHECKING:
    pass

from memory_palace.core import (
    ErrorLevel,
)
from memory_palace.core.config import settings
from memory_palace.core.decorators import with_error_handling
from memory_palace.core.logging import get_logger
from memory_palace.infrastructure.neo4j.queries import VectorIndexQueries

logger = get_logger(__name__)

# Generic type for query results
T = TypeVar("T")


@with_error_handling(error_level=ErrorLevel.ERROR)
async def create_neo4j_driver(
    max_connection_pool_size: int | None = None,
    max_connection_lifetime: int | None = None,
) -> AsyncGenerator[AsyncDriver]:
    """Create a Neo4j driver with proper resource management.

    This is designed to be used as a dependency-injector Resource provider.
    It establishes a connection to Neo4j and ensures proper cleanup.

    Args:
        max_connection_pool_size: Maximum size of the connection pool
        max_connection_lifetime: Maximum lifetime of connections in seconds

    Yields:
        AsyncDriver: Connected Neo4j driver

    Raises:
        ServiceError: If connection fails
    """
    # Use settings if not explicitly provided
    pool_size = max_connection_pool_size or 50
    conn_lifetime = max_connection_lifetime or 3600

    logger.info(
        "Creating Neo4j driver",
        extra={
            "uri": settings.neo4j_uri,
            "pool_size": pool_size,
            "connection_lifetime": conn_lifetime,
        },
    )

    # Create the driver
    # Password is a plain string in config
    password = settings.neo4j_password

    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(
            settings.neo4j_user,
            password,
        ),
        max_connection_pool_size=pool_size,
        max_connection_lifetime=conn_lifetime,
    )

    # Verify connectivity before proceeding
    await driver.verify_connectivity()
    logger.info("Neo4j connection established")

    # Yield the driver for use
    yield driver

    # Clean up on application shutdown
    await driver.close()
    logger.info("Neo4j driver closed")


async def ensure_vector_index(driver: AsyncDriver, dimensions: int = 1024) -> None:
    """Ensure the vector index for memory embeddings exists with correct dimensions.

    Args:
        driver: Neo4j async driver
        dimensions: Expected embedding dimensions (will recreate index if mismatch)
    """
    async with driver.session() as session:
        # Use centralized query to check if index exists
        query, _ = VectorIndexQueries.check_vector_index()
        result = await session.run(query)

        record = await result.single()
        current_dims = None

        if record and record.get("options"):
            # Extract dimensions from index config
            options = record["options"]
            index_config = options.get("indexConfig") or options.get("config") or {}

            # Neo4j uses backticks for property names with dots
            current_dims = index_config.get("`vector.dimensions`") or index_config.get("vector.dimensions")

            if current_dims:
                current_dims = int(current_dims)
                logger.info(f"Existing vector index found with {current_dims} dimensions")

        # If dimensions don't match, recreate the index
        if current_dims is not None and current_dims != dimensions:
            logger.warning(
                f"Vector index dimension mismatch: existing={current_dims}, expected={dimensions}. Recreating index..."
            )

            # Use centralized query to drop index
            query, _ = VectorIndexQueries.drop_vector_index()
            await session.run(query)
            logger.info("Dropped existing vector index")

        # Create the index with correct dimensions
        if current_dims is None or current_dims != dimensions:
            # Use centralized query to create index
            query, _ = VectorIndexQueries.create_vector_index(dimensions)
            await session.run(query)
            logger.info(f"Created vector index with {dimensions} dimensions")
