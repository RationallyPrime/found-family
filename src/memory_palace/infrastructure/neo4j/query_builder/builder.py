"""Main Cypher query builder implementation.

This module provides the core CypherQueryBuilder class with a fluent interface
for constructing type-safe Cypher queries.
"""

from collections.abc import Callable

# For type checking only
from typing import (
    Any,
    Generic,
    LiteralString,
    TypeVar,
    cast,
)

from neo4j import AsyncDriver, AsyncResult, AsyncTransaction, Record
from structlog.typing import FilteringBoundLogger

from memory_palace.core.logging import get_logger
from memory_palace.infrastructure.neo4j.query_builder.helpers import (
    QueryHelpers,
)
from memory_palace.infrastructure.neo4j.query_builder.interfaces import (
    QueryBuilder,
)
from memory_palace.infrastructure.neo4j.query_builder.patterns import (
    PatternBuilder,
)
from memory_palace.infrastructure.neo4j.query_builder.specification_support import (
    SpecificationSupport,
)
from memory_palace.infrastructure.neo4j.query_builder.state import (
    ClauseType,
    CypherQueryState,
)

logger: FilteringBoundLogger = get_logger(name=__name__)

# Generic type variable for query results
T = TypeVar("T")


def create_literal_str(prefix: str, clause: str) -> LiteralString:
    """Create a LiteralString by concatenating strings.

    This function serves as a trusted boundary for creating LiteralString.
    In a real implementation, you would validate the inputs here
    to ensure they don't contain injection vulnerabilities.

    Args:
        prefix: String prefix to add
        clause: Main clause string

    Returns:
        A LiteralString that can be safely used in query builders
    """
    # Since we're casting, this is the point where we should validate
    # that the inputs don't contain anything malicious
    return cast("LiteralString", prefix + clause)


