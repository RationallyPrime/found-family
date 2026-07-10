"""Schema invariants that make repository MERGE operations deterministic."""

from memory_palace.infrastructure.neo4j.queries import SchemaQueries


def test_identity_constraints_cover_all_repository_keys() -> None:
    statements = "\n".join(query for query, _ in SchemaQueries.create_constraints())

    assert "FOR (m:Memory) REQUIRE m.id IS UNIQUE" in statements
    assert "FOR (c:OAuthClient) REQUIRE c.client_id IS UNIQUE" in statements
    assert "FOR (c:OAuthCode) REQUIRE c.code IS UNIQUE" in statements
    assert "FOR (t:OAuthRefreshToken) REQUIRE t.token IS UNIQUE" in statements
    assert "FOR (e:EmbeddingCache) REQUIRE e.cache_key IS UNIQUE" in statements
