"""Dynamic Cypher fragments are closed over trusted identifier sets."""

import pytest

from memory_palace.infrastructure.neo4j.queries import DreamJobQueries, MemoryQueries, VectorIndexQueries


@pytest.mark.parametrize(
    "relationship_type",
    ["RELATES_TO`) DELETE m //", "UNKNOWN", "", "related-to"],
)
def test_relationship_type_rejects_untrusted_identifiers(relationship_type: str) -> None:
    with pytest.raises(ValueError):
        MemoryQueries.create_relationship(relationship_type)


def test_model_derived_labels_still_require_valid_cypher_identifiers() -> None:
    with pytest.raises(ValueError):
        MemoryQueries.store_memory_merge(["Memory", "Bad`) MATCH (n) //"])


@pytest.mark.parametrize("depth", [-1, 0, 4, 1_000])
def test_graph_expansion_depth_is_bounded(depth: int) -> None:
    with pytest.raises(ValueError):
        MemoryQueries.spread_activation(depth)


@pytest.mark.parametrize("dimensions", [-1, 0, 4_097, 100_000])
def test_vector_index_dimensions_are_bounded(dimensions: int) -> None:
    with pytest.raises(ValueError):
        VectorIndexQueries.create_vector_index(dimensions)


def test_batch_write_is_one_ordered_atomic_query() -> None:
    query, params = MemoryQueries.store_utterance_batch()

    assert "UNWIND $memories" in query
    assert "ORDER BY item.position" in query
    assert "WITH nodes[i] AS source, nodes[i + 1] AS target" in query
    assert "MERGE (source)-[r:PRECEDES]->(target)" in query
    assert "RETURN [node IN nodes | node.id] AS stored_ids" in query
    assert params == {}


def test_topic_assignment_requires_a_complete_snapshot_match() -> None:
    query, params = DreamJobQueries.assign_topics_batch()

    assert "WHERE size(matched) = size($updates)" in query
    assert "UNWIND matched AS item" in query
    assert params == {}
