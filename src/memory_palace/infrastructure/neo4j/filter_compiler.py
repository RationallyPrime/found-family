"""Safe filter compilation for Cypher queries.

This module provides a secure way to build WHERE clauses from filter dictionaries,
preventing SQL injection and supporting advanced operators.
"""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from memory_palace.infrastructure.neo4j.identifiers import validate_identifier

_FILTERABLE_FIELDS = frozenset(
    {
        "conversation_id",
        "memory_type",
        "pinned",
        "salience",
        "timestamp",
        "topic_id",
    }
)
_FILTER_ALIASES = frozenset({"m", "node"})

_OPS = {
    "lt": "<",
    "lte": "<=",
    "gt": ">",
    "gte": ">=",
    "ne": "<>",
    "in": "IN",
    "contains": "CONTAINS",
    "startswith": "STARTS WITH",
    "endswith": "ENDS WITH",
    "overlap": "ANY",  # Special handling for list overlaps
}


def _param_name(base: str, idx: int) -> str:
    """Generate unique parameter name."""
    return f"{base}_{idx}"


def compile_filters(filters: dict[str, JsonValue] | None, alias: str = "m") -> tuple[str, dict[str, JsonValue]]:
    """Compile filter dictionary into safe WHERE clause and parameters.

    Args:
        filters: Dictionary of filters supporting:
            - Simple equality: {"field": "value"}
            - Operators: {"field__gt": 5, "field__contains": "text"}
            - Logical groups: {"$or": [...], "$and": [...]}
            - Null checks: {"field": None}
            Filterable fields are deliberately limited to the properties used
            by repository callers: conversation_id, memory_type, pinned,
            salience, timestamp, and topic_id.
        alias: Node alias to use in queries (default: "m")

    Returns:
        Tuple of (WHERE clause string, parameters dict)

    Examples:
        >>> compile_filters({"pinned": True, "salience__gte": 0.5})
        ("WHERE m.pinned = $p_0 AND m.salience >= $p_1", {"p_0": True, "p_1": 0.5})

        >>> compile_filters({"$or": [{"memory_type": "friend_utterance"}, {"memory_type": "claude_utterance"}]})
        ('WHERE (m.memory_type = $p_0 OR m.memory_type = $p_1)', {'p_0': 'friend_utterance', 'p_1': 'claude_utterance'})
    """
    alias = validate_identifier(alias, kind="alias", allowed=_FILTER_ALIASES)

    if not filters:
        return "", {}

    params: dict[str, JsonValue] = {}
    param_counter = [0]  # Use list to allow modification in nested function

    def add_clause(expr: str, value: JsonValue) -> str:
        """Add a clause with parameterized value."""
        param_name = _param_name("p", param_counter[0])
        param_counter[0] += 1

        # Replace placeholder with parameter name
        clause = expr.format(param=f"${param_name}")
        params[param_name] = value
        return clause

    def handle_field_op(field: str, op: str, value: JsonValue) -> str:
        """Handle field with operator."""
        if op == "overlap":
            # Special handling for list overlap
            param_name = _param_name("p", param_counter[0])
            param_counter[0] += 1
            params[param_name] = value
            return f"ANY(x IN ${param_name} WHERE x IN {alias}.{field})"
        if op in _OPS:
            return add_clause(f"{alias}.{field} {_OPS[op]} {{param}}", value)
        raise ValueError(f"Unknown filter operator: {op!r}")

    def process_filters(filter_dict: dict[str, JsonValue]) -> list[str]:
        """Recursively process filter dictionary."""
        local_clauses: list[str] = []

        for key, value in filter_dict.items():
            if key == "$or":
                # OR group
                if not isinstance(value, list) or not value:
                    raise ValueError("$or must be a non-empty list of non-empty filter dictionaries")
                or_clauses = []
                for item in value:
                    if not isinstance(item, dict) or not item:
                        raise ValueError("$or must contain only non-empty filter dictionaries")
                    sub_clauses = process_filters(cast("dict[str, JsonValue]", item))
                    branch = " AND ".join(sub_clauses)
                    or_clauses.append(f"({branch})" if len(sub_clauses) > 1 else branch)
                local_clauses.append(f"({' OR '.join(or_clauses)})")

            elif key == "$and":
                # AND group
                if not isinstance(value, list) or not value:
                    raise ValueError("$and must be a non-empty list of non-empty filter dictionaries")
                and_clauses = []
                for item in value:
                    if not isinstance(item, dict) or not item:
                        raise ValueError("$and must contain only non-empty filter dictionaries")
                    and_clauses.extend(process_filters(cast("dict[str, JsonValue]", item)))
                local_clauses.append(f"({' AND '.join(and_clauses)})")

            elif "__" in key:
                # Field with operator
                field, op = key.split("__", 1)
                field = validate_identifier(field, kind="field", allowed=_FILTERABLE_FIELDS)
                local_clauses.append(handle_field_op(field, op, value))

            elif value is None:
                # NULL check
                field = validate_identifier(key, kind="field", allowed=_FILTERABLE_FIELDS)
                local_clauses.append(f"{alias}.{field} IS NULL")

            else:
                # Simple equality
                field = validate_identifier(key, kind="field", allowed=_FILTERABLE_FIELDS)
                local_clauses.append(add_clause(f"{alias}.{field} = {{param}}", value))

        return local_clauses

    clauses = process_filters(filters)
    where_clause = "WHERE " + " AND ".join(clauses) if clauses else ""

    return where_clause, params


def merge_params(*param_dicts: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Safely merge multiple parameter dictionaries.

    Args:
        *param_dicts: Variable number of parameter dictionaries

    Returns:
        Merged dictionary with conflict detection

    Raises:
        ValueError: If parameter names conflict with different values
    """
    result: dict[str, JsonValue] = {}
    for params in param_dicts:
        for key, value in params.items():
            if key in result and result[key] != value:
                raise ValueError(f"Parameter conflict: {key} has different values")
            result[key] = value
    return result
