"""Centralized Cypher query definitions.

This is the SINGLE SOURCE OF TRUTH for all Cypher queries in the application.
Queries are plain, parameterized Cypher: what runs against the database is
exactly what you read here. Dynamic parts (labels, relationship types) are
interpolated from trusted internal enums/models only — never from user input.
"""

from typing import Any, LiteralString, cast

from memory_palace.core.logging import get_logger
from memory_palace.infrastructure.neo4j.identifiers import validate_identifier

logger = get_logger(__name__)

_RELATIONSHIP_TYPES = frozenset(
    {
        "ANSWERED_BY",
        "CONSOLIDATED_FROM",
        "PRECEDES",
        "RELATES_TO",
        "SIMILAR_TO",
        "SOLVED_BY",
        "VERY_SIMILAR_TO",
    }
)


def _validated_labels(labels: list[str]) -> str:
    return ":".join(validate_identifier(label, kind="label") for label in labels)


class MemoryQueries:
    """All memory-related queries in one place."""

    @staticmethod
    def similarity_search(
        labels: str | None = None,
        additional_filters: str | None = None,
    ) -> tuple[LiteralString, dict[str, Any]]:
        """Vector-index similarity search.

        Args:
            labels: Optional node label filter (e.g., "FriendUtterance")
            additional_filters: Optional pre-compiled WHERE conditions
                (from filter_compiler, parameterized — never raw user input)

        Returns:
            Tuple of (query, params)
        """
        where_conditions = ["score > $threshold", "NOT node:Archived"]
        if labels:
            label_clause = _validated_labels(labels.split(":"))
            where_conditions.append(f"node:{label_clause}")
        if additional_filters:
            where_conditions.append(additional_filters)

        query = f"""
            CALL db.index.vector.queryNodes('memory_embeddings', $k, $embedding)
            YIELD node, score
            WHERE {" AND ".join(where_conditions)}
            RETURN node AS m, score AS similarity
            ORDER BY similarity DESC
            SKIP $offset LIMIT $limit
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def store_memory_merge(labels: list[str]) -> tuple[LiteralString, dict[str, Any]]:
        """MERGE query for storing/updating a memory.

        Args:
            labels: Node labels (from GraphModel.labels(), trusted)
        """
        labels_str = _validated_labels(labels)

        query = f"""
            MERGE (m:{labels_str} {{id: $id}})
            SET m += $properties
            RETURN m
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def store_utterance_batch() -> tuple[LiteralString, dict[str, Any]]:
        """Atomically store an ordered utterance batch and its temporal edges.

        Params: $memories (ordered maps with ``position`` and ``properties``),
        $create_temporal_links.
        """
        query = """
            UNWIND $memories AS item
            MERGE (m:Memory {id: item.id})
            SET m += item.properties
            FOREACH (_ IN CASE item.memory_type
                WHEN 'friend_utterance' THEN [1] ELSE [] END | SET m:FriendUtterance)
            FOREACH (_ IN CASE item.memory_type
                WHEN 'claude_utterance' THEN [1] ELSE [] END | SET m:ClaudeUtterance)
            WITH item, m
            ORDER BY item.position
            WITH collect(m) AS nodes
            CALL (nodes) {
                WITH nodes
                WHERE $create_temporal_links AND size(nodes) > 1
                UNWIND range(0, size(nodes) - 2) AS i
                WITH nodes[i] AS source, nodes[i + 1] AS target
                MERGE (source)-[r:PRECEDES]->(target)
                SET r.strength = 1.0
                RETURN count(r) AS temporal_edges
                UNION
                WITH nodes
                WHERE NOT $create_temporal_links OR size(nodes) <= 1
                RETURN 0 AS temporal_edges
            }
            RETURN [node IN nodes | node.id] AS stored_ids
            """
        return cast(LiteralString, query), {}

    @staticmethod
    def get_memory_by_id(labels: list[str]) -> tuple[LiteralString, dict[str, Any]]:
        """Get a specific memory by ID."""
        labels_str = _validated_labels(labels)

        query = f"""
            MATCH (m:{labels_str} {{id: $id}})
            RETURN m
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def create_relationship(relationship_type: str = "RELATES_TO") -> tuple[LiteralString, dict[str, Any]]:
        """Create (or update) a relationship between two memories."""
        relationship_type = validate_identifier(
            relationship_type,
            kind="relationship type",
            allowed=_RELATIONSHIP_TYPES,
        )
        query = f"""
            MATCH (source:Memory {{id: $source_id}})
            MATCH (target:Memory {{id: $target_id}})
            MERGE (source)-[r:`{relationship_type}`]->(target)
            SET r += $properties
            RETURN r
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def delete_relationship(relationship_type: str | None = None) -> tuple[LiteralString, dict[str, Any]]:
        """Delete relationship(s) between two memories."""
        if relationship_type:
            relationship_type = validate_identifier(
                relationship_type,
                kind="relationship type",
                allowed=_RELATIONSHIP_TYPES,
            )
            query = f"""
                MATCH (source:Memory {{id: $source_id}})-[r:`{relationship_type}`]->(target:Memory {{id: $target_id}})
                DELETE r
                RETURN count(r) AS deleted
                """
        else:
            query = """
                MATCH (source:Memory {id: $source_id})-[r]->(target:Memory {id: $target_id})
                DELETE r
                RETURN count(r) AS deleted
                """

        return cast(LiteralString, query), {}

    @staticmethod
    def detect_relationships() -> tuple[LiteralString, dict[str, Any]]:
        """Find similar memories for relationship detection."""
        query = """
            CALL db.index.vector.queryNodes('memory_embeddings', 5, $embedding)
            YIELD node, score
            WHERE node.id <> $id AND score > $threshold AND NOT node:Archived
            RETURN node AS other, score AS similarity
            ORDER BY similarity DESC
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def spread_activation(depth: int) -> tuple[LiteralString, dict[str, Any]]:
        """Pattern completion: spread activation from seed memories over typed edges.

        For each seed (a vector-search hit carrying its similarity score),
        traverses up to `depth` hops over any relationship. Activation along
        a path = seed_score * PROD(edge strength * hop_decay). Each reached
        memory keeps its strongest activation across all paths/seeds.

        Args:
            depth: Max hops (interpolated — Cypher cannot parameterize
                variable-length bounds; callers pass a trusted constant)

        Params: $seeds (list of {id, score}), $hop_decay, $limit
        """
        if not 1 <= depth <= 3:
            raise ValueError("spread activation depth must be between 1 and 3")

        query = f"""
            UNWIND $seeds AS seed
            MATCH (s:Memory {{id: seed.id}})
            MATCH path = (s)-[*1..{depth}]-(m:Memory)
            WHERE NOT m:Archived AND m.id <> seed.id
            WITH m,
                 max(reduce(a = seed.score,
                            r IN relationships(path) |
                            a * coalesce(r.strength, 0.5) * $hop_decay)) AS activation
            RETURN m, activation
            ORDER BY activation DESC
            LIMIT $limit
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def reinforce_memories() -> tuple[LiteralString, dict[str, Any]]:
        """Reconsolidation: strengthen memories that were just recalled.

        Retrieval IS reinforcement — updates access tracking and boosts
        salience asymptotically toward 1.0. Also re-anchors the decay clock.

        Params: $ids (list of memory id strings), $now (epoch float), $rate
        """
        query = """
            UNWIND $ids AS mid
            MATCH (m:Memory {id: mid})
            SET m.access_count = coalesce(m.access_count, 0) + 1,
                m.last_accessed = $now,
                m.salience = coalesce(m.salience, 0.3) + (1.0 - coalesce(m.salience, 0.3)) * $rate,
                m.salience_updated_at = $now
            RETURN count(m) AS reinforced
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def get_relationship_edges() -> tuple[LiteralString, dict[str, Any]]:
        """Get all relationship edges for a specific memory."""
        query = """
            MATCH (m:Memory {id: $memory_id})-[r]-(other:Memory)
            RETURN type(r) AS relationship_type,
                   r.strength AS strength,
                   r.auto_detected AS auto_detected,
                   other.id AS other_id,
                   CASE WHEN startNode(r) = m THEN 'outgoing' ELSE 'incoming' END AS direction
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def top_salient() -> tuple[LiteralString, dict[str, Any]]:
        """Most important unarchived memories, by salience then recency.

        Params: $limit
        """
        query = """
            MATCH (m:Memory)
            WHERE NOT m:Archived AND m.salience IS NOT NULL
            RETURN m
            ORDER BY m.salience DESC, m.timestamp DESC
            LIMIT $limit
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def memory_exists() -> tuple[LiteralString, dict[str, Any]]:
        """Check whether a memory exists before external note generation.

        Params: $id
        """
        query = """
            MATCH (m:Memory {id: $id})
            RETURN count(m) AS found
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def archive_memory_with_note() -> tuple[LiteralString, dict[str, Any]]:
        """Atomically archive a memory and persist its idempotent audit note.

        Params: $id, $note_id, $note_properties
        """
        query = """
            MATCH (m:Memory {id: $id})
            SET m:Archived
            MERGE (note:Memory:SystemNote {id: $note_id})
            ON CREATE SET note += $note_properties
            RETURN count(m) AS archived
            """
        return cast(LiteralString, query), {}

    @staticmethod
    def palace_stats() -> tuple[LiteralString, dict[str, Any]]:
        """Global statistics for the palace: counts by type, time span, health."""
        query = """
            MATCH (m:Memory)
            WITH count(m) AS total,
                 sum(CASE WHEN m:Archived THEN 1 ELSE 0 END) AS archived,
                 min(m.timestamp) AS oldest,
                 max(m.timestamp) AS newest,
                 avg(m.salience) AS avg_salience,
                 sum(CASE WHEN coalesce(m.pinned, false) THEN 1 ELSE 0 END) AS pinned
            OPTIONAL MATCH (:Memory)-[r]-(:Memory)
            RETURN total, archived, oldest, newest, avg_salience, pinned,
                   count(DISTINCT r) AS relationships
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def type_counts() -> tuple[LiteralString, dict[str, Any]]:
        """Unarchived memory counts grouped by memory_type."""
        query = """
            MATCH (m:Memory)
            WHERE NOT m:Archived
            RETURN m.memory_type AS memory_type, count(*) AS count
            ORDER BY count DESC
            """

        return cast(LiteralString, query), {}


class DreamJobQueries:
    """All dream job/maintenance queries in one place."""

    @staticmethod
    def decay_salience() -> tuple[LiteralString, dict[str, Any]]:
        """Apply exponential decay to memory salience based on ELAPSED TIME.

        salience(t) = floor + (salience - floor) * exp(-lambda * days_elapsed)

        Anchored at `salience_updated_at` (epoch float), so the job is
        idempotent with respect to wall-clock time: running it every minute
        or once a week produces the same trajectory. Pinned memories and
        already-archived memories are untouched.

        Params: $now (epoch float), $decay_lambda (per-day), $floor
        """
        query = """
            MATCH (m:Memory)
            WHERE m.salience IS NOT NULL
              AND coalesce(m.pinned, false) = false
              AND NOT m:Archived
              AND m.salience > $floor
            WITH m, ($now - coalesce(m.salience_updated_at, m.timestamp, $now)) / 86400.0 AS days
            WHERE days > 0
            SET m.salience = $floor + (m.salience - $floor) * exp(-$decay_lambda * days),
                m.salience_updated_at = $now
            RETURN count(m) AS updated
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def archive_stale_memories() -> tuple[LiteralString, dict[str, Any]]:
        """Archive (never delete) low-salience, long-unaccessed memories.

        Adds the :Archived label, which excludes the memory from all recall
        paths. Fully reversible; nothing is destroyed.

        Params: $threshold (salience), $cutoff (epoch float, last-access horizon)
        """
        query = """
            MATCH (m:Memory)
            WHERE coalesce(m.pinned, false) = false
              AND NOT m:Archived
              AND m.salience IS NOT NULL AND m.salience < $threshold
              AND coalesce(m.last_accessed, m.timestamp) < $cutoff
            SET m:Archived
            RETURN count(m) AS archived
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def find_unassigned_memories() -> tuple[LiteralString, dict[str, Any]]:
        """Find recent memories without topic assignments."""
        query = """
            MATCH (m:Memory)
            WHERE m.topic_id IS NULL
              AND m.timestamp > $cutoff
              AND m.embedding IS NOT NULL
              AND NOT m:Archived
            RETURN m.id AS id, m.embedding AS embedding
            ORDER BY m.timestamp DESC
            LIMIT 500
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def assign_topic() -> tuple[LiteralString, dict[str, Any]]:
        """Assign topic ID to a memory."""
        query = """MATCH (m:Memory {id: $id}) SET m.topic_id = $topic_id"""

        return cast(LiteralString, query), {}

    @staticmethod
    def assign_topics_batch() -> tuple[LiteralString, dict[str, Any]]:
        """Atomically apply one complete topic snapshot.

        Params: $updates (maps with id and topic_id)
        """
        query = """
            UNWIND $updates AS update
            MATCH (m:Memory {id: update.id})
            WITH collect({node: m, topic_id: update.topic_id}) AS matched
            WHERE size(matched) = size($updates)
            UNWIND matched AS item
            WITH item.node AS m, item.topic_id AS topic_id
            SET m.topic_id = CASE WHEN topic_id = -1 THEN null ELSE topic_id END
            RETURN count(m) AS updated
            """
        return cast(LiteralString, query), {}

    @staticmethod
    def get_all_memories_for_clustering() -> tuple[LiteralString, dict[str, Any]]:
        """Get a bounded, recent non-archived sample for clustering.

        Params: $limit
        """
        query = """
            MATCH (m:Memory)
            WHERE m.embedding IS NOT NULL AND NOT m:Archived
            RETURN m.id AS id, m.embedding AS embedding, m.topic_id AS current_topic
            ORDER BY m.timestamp DESC
            LIMIT $limit
            """

        return cast(LiteralString, query), {}


