"""Neo4j driver and connection management.

This module provides a clean, async-first Neo4j driver resource provider
with proper connection management and query execution.
"""

import asyncio
import time
from collections.abc import Mapping
from typing import TypeVar

from neo4j import AsyncDriver, AsyncGraphDatabase

from memory_palace.core import (
    ErrorLevel,
)
from memory_palace.core.config import settings
from memory_palace.core.decorators import with_error_handling
from memory_palace.core.logging import get_logger
from memory_palace.infrastructure.neo4j.queries import EmbeddingSchemaQueries, SchemaQueries, VectorIndexQueries

logger = get_logger(__name__)

# Generic type for query results
T = TypeVar("T")


@with_error_handling(error_level=ErrorLevel.ERROR)
async def open_neo4j_driver(
    max_connection_pool_size: int | None = None,
    max_connection_lifetime: int | None = None,
) -> AsyncDriver:
    """Create and verify a Neo4j driver owned by the caller.

    This is designed to be used as a dependency-injector Resource provider.
    It establishes a connection to Neo4j and ensures proper cleanup.

    Args:
        max_connection_pool_size: Maximum size of the connection pool
        max_connection_lifetime: Maximum lifetime of connections in seconds

    Returns:
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

    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(
            settings.neo4j_user,
            settings.neo4j_password_value,
        ),
        max_connection_pool_size=pool_size,
        max_connection_lifetime=conn_lifetime,
    )

    try:
        await driver.verify_connectivity()
    except Exception:
        await driver.close()
        raise
    logger.info("Neo4j connection established")
    return driver


async def ensure_schema(driver: AsyncDriver) -> None:
    """Install identity constraints before accepting concurrent traffic."""
    async with driver.session() as session:
        for query, params in SchemaQueries.create_constraints():
            result = await session.run(query, params)
            await result.consume()
    logger.info("Neo4j identity constraints verified")


async def ensure_embedding_compatibility(driver: AsyncDriver, *, model: str, dimensions: int) -> None:
    """Refuse to bless a legacy, mixed, or unprovenanced embedding corpus."""
    async with driver.session() as session:
        descriptor_query, _ = EmbeddingSchemaQueries.get_descriptor()
        descriptor_result = await session.run(descriptor_query)
        descriptor = await descriptor_result.single()

        corpus_query, _ = EmbeddingSchemaQueries.inspect_corpus()
        corpus_result = await session.run(corpus_query)
        corpus = await corpus_result.single(strict=True)

        embedded = int(corpus["embedded"])
        corpus_matches = (
            int(corpus["missing_provenance"]) == 0
            and corpus["models"] == [model]
            and corpus["declared_dimensions"] == [dimensions]
            and corpus["min_dimensions"] == dimensions
            and corpus["max_dimensions"] == dimensions
        )
        if embedded and not corpus_matches:
            raise RuntimeError(
                "Embedding corpus lacks consistent model provenance. "
                "Run scripts/reembed_corpus.py --apply before starting the application."
            )

        if descriptor is None:
            ensure_query, params = EmbeddingSchemaQueries.ensure_descriptor()
            ensured = await session.run(ensure_query, {**params, "model": model, "dimensions": dimensions})
            descriptor = await ensured.single(strict=True)

    stored_model = descriptor["model"]
    stored_dimensions = int(descriptor["dimensions"])
    if stored_model != model or stored_dimensions != dimensions:
        raise RuntimeError(
            "Embedding corpus mismatch: "
            f"stored={stored_model}/{stored_dimensions}, configured={model}/{dimensions}. "
            "Run scripts/reembed_corpus.py before starting the application."
        )


async def ensure_vector_index(driver: AsyncDriver, dimensions: int = 1024) -> None:
    """Ensure the vector index has the complete expected contract and is online.

    Args:
        driver: Neo4j async driver
        dimensions: Expected embedding dimensions (will recreate index if mismatch)
    """
    async with driver.session() as session:
        # Use centralized query to check if index exists
        query, _ = VectorIndexQueries.check_vector_index()
        result = await session.run(query)

        record = await result.single()
        if record is not None and not _vector_index_matches(record, dimensions):
            logger.warning("Vector index contract mismatch; recreating index")
            query, _ = VectorIndexQueries.drop_vector_index()
            dropped = await session.run(query)
            await dropped.consume()
            logger.info("Dropped existing vector index")
            record = None

        if record is None:
            query, _ = VectorIndexQueries.create_vector_index(dimensions)
            created = await session.run(query)
            await created.consume()
            logger.info(f"Created vector index with {dimensions} dimensions")

        deadline = time.monotonic() + 60.0
        while True:
            query, _ = VectorIndexQueries.check_vector_index()
            status_result = await session.run(query)
            status = await status_result.single()
            if status is not None and _vector_index_matches(status, dimensions) and status["state"] == "ONLINE":
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Vector index did not become ONLINE within 60 seconds")
            await asyncio.sleep(0.25)

    logger.info("Vector index contract verified", dimensions=dimensions, similarity="cosine")


def _vector_index_matches(record: Mapping[str, object], dimensions: int) -> bool:
    """Validate every query-relevant part of a Neo4j vector index contract."""
    options = record.get("options")
    if not isinstance(options, Mapping):
        return False
    index_config = options.get("indexConfig") or options.get("config")
    if not isinstance(index_config, Mapping):
        return False

    configured_dimensions = index_config.get("vector.dimensions") or index_config.get("`vector.dimensions`")
    similarity = index_config.get("vector.similarity_function") or index_config.get("`vector.similarity_function`")
    return (
        record.get("type") == "VECTOR"
        and record.get("labelsOrTypes") == ["Memory"]
        and record.get("properties") == ["embedding"]
        and configured_dimensions == dimensions
        and isinstance(similarity, str)
        and similarity.casefold() == "cosine"
    )
