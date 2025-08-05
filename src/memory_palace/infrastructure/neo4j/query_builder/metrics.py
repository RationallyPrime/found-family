"""Neo4j query metrics collection with Logfire integration.

This module provides metrics collection and error handling for Neo4j queries,
exposing statistics like dbHits and rows for monitoring and optimization,
with proper integration with Logfire for observability.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# For type checking only
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar

import logfire
from neo4j._async.work.result import AsyncResult


if TYPE_CHECKING:
    from neo4j._data import Record

from memory_palace.core.errors.base import ErrorLevel
from memory_palace.core.errors.decorators import with_error_handling
from memory_palace.core.logging import (
    error,
    info,
    update_log_context,
)


# Generic type variable for metrics context
T = TypeVar(name="T")


# Define protocols for Neo4j summary objects that aren't properly typed in stubs
class Neo4jCounters(Protocol):
    """Protocol for Neo4j counters in result summary."""

    nodes_created: int
    nodes_deleted: int
    relationships_created: int
    relationships_deleted: int
    properties_set: int


class Neo4jProfile(Protocol):
    """Protocol for Neo4j execution profile in result summary."""

    db_hits: int


class Neo4jSummary(Protocol):
    """Protocol for Neo4j result summary."""

    counters: Neo4jCounters
    profile: Neo4jProfile


@dataclass
class Neo4jQueryMetrics:
    """Metrics collected from Neo4j query execution.

    Captures performance metrics and metadata from Neo4j queries,
    including database hits, rows returned, and timing information.
    """

    # Query information
    query: str
    parameters: dict[str, Any] = field(default_factory=dict)

    # Performance metrics
    db_hits: int = 0
    rows: int = 0
    execution_time_ms: float = 0

    # Execution metadata
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    successful: bool = False
    error: str | None = None

    def mark_complete(self, success: bool = True, error_msg: str | None = None) -> None:
        """Mark the query as complete and record the completion time.

        Args:
            success: Whether the query executed successfully
            error_msg: Optional error message if the query failed
        """
        self.completed_at = datetime.now()
        self.successful = success
        self.error = error_msg

        if success:
            # Calculate the time difference in milliseconds
            delta = self.completed_at - self.started_at
            self.execution_time_ms = delta.total_seconds() * 1000

            # Log successful query metrics to Logfire
            query_summary = self.query.strip().split("\n")[0][:100]
            if len(self.query) > 100:
                query_summary += "..."

            info(
                f"Neo4j query completed: {query_summary}",
                extra={
                    "db_hits": self.db_hits,
                    "rows": self.rows,
                    "execution_time_ms": round(self.execution_time_ms, 2),
                    "query_type": self._determine_query_type(),
                },
            )
        else:
            # Calculate execution time even for failures
            delta: timedelta = datetime.now() - self.started_at
            execution_ms: float = delta.total_seconds() * 1000

            # Log failed query with error
            error(
                "Neo4j query failed",
                extra={
                    "error": self.error,
                    "query_type": self._determine_query_type(),
                    "execution_time_ms": round(execution_ms, 2),
                },
            )

    def _determine_query_type(self) -> str:
        """Determine the type of query (READ, WRITE, etc).

        Uses regex patterns and query analysis to more accurately determine
        the query type for metrics categorization.

        Returns:
            Query type string for metrics categorization
        """
        import re

        query = self.query.upper()

        # Define pattern groups for better classification
        write_patterns: list[str] = [
            # Direct write operations
            r"\b(CREATE|MERGE)\b",
            # Data modification
            r"\b(SET|REMOVE)\b",
            # Delete operations
            r"\b(DELETE|DETACH DELETE)\b",
            # Database administration
            r"\b(CREATE|DROP) (INDEX|CONSTRAINT)\b",
        ]

        read_patterns: list[str] = [
            # Read operations that don't modify data
            r"\bMATCH\b(?!.*\b(CREATE|MERGE|SET|DELETE|REMOVE)\b)",
            r"\bRETURN\b",
            r"\bOPTIONAL MATCH\b",
            # Aggregation patterns
            r"\b(COUNT|SUM|AVG|MIN|MAX)\b",
            # Path finding
            r"\bSHORTEST PATH\b",
        ]

        schema_patterns: list[str] = [
            # Schema operations
            r"\b(CREATE|DROP) (INDEX|CONSTRAINT)\b",
            r"\bALTER\b",
        ]

        admin_patterns: list[str] = [
            # Admin operations
            r"\bSHOW\b"
        ]

        # Check for write operations first - they're more critical for metrics
        for pattern in write_patterns:
            if re.search(pattern, query):
                if any(re.search(p, query) for p in schema_patterns):
                    return "SCHEMA"
                return "WRITE"

        # Next check for read operations
        for pattern in read_patterns:
            if re.search(pattern, query):
                return "READ"

        # Check for schema operations
        for pattern in schema_patterns:
            if re.search(pattern, query):
                return "SCHEMA"

        # Check for admin operations
        for pattern in admin_patterns:
            if re.search(pattern, query):
                return "ADMIN"

        # More complex pattern - procedure calls
        if "CALL " in query:
            if any(modify in query for modify in ["WRITE", "CREATE", "DELETE"]):
                return "WRITE_PROCEDURE"
            return "READ_PROCEDURE"

        # Final fallbacks - if contains any known read/write keywords
        if any(op in query for op in ["CREATE", "SET", "DELETE", "REMOVE", "MERGE"]):
            return "WRITE"
        if any(op in query for op in ["MATCH", "RETURN", "WHERE", "ORDER BY"]):
            return "READ"

        return "UNKNOWN"

    @with_error_handling(error_level=ErrorLevel.ERROR)
    async def update_from_result(self, result: AsyncResult) -> None:
        """Update metrics from query result.

        Args:
            result: AsyncResult from Neo4j query execution

        Note:
            This method extracts execution metrics from the Neo4j result object,
            particularly focusing on dbHits and row counts.

        Raises:
            ServiceError: If extracting metrics fails
        """
        # Get result summary with metrics
        # We need to check for the existence of the property and access it safely
        summary = getattr(result, "summary", None)

        # Extract metrics if available
        if summary is not None and hasattr(summary, "counters"):
            # Handle updates, creates, deletes
            counters = summary.counters
            # Track total number of operations performed
            self.db_hits = sum(
                [
                    getattr(counters, "nodes_created", 0),
                    getattr(counters, "nodes_deleted", 0),
                    getattr(counters, "relationships_created", 0),
                    getattr(counters, "relationships_deleted", 0),
                    getattr(counters, "properties_set", 0),
                ]
            )

        # Get profile information if available (more accurate dbHits)
        if summary is not None and hasattr(summary, "profile"):
            profile = summary.profile
            if hasattr(profile, "db_hits"):
                self.db_hits = profile.db_hits

        # Community edition might not have detailed metrics
        # Use our row counter as a fallback
        self.mark_complete(success=True)


class MetricsCollector(Generic[T]):
    """Collects and processes Neo4j query metrics with Logfire integration.

    This class provides error handling and metrics collection for Neo4j queries,
    with proper error conversion and detailed performance tracking.

    Generic Parameters:
        T: The type of the query results
    """

    def __init__(self) -> None:
        """Initialize a new metrics collector."""
        self.metrics: Neo4jQueryMetrics | None = None

    def create_metrics(self, query: str, params: dict[str, Any]) -> Neo4jQueryMetrics:
        """Create a new metrics object for a query.

        Args:
            query: The Cypher query being executed
            params: The parameters for the query

        Returns:
            Initialized Neo4jQueryMetrics object
        """
        # When creating metrics, update the Logfire context with query info
        update_log_context(
            "neo4j_metrics",
            {
                "neo4j_query_type": self._determine_query_type(query),
                "neo4j_query_id": id(self),  # Use object ID as unique identifier for this query
            },
        )

        self.metrics = Neo4jQueryMetrics(
            query=query,
            parameters=params,
        )
        return self.metrics

    def _determine_query_type(self, query: str) -> str:
        """Determine the type of query (READ, WRITE, etc).

        Uses regex patterns and query analysis to more accurately determine
        the query type for metrics categorization.

        Args:
            query: The Cypher query string

        Returns:
            Query type string for metrics categorization
        """
        import re

        query = query.upper()

        # Define pattern groups for better classification
        write_patterns: list[str] = [
            # Direct write operations
            r"\b(CREATE|MERGE)\b",
            # Data modification
            r"\b(SET|REMOVE)\b",
            # Delete operations
            r"\b(DELETE|DETACH DELETE)\b",
            # Database administration
            r"\b(CREATE|DROP) (INDEX|CONSTRAINT)\b",
        ]

        read_patterns: list[str] = [
            # Read operations that don't modify data
            r"\bMATCH\b(?!.*\b(CREATE|MERGE|SET|DELETE|REMOVE)\b)",
            r"\bRETURN\b",
            r"\bOPTIONAL MATCH\b",
            # Aggregation patterns
            r"\b(COUNT|SUM|AVG|MIN|MAX)\b",
            # Path finding
            r"\bSHORTEST PATH\b",
        ]

        schema_patterns: list[str] = [
            # Schema operations
            r"\b(CREATE|DROP) (INDEX|CONSTRAINT)\b",
            r"\bALTER\b",
        ]

        admin_patterns: list[str] = [
            # Admin operations
            r"\bSHOW\b"
        ]

        # Check for write operations first - they're more critical for metrics
        for pattern in write_patterns:
            if re.search(pattern, query):
                if any(re.search(p, query) for p in schema_patterns):
                    return "SCHEMA"
                return "WRITE"

        # Next check for read operations
        for pattern in read_patterns:
            if re.search(pattern, query):
                return "READ"

        # Check for schema operations
        for pattern in schema_patterns:
            if re.search(pattern, query):
                return "SCHEMA"

        # Check for admin operations
        for pattern in admin_patterns:
            if re.search(pattern, query):
                return "ADMIN"

        # More complex pattern - procedure calls
        if "CALL " in query:
            if any(modify in query for modify in ["WRITE", "CREATE", "DELETE"]):
                return "WRITE_PROCEDURE"
            return "READ_PROCEDURE"

        # Final fallbacks - if contains any known read/write keywords
        if any(op in query for op in ["CREATE", "SET", "DELETE", "REMOVE", "MERGE"]):
            return "WRITE"
        if any(op in query for op in ["MATCH", "RETURN", "WHERE", "ORDER BY"]):
            return "READ"

        return "UNKNOWN"

    @logfire.instrument()
    @with_error_handling(error_level=ErrorLevel.ERROR)
    async def process_result(
        self, result: AsyncResult, record_callback: Callable[[Any], None] | None = None
    ) -> None:
        """Process result and collect metrics.

        Args:
            result: AsyncResult from Neo4j query
            record_callback: Optional callback to process each record

        Raises:
            ServiceError: If query execution fails
        """
        if self.metrics is None:
            raise ValueError("Metrics not initialized. Call create_metrics first.")

        # Increment row count as we process each record
        async for record in result:
            self.metrics.rows += 1
            if record_callback:
                record_callback(record)

        # Update metrics from result
        await self.metrics.update_from_result(result)

    @logfire.instrument()
    @with_error_handling(error_level=ErrorLevel.ERROR)
    async def get_single_record(
        self, result: AsyncResult, transformer: Callable[[Any], T] | None = None
    ) -> T | None:
        """Get a single record with metrics collection.

        Args:
            result: AsyncResult from Neo4j query
            transformer: Optional function to transform the record

        Returns:
            Single result of type T or None if no results

        Raises:
            ServiceError: If query execution fails
        """
        if self.metrics is None:
            raise ValueError("Metrics not initialized. Call create_metrics first.")

        # Get single record from result
        record: Record | None = await result.single(strict=False)

        # Set row count based on whether we got a record
        if self.metrics:
            self.metrics.rows = 1 if record else 0

        # Update metrics from result
        await self.metrics.update_from_result(result)

        # Transform record if needed
        if record and transformer:
            return transformer(record)

        # Handle the case where either record is None or transformer is None
        if record is None:
            return None

        # If we have a record but no transformer, we need to check if the record
        # is already of type T or can be converted to T
        # Since we don't know how to convert it, we return None for safety
        # In practical use, the caller should always provide a transformer
        return None


@logfire.instrument()
@with_error_handling(error_level=ErrorLevel.ERROR)
async def collect_metrics(
    result: AsyncResult, query: str, params: dict[str, Any]
) -> Neo4jQueryMetrics:
    """Collect metrics from a Neo4j query result with Logfire instrumentation.

    Helper function for standalone metrics collection.

    Args:
        result: AsyncResult from Neo4j query
        query: The Cypher query being executed
        params: The parameters for the query

    Returns:
        Neo4jQueryMetrics with collected metrics

    Raises:
        ServiceError: If collecting metrics fails
    """
    # Create metrics object
    metrics: Neo4jQueryMetrics = Neo4jQueryMetrics(query=query, parameters=params)

    # Update metrics from result
    await metrics.update_from_result(result)

    return metrics