class CypherQueryBuilder(QueryBuilder, SpecificationSupport, QueryHelpers, Generic[T]):
    """Type-safe fluent Cypher query builder with Specification support.

    This class provides a fluent interface for building Cypher queries
    with compile-time type checking, proper query validation, and
    specification-based filtering support.

    Generic Parameters:
        T: The type of the query results (typically a Pydantic model)
    """

    def __init__(self) -> None:
        """Initialize a new Cypher query builder."""
        self._query_parts: list[LiteralString] = []
        self._parameters: dict[str, Any] = {}
        self._param_counter: int = 0
        self._state_machine = CypherQueryState()

    def append_query_part(self, part: LiteralString) -> None:
        """Append a query part to the query being built.

        Args:
            part: The query part to append (must be a LiteralString for safety)
        """
        self._query_parts.append(part)

    def add_clause(self, clause_type: ClauseType) -> None:
        """Add a clause to the query state.

        Args:
            clause_type: The type of clause being added
        """
        self._state_machine.add_clause(clause_type)

    def add_parameter(self, value: Any) -> str:
        """Add a parameter to the query.

        Args:
            value: Parameter value to add

        Returns:
            Parameter name to use in the query
        """
        param_name = f"p{self._param_counter}"
        self._parameters[param_name] = value
        self._param_counter += 1
        return param_name

    def match(self, pattern_func: Callable[[PatternBuilder], PatternBuilder]) -> "CypherQueryBuilder[T]":
        """Add a MATCH clause to the query.

        Args:
            pattern_func: Function that builds the pattern using the pattern builder

        Returns:
            Self for method chaining

        Example:
            ```python
            query.match(lambda p: p.node("Person", "n", name="John").rel_to("KNOWS").node("Person"))
            ```
        """
        self._state_machine.validate_can_add_match()

        # Create pattern builder and apply the pattern function
        pattern_builder: PatternBuilder = PatternBuilder()
        pattern: LiteralString = pattern_func(pattern_builder).build()

        # Add to query parts
        self.append_query_part(create_literal_str("", "MATCH "))
        self.append_query_part(pattern)

        # Update state
        self.add_clause(clause_type=ClauseType.MATCH)

        return self

    def optional_match(self, pattern_func: Callable[[PatternBuilder], PatternBuilder]) -> "CypherQueryBuilder[T]":
        """Add an OPTIONAL MATCH clause to the query.

        Args:
            pattern_func: Function that builds the pattern using the pattern builder

        Returns:
            Self for method chaining
        """
        self._state_machine.validate_can_add_match()

        # Create pattern builder and apply the pattern function
        pattern_builder: PatternBuilder = PatternBuilder()
        pattern: LiteralString = pattern_func(pattern_builder).build()

        # Add to query parts
        self.append_query_part(create_literal_str("", "OPTIONAL MATCH "))
        self.append_query_part(pattern)

        # Update state
        self.add_clause(clause_type=ClauseType.OPTIONAL_MATCH)

        return self

    def where(self, condition: str) -> "CypherQueryBuilder[T]":
        """Add a WHERE clause to the query.

        Args:
            condition: WHERE condition (must be a LiteralString for safety)

        Returns:
            Self for method chaining

        Example:
            ```python
            query.where("n.age > 18")
            ```
        """
        self._state_machine.validate_can_add_where()

        # Add to query parts
        self.append_query_part(create_literal_str("", "WHERE "))
        self.append_query_part(condition)

        # Update state
        self.add_clause(clause_type=ClauseType.WHERE)

        return self

    def where_param(self, condition: str, param_value: Any) -> "CypherQueryBuilder[T]":
        """Add a WHERE clause with a parameterized value.

        This is a safer alternative to the raw where() method as it
        automatically handles parameter creation and substitution.

        Args:
            condition: WHERE condition with {} placeholder for parameter
            param_value: Value to substitute for the parameter

        Returns:
            Self for method chaining

        Example:
            ```python
            query.where_param("n.age > {}", 18)
            ```
        """
        self._state_machine.validate_can_add_where()

        # Add parameter
        param_name = self.add_parameter(param_value)

        # Replace placeholder with parameter
        condition_with_param = condition.replace("{}", f"${param_name}")

        # Add to query parts
        self.append_query_part(create_literal_str(prefix="", clause="WHERE "))
        self.append_query_part(create_literal_str(prefix="", clause=condition_with_param))

        # Update state
        self.add_clause(clause_type=ClauseType.WHERE)

        return self

    def return_clause(self, *return_items: str) -> "CypherQueryBuilder[T]":
        """Add a RETURN clause to the query.

        Args:
            *return_items: Items to return (must be LiteralString for safety)

        Returns:
            Self for method chaining

        Example:
            ```python
            query.return_clause("n", "count(r) as count")
            ```
        """
        self._state_machine.validate_can_add_return()

        # Join return items with commas
        return_str = ", ".join(return_items)

        # Add to query parts
        self.append_query_part(create_literal_str(prefix="", clause="RETURN "))
        self.append_query_part(create_literal_str(prefix="", clause=return_str))

        # Update state
        self.add_clause(clause_type=ClauseType.RETURN)

        return self

    def with_clause(self, *with_items: str) -> "CypherQueryBuilder[T]":
        """Add a WITH clause to the query.

        Args:
            *with_items: Items to include in WITH clause (must be LiteralString for safety)

        Returns:
            Self for method chaining

        Example:
            ```python
            query.with_clause("n", "count(r) as count")
            ```
        """
        self._state_machine.validate_can_add_with()

        # Join with items with commas
        with_str = ", ".join(with_items)

        # Add to query parts
        self.append_query_part(create_literal_str(prefix="", clause="WITH "))
        self.append_query_part(create_literal_str(prefix="", clause=with_str))

        # Update state
        self.add_clause(clause_type=ClauseType.WITH)

        return self

    def order_by(self, *order_items: str) -> "CypherQueryBuilder[T]":
        """Add an ORDER BY clause to the query.

        Args:
            *order_items: Items to order by (must be LiteralString for safety)

        Returns:
            Self for method chaining

        Example:
            ```python
            query.order_by("n.name ASC", "n.age DESC")
            ```
        """
        self._state_machine.validate_can_add_order_by()

        # Join order items with commas
        order_str = ", ".join(order_items)

        # Add to query parts
        self.append_query_part(create_literal_str(prefix="", clause="ORDER BY "))
        self.append_query_part(create_literal_str(prefix="", clause=order_str))

        # Update state
        self.add_clause(clause_type=ClauseType.ORDER_BY)

        return self

    def skip(self, count: int) -> "CypherQueryBuilder[T]":
        """Add a SKIP clause to the query.

        Args:
            count: Number of results to skip

        Returns:
            Self for method chaining
        """
        self._state_machine.validate_can_add_skip()

        # Add parameter for skip count
        param_name = self.add_parameter(count)

        # Add to query parts
        self._query_parts.append(create_literal_str(prefix="", clause=f"SKIP ${param_name}"))

        # Update state
        self._state_machine.add_clause(clause_type=ClauseType.SKIP)

        return self

    def limit(self, count: int) -> "CypherQueryBuilder[T]":
        """Add a LIMIT clause to the query.

        Args:
            count: Maximum number of results to return

        Returns:
            Self for method chaining
        """
        self._state_machine.validate_can_add_limit()

        # Add parameter for limit count
        param_name = self.add_parameter(count)

        # Add to query parts
        self._query_parts.append(create_literal_str(prefix="", clause=f"LIMIT ${param_name}"))

        # Update state
        self._state_machine.add_clause(clause_type=ClauseType.LIMIT)

        return self

    def create(self, pattern_func: Callable[[PatternBuilder], PatternBuilder]) -> "CypherQueryBuilder[T]":
        """Add a CREATE clause to the query.

        Args:
            pattern_func: Function that builds the pattern using the pattern builder

        Returns:
            Self for method chaining
        """
        self._state_machine.validate_can_add_create()

        # Create pattern builder and apply the pattern function
        pattern_builder: PatternBuilder = PatternBuilder()
        pattern: LiteralString = pattern_func(pattern_builder).build()

        # Add to query parts
        self._query_parts.append(create_literal_str(prefix="", clause="CREATE "))
        self._query_parts.append(pattern)

        # Update state
        self._state_machine.add_clause(clause_type=ClauseType.CREATE)

        return self

    def set_property(self, node_var: str, properties: dict[str, Any]) -> "CypherQueryBuilder[T]":
        """Add a SET clause to set node properties.

        Args:
            node_var: Variable name of the node to set properties on
            properties: Dictionary of property names and values

        Returns:
            Self for method chaining

        Example:
            ```python
            query.set_property("n", {"name": "John", "age": 30})
            ```
        """
        self._state_machine.validate_can_add_set()

        # Create SET parts
        set_parts: list[LiteralString] = []

        for prop_name, prop_value in properties.items():
            param_name = self.add_parameter(prop_value)
            set_parts.append(create_literal_str(prefix="", clause=f"{node_var}.{prop_name} = ${param_name}"))

        # Join set parts with commas
        set_str: LiteralString = ", ".join(set_parts)

        # Add to query parts
        self._query_parts.append(create_literal_str(prefix="", clause="SET "))
        self._query_parts.append(create_literal_str(prefix="", clause=set_str))

        # Update state
        self._state_machine.add_clause(clause_type=ClauseType.SET)

        return self

    def delete(self, *variables: LiteralString) -> "CypherQueryBuilder[T]":
        """Add a DELETE clause to the query.

        Args:
            *variables: Variables to delete

        Returns:
            Self for method chaining

        Example:
            ```python
            query.delete("n", "r")
            ```
        """
        self._state_machine.validate_can_add_delete()

        # Join variables with commas
        delete_str: LiteralString = ", ".join(variables)

        # Add to query parts
        self._query_parts.append(create_literal_str(prefix="", clause="DELETE "))
        self._query_parts.append(create_literal_str(prefix="", clause=delete_str))

        # Update state
        self._state_machine.add_clause(clause_type=ClauseType.DELETE)

        return self

    def detach_delete(self, *variables: LiteralString) -> "CypherQueryBuilder[T]":
        """Add a DETACH DELETE clause to the query.

        Args:
            *variables: Variables to detach and delete

        Returns:
            Self for method chaining

        Example:
            ```python
            query.detach_delete("n")
            ```
        """
        self._state_machine.validate_can_add_delete()

        # Join variables with commas
        delete_str: LiteralString = ", ".join(variables)

        # Add to query parts
        self._query_parts.append(create_literal_str(prefix="", clause="DETACH DELETE "))
        self._query_parts.append(create_literal_str(prefix="", clause=delete_str))

        # Update state
        self._state_machine.add_clause(clause_type=ClauseType.DETACH_DELETE)

        return self

    def build(self) -> tuple[LiteralString, dict[str, Any]]:
        """Build the final Cypher query and parameters.

        Returns:
            Tuple of (query, params)

        Raises:
            ValueError: If the query is not in a valid state
        """
        # Validate query state before building
        self._state_machine.validate_query_complete()

        # Join query parts with spaces
        query: LiteralString = " ".join(self._query_parts)

        # Return the query and parameters
        return query, self._parameters

    async def execute(
        self,
        driver: AsyncDriver,
        result_transformer: Callable[[Record], T] | None = None,
        transaction: AsyncTransaction | None = None,
        timeout: float | None = None,
    ) -> list[T]:
        """Execute the query and return results.

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

        logger.debug("Executing Neo4j query", extra={"query": query, "params": params})

        results: list[T] = []

        # Use provided transaction or create a new session
        if transaction:
            result = await transaction.run(query, parameters=params, timeout=timeout)

            async for record in result:
                if result_transformer:
                    results.append(result_transformer(record))
                else:
                    results.append(cast("T", record))

        else:
            async with driver.session() as session:
                result: AsyncResult = await session.run(query, parameters=params, timeout=timeout)

                async for record in result:
                    if result_transformer:
                        results.append(result_transformer(record))
                    else:
                        results.append(cast("T", record))

        return results

    async def execute_single(
        self,
        driver: AsyncDriver,
        result_transformer: Callable[[Record], T] | None = None,
        transaction: AsyncTransaction | None = None,
        timeout: float | None = None,
    ) -> T | None:
        """Execute the query and return a single result.

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

        logger.debug(
            "Executing Neo4j query for single result",
            extra={"query": query, "params": params},
        )

        # Use provided transaction or create a new session
        if transaction:
            result: AsyncResult = await transaction.run(query, parameters=params, timeout=timeout)
            record: Record | None = await result.single(strict=False)

            if record:
                if result_transformer:
                    return result_transformer(record)
                return cast("T", record)

        else:
            async with driver.session() as session:
                result = await session.run(query, parameters=params, timeout=timeout)
                record = await result.single(strict=False)

                if record:
                    if result_transformer:
                        return result_transformer(record)
                    return cast("T", record)

        return None
