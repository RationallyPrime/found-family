"""Neo4j driver and connection management.

This module provides a clean, async-first Neo4j driver resource provider
with proper connection management and query execution.
"""

from collections.abc import AsyncGenerator, Callable
from typing import TYPE_CHECKING, Any, Generic, LiteralString, TypeVar, cast

from neo4j import AsyncDriver, AsyncGraphDatabase

if TYPE_CHECKING:
    from neo4j._async.work.result import AsyncResult
    from neo4j._data import Record

from memory_palace.core import (
    ErrorCode,
    ErrorLevel,
    ServiceErrorDetails,
)
from memory_palace.core.config import settings
from memory_palace.core.decorators import with_error_handling
from memory_palace.core.errors import ServiceError
from memory_palace.core.logging import get_logger

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
    # Handle both SecretStr and plain string for password
    password = (
        settings.neo4j_password.get_secret_value()
        if hasattr(settings.neo4j_password, 'get_secret_value')
        else settings.neo4j_password
    )
    
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


class Neo4jQuery(Generic[T]):
    """Neo4j query executor with typed results.

    This class provides methods to execute Cypher queries with
    various result formats. It's designed to be instantiated
    from a driver.
    """

    def __init__(self, driver: AsyncDriver) -> None:
        """Initialize query executor with a Neo4j driver.

        Args:
            driver: Connected Neo4j AsyncDriver
        """
        self.driver: AsyncDriver = driver

    @with_error_handling(error_level=ErrorLevel.ERROR)
    async def execute(
        self,
        query: LiteralString,
        params: dict[str, Any] | None = None,
        result_transformer: Callable[[Any], T] | None = None,
    ) -> AsyncGenerator[T]:
        """Execute a Neo4j query, yielding results as they arrive.

        Args:
            query: Cypher query to execute
            params: Parameters for the query
            result_transformer: Optional function to transform each record

        Yields:
            Query results, transformed if transformer provided

        Raises:
            ServiceError: If query execution fails
        """
        logger.debug("Executing Neo4j query", extra={"query": query, "params": params})

        async with self.driver.session() as session:
            result: AsyncResult = await session.run(query, parameters=params or {})

            async for record in result:
                if result_transformer:
                    yield result_transformer(record)
                else:
                    yield cast("T", record)

    @with_error_handling(error_level=ErrorLevel.ERROR)
    async def execute_single(
        self,
        query: LiteralString,
        params: dict[str, Any] | None = None,
        result_transformer: Callable[[Any], T] | None = None,
    ) -> T | None:
        """Execute a Neo4j query and return a single result.

        Args:
            query: Cypher query to execute
            params: Parameters for the query
            result_transformer: Optional function to transform the record

        Returns:
            Transformed result or None if no results

        Raises:
            ServiceError: If query execution fails
        """
        logger.debug(
            "Executing Neo4j query for single result",
            extra={"query": query, "params": params},
        )

        async with self.driver.session() as session:
            result: AsyncResult = await session.run(query, parameters=params or {})
            record: Record | None = await result.single(strict=False)

            if record:
                if result_transformer:
                    return result_transformer(record)
                return cast("T", record)
            return None

    async def execute_list(
        self,
        query: LiteralString,
        params: dict[str, Any] | None = None,
        result_transformer: Callable[[Any], T] | None = None,
    ) -> list[T]:
        """Execute a Neo4j query and return a list of results.

        Args:
            query: Cypher query to execute
            params: Parameters for the query
            result_transformer: Optional function to transform each record

        Returns:
            List of transformed results

        Raises:
            Neo4jQueryError: If query execution fails
        """
        logger.debug(
            "Executing Neo4j query for result list",
            extra={"query": query, "params": params},
        )

        results: list[T] = []
        async for record in self.execute(query, params, result_transformer):
            results.append(record)

        return results

    @with_error_handling(error_level=ErrorLevel.ERROR)
    async def execute_value(
        self,
        query: LiteralString,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a Neo4j query and return the first value from the result.

        Args:
            query: Cypher query to execute
            params: Parameters for the query

        Returns:
            First value from first record or None

        Raises:
            ServiceError: If query execution fails
        """
        logger.debug(
            "Executing Neo4j query for single value",
            extra={"query": query, "params": params},
        )

        async with self.driver.session() as session:
            result = await session.run(query, parameters=params or {})
            record = await result.single(strict=False)

            if record and len(record) > 0:
                return record[0]
            return None

    @with_error_handling(error_level=ErrorLevel.ERROR)
    async def execute_batch(
        self,
        operations: list[dict[str, Any]],
        batch_size: int = 50,
        continue_on_error: bool = False,
    ) -> tuple[int, list[dict[str, Any]]]:
        """Execute multiple operations in batches.

        Args:
            operations: List of operation dictionaries with 'query' and 'params' keys
            batch_size: Size of each batch
            continue_on_error: Whether to continue if an operation fails

        Returns:
            Tuple of (success_count, failed_operations)

        Raises:
            ServiceError: If any operation fails and continue_on_error is False
        """
        if not operations:
            return 0, []

        # Calculate batch information
        total_ops = len(operations)
        total_batches = (total_ops + batch_size - 1) // batch_size

        logger.debug(
            "Starting batch operations",
            extra={
                "total_operations": total_ops,
                "batch_size": batch_size,
                "total_batches": total_batches,
            },
        )

        success_count = 0
        failed_operations: list[dict[str, Any]] = []

        # Process batches
        for i in range(0, total_ops, batch_size):
            batch: list[dict[str, Any]] = operations[i : i + batch_size]
            batch_num: int = (i // batch_size) + 1

            logger.debug(f"Processing batch {batch_num}/{total_batches}")

            async with self.driver.session() as session:
                for op_idx, op in enumerate(batch):
                    try:
                        query = op.get("query", "")
                        params = op.get("params", {})

                        _ = await session.run(query, parameters=params)
                        success_count += 1

                    except Exception as e:
                        # Record the failed operation
                        failed_op: dict[str, Any] = {
                            "operation": op,
                            "error": str(e),
                            "batch": batch_num,
                            "index": op_idx,
                        }
                        failed_operations.append(failed_op)

                        logger.error(
                            f"Operation failed in batch {batch_num}, index {op_idx}",
                            extra={"error": str(e)},
                        )

                        if not continue_on_error:
                            # Let the decorator handle the error
                            details = ServiceErrorDetails(
                                source="Neo4jQuery.execute_batch",
                                operation="Neo4j batch query",
                                service_name="Neo4j",
                                endpoint=f"Batch {batch_num}, index {op_idx}",
                                status_code=None,
                                request_id=None,
                                latency_ms=None,
                            )
                            raise ServiceError(
                                message=f"Batch operation failed: {e!s}",
                                code=ErrorCode.DB_QUERY,
                                details=details,
                            ) from e

        # Log summary
        failed_count = len(failed_operations)
        logger.info(
            f"Batch operation completed: {success_count} succeeded, {failed_count} failed",
            extra={
                "success_count": success_count,
                "failed_count": failed_count,
                "total_operations": total_ops,
            },
        )

        return success_count, failed_operations


def create_neo4j_query(driver: AsyncDriver) -> Neo4jQuery[Any]:
    """Create a Neo4jQuery instance from a driver.

    Args:
        driver: Connected Neo4j AsyncDriver

    Returns:
        Neo4jQuery: A query executor for the driver
    """
    return Neo4jQuery(driver)
