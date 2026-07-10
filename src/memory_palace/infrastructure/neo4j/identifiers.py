"""Validation for identifiers interpolated into Cypher query text."""

import re
from collections.abc import Collection

_CYPHER_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_identifier(
    identifier: str,
    *,
    kind: str,
    allowed: Collection[str] | None = None,
) -> str:
    """Return a safe identifier or fail before query construction."""
    if _CYPHER_IDENTIFIER.fullmatch(identifier) is None:
        raise ValueError(f"Unsafe Cypher {kind} identifier: {identifier!r}")
    if allowed is not None and identifier not in allowed:
        raise ValueError(f"Unknown Cypher {kind} identifier: {identifier!r}")
    return identifier
