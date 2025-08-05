"""CypherQueryBuilder extension with metrics integration.

This module provides a metrics-enhanced version of the CypherQueryBuilder class
that automatically collects and reports execution metrics.
"""

from collections.abc import Callable

# For type checking only
from typing import TYPE_CHECKING, Any, TypeVar

import logfire
from neo4j import AsyncDriver, AsyncTransaction


if TYPE_CHECKING:
    from neo4j import AsyncResult

from memory_palace.core.logging import update_log_context
from memory_palace.infrastructure.neo4j.query_builder.builder import (
    CypherQueryBuilder,
)
from memory_palace.infrastructure.neo4j.query_builder.metrics import (
    MetricsCollector,
)


# Generic type variable for query results
T = TypeVar(name="T")


@logfire.instrument()
class MetricsCypherQueryBuilder(CypherQueryBuilder[T]):
    """CypherQueryBuilder with integrated metrics collection.

    This extended query builder automatically collects and reports
    performance metrics like dbHits and rows for each query execution.

    Generic Parameters:
        T: The type of the query results (typically a Pydantic model)
    """

    def __init__(self) -> None:
        """Initialize a new metrics-enhanced Cypher query builder."""
        super().__init__()
        self._metrics_collector = MetricsCollector[T]()

    @logfire.instrument()
    async def execute(
        self,
        driver: AsyncDriver,
        result_transformer: Callable[[Any], T] | None = None,
        transaction: AsyncTransaction | None = None,
        timeout: float | None = None,
    ) -> list[T]:
        """Execute the query and return results with metrics collection.

        Args:
            driver: Neo4j AsyncDriver to use for execution
            result_transformer: Optional function to transform each record
            transaction: Optional transaction to use (if None, creates a new one)
            timeout: Optional query timeout in seconds

        Returns:
            List of query results of type T

        Raises:
            ValueError: If the query is not in a valid state
            Neo4jQueryError: If the query execution fails
        """
        query, params = self.build()

        # Create metrics object to track this query execution
        self._metrics_collector.create_metrics(query, params)

        # Add query context to Logfire logs
        update_log_context(
            "neo4j_query_info",
            {
                "neo4j_query_start": True,
                "neo4j_query_type": self._metrics_collector._determine_query_type(query),
            },
        )

        results: list[T] = []

        # Use provided transaction or create a new session
        if transaction:
            # Execute with existing transaction
            result: AsyncResult = await transaction.run(query, parameters=params, timeout=timeout)

            # Define a synchronous callback wrapper that awaits the async callback
            def create_record_processor(
                result_transformer: Callable[[Any], T] | None,
            ) -> Callable[[Any], None]:
                def process_record(record: Any) -> None:
                    if result_transformer:
                        results.append(result_transformer(record))
                    else:
                        results.append(record)  # We assume record is of type T

                return process_record

            # Use metrics collector to process results
            await self._metrics_collector.process_result(
                result, create_record_processor(result_transformer)
            )

        else:
            # Create new session and transaction
            async with driver.session() as session:
                result = await session.run(query, parameters=params, timeout=timeout)

                # Define a synchronous callback wrapper
                def create_record_processor(
                    result_transformer: Callable[[Any], T] | None,
                ) -> Callable[[Any], None]:
                    def process_record(record: Any) -> None:
                        if result_transformer:
                            results.append(result_transformer(record))
                        else:
                            results.append(record)  # We assume record is of type T

                    return process_record

                # Use metrics collector to process results
                await self._metrics_collector.process_result(
                    result, create_record_processor(result_transformer)
                )

        # Update Logfire context with completion
        update_log_context(
            "neo4j_query_results",
            {
                "neo4j_query_complete": True,
                "neo4j_rows": len(results),
                "neo4j_db_hits": self._metrics_collector.metrics.db_hits
                if self._metrics_collector.metrics
                else 0,
                "neo4j_execution_time_ms": round(
                    self._metrics_collector.metrics.execution_time_ms
                    if self._metrics_collector.metrics
                    else 0,
                    2,
                ),
            },
        )

        return results

    @logfire.instrument()
    async def execute_single(
        self,
        driver: AsyncDriver,
        result_transformer: Callable[[Any], T] | None = None,
        transaction: AsyncTransaction | None = None,
        timeout: float | None = None,
    ) -> T | None:
        """Execute the query and return a single result with metrics collection.

        Args:
            driver: Neo4j AsyncDriver to use for execution
            result_transformer: Optional function to transform the record
            transaction: Optional transaction to use (if None, creates a new one)
            timeout: Optional query timeout in seconds

        Returns:
            Single result of type T or None if no results

        Raises:
            ValueError: If the query is not in a valid state
            Neo4jQueryError: If the query execution fails
        """
        query, params = self.build()

        # Create metrics object to track this query execution
        self._metrics_collector.create_metrics(query, params)

        # Add query context to Logfire logs
        update_log_context(
            "neo4j_single_query",
            {
                "neo4j_query_start": True,
                "neo4j_query_type": self._metrics_collector._determine_query_type(query),
                "neo4j_single_result": True,
            },
        )

        # Use provided transaction or create a new session
        if transaction:
            # Execute with existing transaction
            result = await transaction.run(query, parameters=params, timeout=timeout)

            # Get single record with metrics collection
            record = await self._metrics_collector.get_single_record(
                result, transformer=result_transformer
            )

        else:
            # Create new session
            async with driver.session() as session:
                result: AsyncResult = await session.run(query, parameters=params, timeout=timeout)

                # Get single record with metrics collection
                record = await self._metrics_collector.get_single_record(
                    result, transformer=result_transformer
                )

        # Update Logfire context with completion
        update_log_context(
            "neo4j_query_completion",
            {
                "neo4j_query_complete": True,
                "neo4j_rows": 1 if record is not None else 0,
                "neo4j_db_hits": self._metrics_collector.metrics.db_hits
                if self._metrics_collector.metrics
                else 0,
                "neo4j_execution_time_ms": round(
                    self._metrics_collector.metrics.execution_time_ms
                    if self._metrics_collector.metrics
                    else 0,
                    2,
                ),
            },
        )

        return record
