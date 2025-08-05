"""Example usage of the Cypher query builder.

This file demonstrates how to use the query builder to construct various
Cypher queries. This is not intended to be imported or used directly in
production code, but serves as documentation and verification.
"""

from typing import Any, LiteralString, cast

from pydantic import BaseModel

from memory_palace.infrastructure.neo4j.query_builder import (
    CypherQueryBuilder,
    PaginationMixin,
)


# Example Pydantic model for query results
class Person(BaseModel):
    """Sample person model for demonstration."""

    name: str
    age: int
    occupation: str | None = None


# Example of a query builder with pagination mixin
class PaginatedQueryBuilder(CypherQueryBuilder[Person], PaginationMixin[Person]):
    """Sample query builder with pagination support."""

    # Fix return type mismatches by overriding methods to return self
    def skip(self, count: int) -> "PaginatedQueryBuilder":
        """Override to fix return type."""
        super().skip(count)
        return self

    def limit(self, count: int) -> "PaginatedQueryBuilder":
        """Override to fix return type."""
        super().limit(count)
        return self

    def paginate(self, page: int, page_size: int) -> "PaginatedQueryBuilder":
        """Override to fix return type."""
        super().paginate(page, page_size)
        return self


def example_read_query() -> None:
    """Example of constructing a read query."""
    # Create a query builder for Person results
    query: CypherQueryBuilder[Person] = CypherQueryBuilder[Person]()

    # Build a query to find people named "John" over 30
    query_str, params = (
        query.match(lambda p: p.node("Person", "n"))
        .where_param("n.name = {} AND n.age > {}", "John")
        .where_param("n.age > {}", 30)
        .return_clause("n")
        .build()
    )

    print("Example Read Query:")
    print(f"Query: {query_str}")
    print(f"Params: {params}")


def example_relationship_query() -> None:
    """Example of a query with relationships."""
    query: CypherQueryBuilder[dict[str, Any]] = CypherQueryBuilder[dict[str, Any]]()

    # Build a query to find friends-of-friends
    query_str, params = (
        query.match(
            lambda p: (
                p.node("Person", "p1", name="Alice")
                .rel_to("KNOWS", "r1")
                .node("Person", "p2")
                .rel_to("KNOWS", "r2")
                .node("Person", "p3")
            )
        )
        .where("p1 <> p3")  # Not the same person
        .return_clause("p3.name AS friend_of_friend", "COUNT(p2) AS connection_count")
        .order_by("connection_count DESC")
        .build()
    )

    print("\nExample Relationship Query:")
    print(f"Query: {query_str}")
    print(f"Params: {params}")


def example_create_query() -> None:
    """Example of a create query."""
    query: CypherQueryBuilder[Person] = CypherQueryBuilder[Person]()

    # Build a query to create a new person
    properties: dict[str, str | int] = {
        "name": "Jane",
        "age": 25,
        "occupation": "Engineer",
    }

    # Method 1: Using create with pattern builder
    query_str1, params1 = (
        query.create(lambda p: p.node("Person", properties=properties)).return_clause("n").build()
    )

    print("\nExample Create Query (Method 1):")
    print(f"Query: {query_str1}")
    print(f"Params: {params1}")

    # Method 2: Manually building a parameterized query
    # Add each property as a parameter
    props_str_parts: list[LiteralString] = []
    params2: dict[str, Any] = {}

    for i, (_, value) in enumerate[tuple[str, str | int]](properties.items()):
        param_name = f"p{i}"
        params2[param_name] = value
        props_str_parts.append("{key}: ${param_name}")

    props_str: LiteralString = ", ".join(props_str_parts)
    create_str: LiteralString = f"CREATE (n:Person {{{props_str}}}) RETURN n"

    print("\nExample Create Query (Method 2):")
    print(f"Query: {create_str}")
    print(f"Params: {params2}")


def example_update_query() -> None:
    """Example of an update query."""
    query: CypherQueryBuilder[Person] = CypherQueryBuilder[Person]()

    # Build a query to update a person's age
    query_str, params = (
        query.match(lambda p: p.node("Person", "n", name="John"))
        .set_property(cast("LiteralString", "n"), {"age": 35, "occupation": "Developer"})
        .return_clause("n")
        .build()
    )

    print("\nExample Update Query:")
    print(f"Query: {query_str}")
    print(f"Params: {params}")


def example_delete_query() -> None:
    """Example of a delete query."""
    query: CypherQueryBuilder[dict[str, Any]] = CypherQueryBuilder[dict[str, Any]]()

    # Build a query to delete a person
    query_str, params = (
        query.match(lambda p: p.node("Person", "n", name="John"))
        .detach_delete(cast("LiteralString", "n"))
        .build()
    )

    print("\nExample Delete Query:")
    print(f"Query: {query_str}")
    print(f"Params: {params}")


def example_pagination_query() -> None:
    """Example of a query with pagination."""
    # Use a builder with the pagination mixin
    query: PaginatedQueryBuilder = PaginatedQueryBuilder()

    query_str, params = (
        query.match(lambda p: p.node("Person", "n"))
        .return_clause("n")
        .order_by("n.name")
        # Use pagination methods from the mixin (implemented separately)
        .skip(10)  # (2-1) * 10 = 10
        .limit(10)
        .build()
    )

    print("\nExample Pagination Query:")
    print(f"Query: {query_str}")
    print(f"Params: {params}")


def example_with_clause_query() -> None:
    """Example of a query with a WITH clause."""
    query: CypherQueryBuilder[dict[str, Any]] = CypherQueryBuilder[dict[str, Any]]()

    query_str, params = (
        query.match(lambda p: p.node("Person", "n"))
        .with_clause("n", "n.age as age")
        .where_param("age > {}", 30)
        .return_clause("n.name as name", "age")
        .build()
    )

    print("\nExample WITH Clause Query:")
    print(f"Query: {query_str}")
    print(f"Params: {params}")


def run_all_examples() -> None:
    """Run all example queries."""
    example_read_query()
    example_relationship_query()
    example_create_query()
    example_update_query()
    example_delete_query()
    example_pagination_query()
    example_with_clause_query()


if __name__ == "__main__":
    # This file is not meant to be run directly, but if it is, show the examples
    run_all_examples()
