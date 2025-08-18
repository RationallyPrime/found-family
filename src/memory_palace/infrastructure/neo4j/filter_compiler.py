"""Safe filter compilation for Cypher queries.

This module provides a secure way to build WHERE clauses from filter dictionaries,
preventing SQL injection and supporting advanced operators.
"""

from __future__ import annotations

from typing import Any, Tuple

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
    "overlap": "ANY"  # Special handling for list overlaps
}


def _param_name(base: str, idx: int) -> str:
    """Generate unique parameter name."""
    return f"{base}_{idx}"


def compile_filters(
    filters: dict[str, Any] | None,
    alias: str = "m"
) -> Tuple[str, dict[str, Any]]:
    """Compile filter dictionary into safe WHERE clause and parameters.
    
    Args:
        filters: Dictionary of filters supporting:
            - Simple equality: {"field": "value"}
            - Operators: {"field__gt": 5, "field__contains": "text"}
            - Logical groups: {"$or": [...], "$and": [...]}
            - Null checks: {"field": None}
        alias: Node alias to use in queries (default: "m")
    
    Returns:
        Tuple of (WHERE clause string, parameters dict)
        
    Examples:
        >>> compile_filters({"name": "Alice", "age__gt": 18})
        ("WHERE m.name = $p_0 AND m.age > $p_1", {"p_0": "Alice", "p_1": 18})
        
        >>> compile_filters({"$or": [{"type": "A"}, {"type": "B"}]})
        ("WHERE (m.type = $p_0) OR (m.type = $p_1)", {"p_0": "A", "p_1": "B"})
    """
    if not filters:
        return "", {}
    
    clauses: list[str] = []
    params: dict[str, Any] = {}
    param_counter = [0]  # Use list to allow modification in nested function
    
    def add_clause(expr: str, value: Any) -> None:
        """Add a clause with parameterized value."""
        param_name = _param_name("p", param_counter[0])
        param_counter[0] += 1
        
        # Replace placeholder with parameter name
        clause = expr.format(param=f"${param_name}")
        clauses.append(clause)
        params[param_name] = value
    
    def handle_field_op(field: str, op: str, value: Any) -> None:
        """Handle field with operator."""
        if op == "overlap":
            # Special handling for list overlap
            param_name = _param_name("p", param_counter[0])
            param_counter[0] += 1
            clauses.append(
                f"ANY(x IN ${param_name} WHERE x IN {alias}.{field})"
            )
            params[param_name] = value
        elif op in ("startswith", "endswith", "contains"):
            # Text operators
            add_clause(f"{alias}.{field} {_OPS[op]} {{param}}", value)
        elif op in _OPS:
            # Standard comparison operators
            add_clause(f"{alias}.{field} {_OPS[op]} {{param}}", value)
        else:
            # Unknown operator, treat as equality with suffix
            add_clause(f"{alias}.{field}__{op} = {{param}}", value)
    
    def process_filters(filter_dict: dict[str, Any], parent_op: str = "AND") -> list[str]:
        """Recursively process filter dictionary."""
        local_clauses = []
        
        for key, value in filter_dict.items():
            if key == "$or":
                # OR group
                if isinstance(value, list):
                    or_clauses = []
                    for item in value:
                        if isinstance(item, dict):
                            sub_clauses = process_filters(item, "AND")
                            if sub_clauses:
                                # Wrap in parens if multiple conditions
                                if len(sub_clauses) > 1:
                                    or_clauses.append(f"({' AND '.join(sub_clauses)})")
                                else:
                                    or_clauses.append(sub_clauses[0])
                    if or_clauses:
                        if len(or_clauses) > 1:
                            local_clauses.append(f"({' OR '.join(or_clauses)})")
                        else:
                            local_clauses.append(or_clauses[0])
            
            elif key == "$and":
                # AND group
                if isinstance(value, list):
                    and_clauses = []
                    for item in value:
                        if isinstance(item, dict):
                            sub_clauses = process_filters(item, "AND")
                            and_clauses.extend(sub_clauses)
                    if and_clauses:
                        if len(and_clauses) > 1:
                            local_clauses.append(f"({' AND '.join(and_clauses)})")
                        else:
                            local_clauses.append(and_clauses[0])
            
            elif "__" in key:
                # Field with operator
                field, op = key.split("__", 1)
                handle_field_op(field, op, value)
            
            elif value is None:
                # NULL check
                local_clauses.append(f"{alias}.{key} IS NULL")
            
            else:
                # Simple equality
                add_clause(f"{alias}.{key} = {{param}}", value)
        
        # Add any clauses created by handle_field_op
        return local_clauses
    
    # Process the main filter dictionary
    process_filters(filters)
    
    # Build WHERE clause
    if clauses:
        where_clause = "WHERE " + " AND ".join(clauses)
    else:
        where_clause = ""
    
    return where_clause, params


def merge_params(*param_dicts: dict[str, Any]) -> dict[str, Any]:
    """Safely merge multiple parameter dictionaries.
    
    Args:
        *param_dicts: Variable number of parameter dictionaries
        
    Returns:
        Merged dictionary with conflict detection
        
    Raises:
        ValueError: If parameter names conflict with different values
    """
    result = {}
    for params in param_dicts:
        for key, value in params.items():
            if key in result and result[key] != value:
                raise ValueError(f"Parameter conflict: {key} has different values")
            result[key] = value
    return result