class ConsolidationQueries:
    """Queries for the episodic → semantic consolidation dream job."""

    @staticmethod
    def find_conversation_cohorts() -> tuple[LiteralString, dict[str, Any]]:
        """Un-consolidated episodic memories grouped by conversation.

        Params: $min_cohort, $max_cohorts, $max_cohort_size
        """
        query = """
            MATCH (m:Memory)
            WHERE (m:FriendUtterance OR m:ClaudeUtterance)
              AND NOT m:Archived
              AND coalesce(m.consolidated, false) = false
              AND m.conversation_id IS NOT NULL
            WITH m ORDER BY m.timestamp
            WITH m.conversation_id AS cohort_key, collect({
                id: m.id, content: m.content, memory_type: m.memory_type,
                timestamp: m.timestamp, salience: m.salience,
                emotional_valence: m.emotional_valence,
                emotional_intensity: m.emotional_intensity
            }) AS episodes
            WHERE size(episodes) >= $min_cohort
            RETURN cohort_key, episodes[0..$max_cohort_size] AS episodes
            ORDER BY size(episodes) DESC
            LIMIT $max_cohorts
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def find_daily_cohorts() -> tuple[LiteralString, dict[str, Any]]:
        """Un-consolidated conversation-less memories grouped by UTC day.

        Params: $min_cohort, $max_cohorts, $max_cohort_size
        """
        query = """
            MATCH (m:Memory)
            WHERE (m:FriendUtterance OR m:ClaudeUtterance)
              AND NOT m:Archived
              AND coalesce(m.consolidated, false) = false
              AND m.conversation_id IS NULL
            WITH m ORDER BY m.timestamp
            WITH toString(toInteger(m.timestamp / 86400.0)) AS cohort_key, collect({
                id: m.id, content: m.content, memory_type: m.memory_type,
                timestamp: m.timestamp, salience: m.salience,
                emotional_valence: m.emotional_valence,
                emotional_intensity: m.emotional_intensity
            }) AS episodes
            WHERE size(episodes) >= $min_cohort
            RETURN cohort_key, episodes[0..$max_cohort_size] AS episodes
            ORDER BY size(episodes) DESC
            LIMIT $max_cohorts
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def finalize_consolidation() -> tuple[LiteralString, dict[str, Any]]:
        """Atomically store a consolidation, link sources, and mark them.

        Params: $id, $properties, $source_ids
        """
        query = """
            UNWIND $source_ids AS source_id
            MATCH (source:Memory {id: source_id})
            WITH collect(source) AS sources
            WHERE size(sources) = size($source_ids)
            MERGE (c:Memory:Consolidation {id: $id})
            ON CREATE SET c += $properties
            FOREACH (source IN sources |
                SET source.consolidated = true
                MERGE (c)-[:CONSOLIDATED_FROM {strength: 1.0}]->(source)
            )
            RETURN c
            """

        return cast(LiteralString, query), {}


