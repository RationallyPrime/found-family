"""Embedding bootstrap validates corpus and vector-index identity, not just names."""

from memory_palace.infrastructure.neo4j.driver import _vector_index_matches
from memory_palace.infrastructure.neo4j.queries import EmbeddingSchemaQueries, VectorIndexQueries


def _index_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "type": "VECTOR",
        "labelsOrTypes": ["Memory"],
        "properties": ["embedding"],
        "state": "ONLINE",
        "options": {
            "indexConfig": {
                "vector.dimensions": 1_024,
                "vector.similarity_function": "cosine",
            }
        },
    }
    record.update(overrides)
    return record


def test_vector_index_contract_checks_label_property_metric_and_dimensions() -> None:
    assert _vector_index_matches(_index_record(), 1_024) is True
    assert (
        _vector_index_matches(
            _index_record(
                options={
                    "indexConfig": {
                        "vector.dimensions": 1_024,
                        "vector.similarity_function": "COSINE",
                    }
                }
            ),
            1_024,
        )
        is True
    )
    assert _vector_index_matches(_index_record(labelsOrTypes=["Other"]), 1_024) is False
    assert _vector_index_matches(_index_record(properties=["other"]), 1_024) is False
    assert _vector_index_matches(_index_record(), 2_048) is False
    assert _vector_index_matches(_index_record(options={"indexConfig": {}}), 1_024) is False


def test_bootstrap_queries_inspect_corpus_and_full_index_contract() -> None:
    corpus_query, _ = EmbeddingSchemaQueries.inspect_corpus()
    index_query, _ = VectorIndexQueries.check_vector_index()

    assert "missing_provenance" in corpus_query
    assert "min(size(m.embedding))" in corpus_query
    assert "labelsOrTypes, properties, options, state" in index_query


def test_legacy_provenance_adoption_is_atomic_and_guarded() -> None:
    query, _ = EmbeddingSchemaQueries.adopt_legacy_provenance()

    assert "actual_dimensions = [$dimensions]" in query
    assert "existing_models = [$model]" in query
    assert "existing.dimensions = $dimensions" in query
    assert "FOREACH (memory IN memories" in query
    assert "MERGE (schema:EmbeddingSchema" in query
