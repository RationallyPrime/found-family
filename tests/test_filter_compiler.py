"""Behavioral tests for safe Cypher filter compilation."""

import pytest

from memory_palace.infrastructure.neo4j.filter_compiler import compile_filters


def test_or_group_preserves_boolean_semantics() -> None:
    where, params = compile_filters(
        {
            "$or": [
                {"memory_type": "friend_utterance"},
                {"memory_type": "claude_utterance"},
            ]
        }
    )

    assert where == "WHERE (m.memory_type = $p_0 OR m.memory_type = $p_1)"
    assert params == {"p_0": "friend_utterance", "p_1": "claude_utterance"}


def test_or_group_keeps_each_compound_branch_isolated() -> None:
    where, params = compile_filters(
        {
            "$or": [
                {"pinned": True, "salience__gte": 0.8},
                {"memory_type": "consolidation"},
            ]
        }
    )

    assert where == ("WHERE ((m.pinned = $p_0 AND m.salience >= $p_1) OR m.memory_type = $p_2)")
    assert params == {"p_0": True, "p_1": 0.8, "p_2": "consolidation"}


def test_unsafe_field_identifier_is_rejected() -> None:
    with pytest.raises(ValueError, match="field"):
        compile_filters({"content) MATCH (secret) //": "x"})


def test_unknown_but_well_formed_field_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown Cypher field"):
        compile_filters({"unexpected_field": "x"})


def test_unknown_operator_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown filter operator"):
        compile_filters({"salience__approximately": 0.8})


@pytest.mark.parametrize("group", [[], "not-a-list", [{}]])
def test_empty_or_malformed_logical_groups_are_rejected(group: object) -> None:
    with pytest.raises(ValueError, match=r"\$or"):
        compile_filters({"$or": group})


def test_and_group_preserves_boolean_semantics() -> None:
    where, params = compile_filters(
        {"$and": [{"timestamp__gte": 100.0}, {"salience__gte": 0.5}]},
        alias="node",
    )

    assert where == "WHERE (node.timestamp >= $p_0 AND node.salience >= $p_1)"
    assert params == {"p_0": 100.0, "p_1": 0.5}


def test_empty_and_group_is_rejected() -> None:
    with pytest.raises(ValueError, match=r"\$and"):
        compile_filters({"$and": []})