class SchemaQueries:
    """Database constraints required by repository identity semantics."""

    @staticmethod
    def create_constraints() -> list[tuple[LiteralString, dict[str, Any]]]:
        statements = [
            "CREATE CONSTRAINT memory_id_unique IF NOT EXISTS FOR (m:Memory) REQUIRE m.id IS UNIQUE",
            "CREATE CONSTRAINT oauth_client_id_unique IF NOT EXISTS FOR (c:OAuthClient) REQUIRE c.client_id IS UNIQUE",
            "CREATE CONSTRAINT oauth_code_unique IF NOT EXISTS FOR (c:OAuthCode) REQUIRE c.code IS UNIQUE",
            "CREATE CONSTRAINT oauth_refresh_token_unique IF NOT EXISTS FOR (t:OAuthRefreshToken) REQUIRE t.token IS UNIQUE",
            "CREATE CONSTRAINT embedding_cache_key_unique IF NOT EXISTS FOR (e:EmbeddingCache) REQUIRE e.cache_key IS UNIQUE",
            "CREATE CONSTRAINT embedding_schema_name_unique IF NOT EXISTS FOR (s:EmbeddingSchema) REQUIRE s.name IS UNIQUE",
            "CREATE CONSTRAINT consolidation_cohort_unique IF NOT EXISTS FOR (c:Consolidation) REQUIRE c.cohort_fingerprint IS UNIQUE",
        ]
        return [(cast(LiteralString, statement), {}) for statement in statements]


