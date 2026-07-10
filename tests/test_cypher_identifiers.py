"""Tests for fail-closed validation of interpolated Cypher identifiers."""

import pytest

from memory_palace.infrastructure.neo4j.identifiers import validate_identifier


def test_allowed_identifier_is_returned_unchanged() -> None:
    assert validate_identifier("node", kind="alias", allowed={"m", "node"}) == "node"


@pytest.mark.parametrize(
    "identifier",
    ["node) MATCH (secret)", "node.value", "node`", "", "9node"],
)
def test_unsafe_identifier_is_rejected(identifier: str) -> None:
    with pytest.raises(ValueError, match="Unsafe Cypher alias"):
        validate_identifier(identifier, kind="alias", allowed={"m", "node"})


def test_unrecognized_safe_identifier_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown Cypher alias"):
        validate_identifier("candidate", kind="alias", allowed={"m", "node"})
