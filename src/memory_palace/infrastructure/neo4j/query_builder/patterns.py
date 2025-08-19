"""Pattern builders for Cypher queries.

This module provides utilities for building node and relationship patterns
in a type-safe manner.
"""

from typing import Any, Literal, LiteralString


class NodePattern:
    """Builder for Cypher node patterns.

    This class represents a node pattern like (n:Label {prop: value})
    and provides methods to build it in a type-safe way.
    """

    def __init__(
        self,
        variable: str = "",
        labels: list[str] | None = None,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """Initialize a node pattern builder.

        Args:
            variable: Variable name for the node (can be empty)
            labels: Node labels
            properties: Node properties
        """
        self.variable: str = variable
        self.labels: list[str] = labels or []
        self.properties: dict[str, Any] = properties or {}

    def build(self) -> LiteralString:
        """Build the Cypher node pattern string.

        Returns:
            Cypher node pattern as a LiteralString
        """
        # Start with opening parenthesis
        pattern_parts: list[str] = ["("]

        # Add variable if present
        if self.variable:
            pattern_parts.append(self.variable)

        # Add labels if present
        if self.labels:
            label_str = ":".join(self.labels)
            pattern_parts.append(f":{label_str}")

        # Add properties if present
        if self.properties:
            prop_parts: list[Any] = []
            for key, value in self.properties.items():
                if isinstance(value, str) and not value.startswith("$"):
                    # String literals should be quoted
                    prop_parts.append(f"{key}: '{value}'")
                else:
                    # Parameters or non-strings
                    prop_parts.append(f"{key}: {value}")

            prop_str: LiteralString = ", ".join(prop_parts)
            pattern_parts.append(f" {{{prop_str}}}")

        # Close the pattern
        pattern_parts.append(")")

        return "".join(pattern_parts)  # type: LiteralString


class RelationshipPattern:
    """Builder for Cypher relationship patterns.

    This class represents a relationship pattern like [r:TYPE {prop: value}]
    and provides methods to build it in a type-safe way.
    """

    def __init__(
        self,
        variable: str = "",
        types: list[str] | None = None,
        properties: dict[str, Any] | None = None,
        direction: Literal["->", "<-", "-"] = "->",
    ) -> None:
        """Initialize a relationship pattern builder.

        Args:
            variable: Variable name for the relationship (can be empty)
            types: Relationship types
            properties: Relationship properties
            direction: Direction of the relationship (-> outgoing, <- incoming, - any)
        """
        self.variable: str = variable
        self.types: list[str] = types or []
        self.properties: dict[str, Any] = properties or {}
        self.direction: Literal["->", "<-", "-"] = direction
        self.min_hops: int | None = None
        self.max_hops: int | None = None

    def with_length(self, min_hops: int | None = None, max_hops: int | None = None) -> "RelationshipPattern":
        """Set the length for variable-length relationships.

        Args:
            min_hops: Minimum number of hops (None for no minimum)
            max_hops: Maximum number of hops (None for no maximum)

        Returns:
            Self for method chaining
        """
        self.min_hops = min_hops
        self.max_hops = max_hops
        return self

    def build(self) -> LiteralString:
        """Build the Cypher relationship pattern string.

        Returns:
            Cypher relationship pattern as a LiteralString
        """
        # Start with opening bracket
        pattern_parts: list[str] = ["["]

        # Add variable if present
        if self.variable:
            pattern_parts.append(self.variable)

        # Add types if present
        if self.types:
            type_str = "|".join(self.types)
            pattern_parts.append(f":{type_str}")

        # Add properties if present
        if self.properties:
            prop_parts: list[Any] = []
            for key, value in self.properties.items():
                if isinstance(value, str) and not value.startswith("$"):
                    # String literals should be quoted
                    prop_parts.append(f"{key}: '{value}'")
                else:
                    # Parameters or non-strings
                    prop_parts.append(f"{key}: {value}")

            prop_str: LiteralString = ", ".join(prop_parts)
            pattern_parts.append(f" {{{prop_str}}}")

        # Add variable length if specified
        if self.min_hops is not None or self.max_hops is not None:
            length_parts: list[str] = []

            if self.min_hops is not None:
                length_parts.append(str(self.min_hops))

            length_parts.append("..")

            if self.max_hops is not None:
                length_parts.append(str(self.max_hops))

            pattern_parts.append(f"*{''.join(length_parts)}")

        # Close the pattern
        pattern_parts.append("]")

        return "".join(pattern_parts)  # type: LiteralString


class PatternBuilder:
    """Fluent builder for Cypher patterns.

    This class allows building complex node and relationship patterns
    with a fluent, chainable API.
    """

    def __init__(self) -> None:
        """Initialize a new pattern builder."""
        self._pattern_parts: list[LiteralString] = []

    def node(
        self,
        label: str | None = None,
        variable: str = "",
        **properties: Any,
    ) -> "PatternBuilder":
        """Add a node pattern.

        Args:
            label: Node label (can be None)
            variable: Variable name for the node
            **properties: Node properties as keyword arguments

        Returns:
            Self for method chaining
        """
        labels: list[str] = [label] if label else []
        node: NodePattern = NodePattern(variable=variable, labels=labels, properties=properties)
        self._pattern_parts.append(node.build())
        return self

    def relationship(
        self,
        type_: str | None = None,
        variable: str = "",
        direction: Literal["->", "<-", "-"] = "->",
        min_hops: int | None = None,
        max_hops: int | None = None,
        **properties: Any,
    ) -> "PatternBuilder":
        """Add a relationship pattern.

        Args:
            type_: Relationship type (can be None)
            variable: Variable name for the relationship
            direction: Direction of the relationship
            min_hops: Minimum number of hops for variable-length
            max_hops: Maximum number of hops for variable-length
            **properties: Relationship properties as keyword arguments

        Returns:
            Self for method chaining
        """
        types: list[str] = [type_] if type_ else []
        rel: RelationshipPattern = RelationshipPattern(
            variable=variable, types=types, properties=properties, direction=direction
        )

        if min_hops is not None or max_hops is not None:
            rel.with_length(min_hops=min_hops, max_hops=max_hops)

        # Handle different directions
        if direction == "->":
            self._pattern_parts.append("-")
            self._pattern_parts.append(rel.build())
            self._pattern_parts.append("->")
        elif direction == "<-":
            self._pattern_parts.append("<-")
            self._pattern_parts.append(rel.build())
            self._pattern_parts.append("-")
        else:  # direction == "-"
            self._pattern_parts.append("-")
            self._pattern_parts.append(rel.build())
            self._pattern_parts.append("-")

        return self

    def rel_to(
        self,
        type_: str | None = None,
        variable: str = "",
        min_hops: int | None = None,
        max_hops: int | None = None,
        **properties: Any,
    ) -> "PatternBuilder":
        """Add an outgoing relationship pattern (shorthand for relationship(direction="->")).

        Args:
            type_: Relationship type (can be None)
            variable: Variable name for the relationship
            min_hops: Minimum number of hops for variable-length
            max_hops: Maximum number of hops for variable-length
            **properties: Relationship properties as keyword arguments

        Returns:
            Self for method chaining
        """
        return self.relationship(
            type_=type_,
            variable=variable,
            direction="->",
            min_hops=min_hops,
            max_hops=max_hops,
            **properties,
        )

    def rel_from(
        self,
        type_: str | None = None,
        variable: str = "",
        min_hops: int | None = None,
        max_hops: int | None = None,
        **properties: Any,
    ) -> "PatternBuilder":
        """Add an incoming relationship pattern (shorthand for relationship(direction="<-")).

        Args:
            type_: Relationship type (can be None)
            variable: Variable name for the relationship
            min_hops: Minimum number of hops for variable-length
            max_hops: Maximum number of hops for variable-length
            **properties: Relationship properties as keyword arguments

        Returns:
            Self for method chaining
        """
        return self.relationship(
            type_=type_,
            variable=variable,
            direction="<-",
            min_hops=min_hops,
            max_hops=max_hops,
            **properties,
        )

    def rel(
        self,
        type_: str | None = None,
        variable: str = "",
        min_hops: int | None = None,
        max_hops: int | None = None,
        **properties: Any,
    ) -> "PatternBuilder":
        """Add a bidirectional relationship pattern (shorthand for relationship(direction="-")).

        Args:
            type_: Relationship type (can be None)
            variable: Variable name for the relationship
            min_hops: Minimum number of hops for variable-length
            max_hops: Maximum number of hops for variable-length
            **properties: Relationship properties as keyword arguments

        Returns:
            Self for method chaining
        """
        return self.relationship(
            type_=type_,
            variable=variable,
            direction="-",
            min_hops=min_hops,
            max_hops=max_hops,
            **properties,
        )

    def build(self) -> LiteralString:
        """Build the complete Cypher pattern string.

        Returns:
            Cypher pattern as a LiteralString
        """
        return "".join(self._pattern_parts)