class OAuthQueries:
    """Queries for OAuth state persistence (DCR clients + auth codes).

    Client registrations must survive restarts (claude.ai stores its
    client_id); auth codes are 10-minute single-use ephemera.
    """

    @staticmethod
    def get_client() -> tuple[LiteralString, dict[str, Any]]:
        """Params: $client_id"""
        query = """
            MATCH (c:OAuthClient {client_id: $client_id})
            RETURN c.client_id AS client_id, c.data_json AS data_json
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def save_client() -> tuple[LiteralString, dict[str, Any]]:
        """Params: $client_id, $data_json, $now"""
        query = """
            MERGE (c:OAuthClient {client_id: $client_id})
            ON CREATE SET c.created_at = $now
            SET c.data_json = $data_json, c.updated_at = $now
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def save_auth_code() -> tuple[LiteralString, dict[str, Any]]:
        """Store a code and opportunistically purge expired ones.

        Params: $code, $data_json, $expires_at, $now
        """
        query = """
            OPTIONAL MATCH (stale:OAuthCode)
            WHERE stale.expires_at < $now
            DETACH DELETE stale
            WITH count(*) AS _
            WITH _
            OPTIONAL MATCH (prior:OAuthCode {client_id: $client_id})
            DETACH DELETE prior
            WITH count(*) AS _
            CREATE (c:OAuthCode {
                code: $code,
                client_id: $client_id,
                data_json: $data_json,
                expires_at: $expires_at
            })
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def get_auth_code() -> tuple[LiteralString, dict[str, Any]]:
        """Fetch a still-valid code without consuming it.

        Params: $code, $now
        """
        query = """
            MATCH (c:OAuthCode {code: $code})
            WHERE c.expires_at >= $now
            RETURN c.data_json AS data_json
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def consume_auth_code() -> tuple[LiteralString, dict[str, Any]]:
        """Atomically fetch-and-delete a code (single use).

        Returns data_json and whether it was still valid at $now.

        Params: $code, $now
        """
        query = """
            MATCH (c:OAuthCode {code: $code})
            WITH c, c.data_json AS data_json, c.expires_at >= $now AS valid
            DETACH DELETE c
            RETURN data_json, valid
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def save_refresh_token() -> tuple[LiteralString, dict[str, Any]]:
        """Persist one new refresh-token family and purge expired state.

        Params: $token, $client_id, $family_id, $data_json, $expires_at, $now
        """
        query = """
            OPTIONAL MATCH (stale:OAuthRefreshToken)
            WHERE stale.expires_at < $now
            DETACH DELETE stale
            WITH count(*) AS _
            CREATE (:OAuthRefreshToken {
                token: $token,
                client_id: $client_id,
                family_id: $family_id,
                data_json: $data_json,
                expires_at: $expires_at,
                created_at: $now
            })
            """
        return cast(LiteralString, query), {}

    @staticmethod
    def rotate_refresh_token() -> tuple[LiteralString, dict[str, Any]]:
        """Atomically rotate a token or revoke its family on reuse.

        Params: $presented_token, $replacement_token, $client_id, $family_id,
        $data_json, $expires_at, $now
        """
        query = """
            OPTIONAL MATCH (old:OAuthRefreshToken {
                token: $presented_token,
                client_id: $client_id,
                family_id: $family_id
            })
            WITH old, old IS NOT NULL AND old.expires_at >= $now AS valid
            OPTIONAL MATCH (family:OAuthRefreshToken {client_id: $client_id, family_id: $family_id})
            WITH old, valid, collect(CASE WHEN NOT valid THEN family ELSE null END) AS compromised
            FOREACH (token IN compromised | DELETE token)
            FOREACH (_ IN CASE WHEN valid THEN [1] ELSE [] END |
                DELETE old
                CREATE (:OAuthRefreshToken {
                    token: $replacement_token,
                    client_id: $client_id,
                    family_id: $family_id,
                    data_json: $data_json,
                    expires_at: $expires_at,
                    created_at: $now
                })
            )
            RETURN valid AS rotated
            """
        return cast(LiteralString, query), {}


class CacheQueries:
    """Queries for the Neo4j-backed embedding cache."""

    @staticmethod
    def get_cached_embedding() -> tuple[LiteralString, dict[str, Any]]:
        """Fetch a cached embedding (model-scoped, 30-day TTL), counting the hit.

        Params: $key, $model
        """
        query = """
            MATCH (e:EmbeddingCache {cache_key: $key, model: $model})
            WHERE e.created > datetime() - duration('P30D')
            SET e.hit_count = coalesce(e.hit_count, 0) + 1
            RETURN e.vector AS embedding
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def store_embedding() -> tuple[LiteralString, dict[str, Any]]:
        """Store an embedding in the cache with model metadata.

        Params: $key, $model, $embedding, $dimensions, $text
        """
        query = """
            MERGE (e:EmbeddingCache {cache_key: $key, model: $model})
            ON CREATE SET e.hit_count = 0
            SET e.vector = $embedding,
                e.dimensions = $dimensions,
                e.created = datetime(),
                e.text_preview = left($text, 100)
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def get_cache_stats() -> tuple[LiteralString, dict[str, Any]]:
        """Get statistics about the embedding cache."""
        query = """
            MATCH (e:EmbeddingCache)
            RETURN count(e) AS size,
                   sum(coalesce(e.hit_count, 0)) AS total_hits
            """

        return cast(LiteralString, query), {}


class EmbeddingSchemaQueries:
    """Corpus-level embedding-space compatibility descriptor."""

    @staticmethod
    def get_descriptor() -> tuple[LiteralString, dict[str, Any]]:
        query = """
            MATCH (s:EmbeddingSchema {name: 'memory_embeddings'})
            RETURN s.model AS model, s.dimensions AS dimensions
            """
        return cast(LiteralString, query), {}

    @staticmethod
    def inspect_corpus() -> tuple[LiteralString, dict[str, Any]]:
        query = """
            MATCH (m:Memory)
            WHERE m.embedding IS NOT NULL
            RETURN count(m) AS embedded,
                   collect(DISTINCT m.embedding_model) AS models,
                   collect(DISTINCT m.embedding_dimensions) AS declared_dimensions,
                   min(size(m.embedding)) AS min_dimensions,
                   max(size(m.embedding)) AS max_dimensions,
                   sum(CASE WHEN m.embedding_model IS NULL OR m.embedding_dimensions IS NULL THEN 1 ELSE 0 END)
                       AS missing_provenance
            """
        return cast(LiteralString, query), {}

    @staticmethod
    def ensure_descriptor() -> tuple[LiteralString, dict[str, Any]]:
        query = """
            MERGE (s:EmbeddingSchema {name: 'memory_embeddings'})
            ON CREATE SET s.model = $model, s.dimensions = $dimensions, s.created_at = datetime()
            RETURN s.model AS model, s.dimensions AS dimensions
            """
        return cast(LiteralString, query), {}

    @staticmethod
    def replace_descriptor() -> tuple[LiteralString, dict[str, Any]]:
        query = """
            MERGE (s:EmbeddingSchema {name: 'memory_embeddings'})
            SET s.model = $model, s.dimensions = $dimensions, s.updated_at = datetime()
            """
        return cast(LiteralString, query), {}

    @staticmethod
    def adopt_legacy_provenance() -> tuple[LiteralString, dict[str, Any]]:
        """Atomically adopt a proven uniform legacy corpus into one vector space."""
        query = """
            MATCH (m:Memory)
            WHERE m.embedding IS NOT NULL
            WITH collect(m) AS memories,
                 collect(DISTINCT size(m.embedding)) AS actual_dimensions,
                 collect(DISTINCT m.embedding_model) AS existing_models,
                 collect(DISTINCT m.embedding_dimensions) AS existing_declared_dimensions
            OPTIONAL MATCH (existing:EmbeddingSchema {name: 'memory_embeddings'})
            WITH memories, actual_dimensions, existing_models, existing_declared_dimensions, existing
            WHERE size(memories) > 0
              AND actual_dimensions = [$dimensions]
              AND (size(existing_models) = 0 OR existing_models = [$model])
              AND (size(existing_declared_dimensions) = 0 OR existing_declared_dimensions = [$dimensions])
              AND (existing IS NULL OR (existing.model = $model AND existing.dimensions = $dimensions))
            FOREACH (memory IN memories |
                SET memory.embedding_model = $model,
                    memory.embedding_dimensions = $dimensions
            )
            MERGE (schema:EmbeddingSchema {name: 'memory_embeddings'})
            SET schema.model = $model,
                schema.dimensions = $dimensions,
                schema.updated_at = datetime()
            RETURN size(memories) AS adopted
            """
        return cast(LiteralString, query), {}


class VectorIndexQueries:
    """Queries for managing Neo4j vector indexes."""

    @staticmethod
    def check_vector_index() -> tuple[LiteralString, dict[str, Any]]:
        """Check if vector index exists and get its configuration."""
        query = """
            SHOW INDEXES
            YIELD name, type, labelsOrTypes, properties, options, state
            WHERE name = 'memory_embeddings'
            RETURN type, labelsOrTypes, properties, options, state
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def drop_vector_index() -> tuple[LiteralString, dict[str, Any]]:
        """Drop the existing vector index."""
        query = "DROP INDEX memory_embeddings IF EXISTS"

        return cast(LiteralString, query), {}

    @staticmethod
    def create_vector_index(dimensions: int) -> tuple[LiteralString, dict[str, Any]]:
        """Create vector index with specified dimensions."""
        if not 1 <= dimensions <= 4_096:
            raise ValueError("vector dimensions must be between 1 and 4096")
        query = f"""
            CREATE VECTOR INDEX memory_embeddings IF NOT EXISTS
            FOR (m:Memory) ON m.embedding
            OPTIONS {{indexConfig: {{
              `vector.dimensions`: {dimensions},
              `vector.similarity_function`: 'cosine'
            }}}}
            """

        return cast(LiteralString, query), {}


class QueryFactory:
    """Assembles complete queries with runtime parameters."""

    @staticmethod
    def build_similarity_search(
        embedding: list[float],
        threshold: float,
        limit: int,
        offset: int = 0,
        labels: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build a complete similarity search query with parameters."""
        from memory_palace.core.constants import VECTOR_SEARCH_K_MULTIPLIER

        filter_clause = None
        params: dict[str, Any] = {
            "embedding": embedding,
            "threshold": threshold,
            "limit": limit,
            "offset": offset,
            "k": max(limit * VECTOR_SEARCH_K_MULTIPLIER, 50),  # Widen k for better recall
        }

        if filters:
            from memory_palace.infrastructure.neo4j.filter_compiler import compile_filters

            where_clause, filter_params = compile_filters(filters, alias="node")
            if where_clause.startswith("WHERE "):
                filter_clause = where_clause[6:]
                params.update(filter_params)

        query, _ = MemoryQueries.similarity_search(labels=labels, additional_filters=filter_clause)

        return query, params

    @staticmethod
    def build_filtered_recall(
        labels: list[str], filters: dict[str, Any] | None, limit: int, offset: int = 0
    ) -> tuple[str, dict[str, Any]]:
        """Build a filtered recall query."""
        labels_str = _validated_labels(labels)

        conditions = ["NOT m:Archived"]
        where_params: dict[str, Any] = {}
        if filters:
            from memory_palace.infrastructure.neo4j.filter_compiler import compile_filters

            where_clause, where_params = compile_filters(filters, alias="m")
            if where_clause.startswith("WHERE "):
                conditions.append(where_clause[6:])

        query = f"""
            MATCH (m:{labels_str})
            WHERE {" AND ".join(conditions)}
            RETURN m
            ORDER BY m.timestamp DESC
            SKIP $offset LIMIT $limit
            """

        params = {"offset": offset, "limit": limit, **where_params}

        return query, params
