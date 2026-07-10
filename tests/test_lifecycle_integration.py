"""Integration tests for the memory lifecycle queries against a live Neo4j.

Every node created here carries the :TestMemory label; conftest teardown
removes them. Requires the dev Neo4j (docker compose up -d neo4j).
"""

import math
import uuid
from datetime import UTC, datetime

import pytest
from neo4j import AsyncSession

from memory_palace.core.constants import (
    SALIENCE_DECAY_LAMBDA_PER_DAY,
    SALIENCE_FLOOR,
    SALIENCE_REINFORCEMENT_RATE,
)
from memory_palace.infrastructure.neo4j.queries import DreamJobQueries, MemoryQueries

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC).timestamp()


async def _create_test_memory(
    session: AsyncSession,
    *,
    salience: float,
    updated_days_ago: float,
    pinned: bool = False,
    last_accessed_days_ago: float | None = None,
) -> str:
    mid = str(uuid.uuid4())
    await session.run(
        """
        CREATE (m:Memory:TestMemory {
            id: $id, memory_type: 'claude_utterance', content: 'test',
            timestamp: $ts, salience: $salience,
            salience_updated_at: $updated_at, pinned: $pinned,
            access_count: 0, last_accessed: $last_accessed
        })
        """,
        id=mid,
        ts=NOW - 200 * 86400,
        salience=salience,
        updated_at=NOW - updated_days_ago * 86400,
        pinned=pinned,
        last_accessed=(NOW - last_accessed_days_ago * 86400) if last_accessed_days_ago is not None else None,
    )
    return mid


async def _get(session: AsyncSession, mid: str) -> dict:
    result = await session.run("MATCH (m:Memory {id: $id}) RETURN properties(m) AS p, m:Archived AS archived", id=mid)
    record = await result.single()
    return {**record["p"], "_archived": record["archived"]}


async def test_decay_matches_formula(neo4j_session: AsyncSession) -> None:
    """One decay pass must equal the closed-form curve for elapsed time."""
    mid = await _create_test_memory(neo4j_session, salience=0.8, updated_days_ago=45.0)

    query, _ = DreamJobQueries.decay_salience()
    await neo4j_session.run(query, now=NOW, decay_lambda=SALIENCE_DECAY_LAMBDA_PER_DAY, floor=SALIENCE_FLOOR)

    node = await _get(neo4j_session, mid)
    expected = SALIENCE_FLOOR + (0.8 - SALIENCE_FLOOR) * math.exp(-SALIENCE_DECAY_LAMBDA_PER_DAY * 45.0)
    assert math.isclose(node["salience"], expected, rel_tol=1e-6)
    assert node["salience_updated_at"] == NOW  # decay clock re-anchored


async def test_decay_is_cadence_independent_in_cypher(neo4j_session: AsyncSession) -> None:
    """Running the decay query twice must not decay twice for the same time."""
    mid = await _create_test_memory(neo4j_session, salience=0.8, updated_days_ago=45.0)

    query, _ = DreamJobQueries.decay_salience()
    params = {"now": NOW, "decay_lambda": SALIENCE_DECAY_LAMBDA_PER_DAY, "floor": SALIENCE_FLOOR}
    await neo4j_session.run(query, **params)
    first = (await _get(neo4j_session, mid))["salience"]
    await neo4j_session.run(query, **params)  # same $now: zero additional elapsed time
    second = (await _get(neo4j_session, mid))["salience"]

    assert math.isclose(first, second, rel_tol=1e-9)


async def test_pinned_memories_never_decay(neo4j_session: AsyncSession) -> None:
    mid = await _create_test_memory(neo4j_session, salience=0.9, updated_days_ago=365.0, pinned=True)

    query, _ = DreamJobQueries.decay_salience()
    await neo4j_session.run(query, now=NOW, decay_lambda=SALIENCE_DECAY_LAMBDA_PER_DAY, floor=SALIENCE_FLOOR)

    node = await _get(neo4j_session, mid)
    assert node["salience"] == 0.9


async def test_reinforcement_boosts_and_tracks_access(neo4j_session: AsyncSession) -> None:
    mid = await _create_test_memory(neo4j_session, salience=0.5, updated_days_ago=0.0)

    query, _ = MemoryQueries.reinforce_memories()
    await neo4j_session.run(query, ids=[mid], now=NOW, rate=SALIENCE_REINFORCEMENT_RATE)

    node = await _get(neo4j_session, mid)
    assert math.isclose(node["salience"], 0.5 + 0.5 * SALIENCE_REINFORCEMENT_RATE, rel_tol=1e-9)
    assert node["access_count"] == 1
    assert node["last_accessed"] == NOW


async def test_archive_only_takes_stale_unpinned_low_salience(neo4j_session: AsyncSession) -> None:
    stale = await _create_test_memory(
        neo4j_session, salience=0.06, updated_days_ago=100.0, last_accessed_days_ago=100.0
    )
    fresh = await _create_test_memory(neo4j_session, salience=0.06, updated_days_ago=100.0, last_accessed_days_ago=5.0)
    pinned = await _create_test_memory(
        neo4j_session, salience=0.06, updated_days_ago=100.0, last_accessed_days_ago=100.0, pinned=True
    )

    query, _ = DreamJobQueries.archive_stale_memories()
    await neo4j_session.run(query, threshold=0.1, cutoff=NOW - 90 * 86400)

    assert (await _get(neo4j_session, stale))["_archived"] is True
    assert (await _get(neo4j_session, fresh))["_archived"] is False
    assert (await _get(neo4j_session, pinned))["_archived"] is False


async def test_spread_activation_through_typed_edges(neo4j_session: AsyncSession) -> None:
    """a -PRECEDES(1.0)-> b -RELATES_TO(0.8)-> c: activation decays per hop."""
    a = await _create_test_memory(neo4j_session, salience=0.5, updated_days_ago=0.0)
    b = await _create_test_memory(neo4j_session, salience=0.5, updated_days_ago=0.0)
    c = await _create_test_memory(neo4j_session, salience=0.5, updated_days_ago=0.0)
    await neo4j_session.run(
        """
        MATCH (a:Memory {id: $a}), (b:Memory {id: $b}), (c:Memory {id: $c})
        CREATE (a)-[:PRECEDES {strength: 1.0}]->(b)
        CREATE (b)-[:RELATES_TO {strength: 0.8}]->(c)
        """,
        a=a,
        b=b,
        c=c,
    )

    query, _ = MemoryQueries.spread_activation(2)
    result = await neo4j_session.run(query, seeds=[{"id": a, "score": 1.0}], hop_decay=0.7, limit=10)
    activations = {}
    async for record in result:
        activations[dict(record["m"])["id"]] = record["activation"]

    assert math.isclose(activations[b], 1.0 * 1.0 * 0.7, rel_tol=1e-9)
    assert math.isclose(activations[c], 1.0 * 1.0 * 0.7 * 0.8 * 0.7, rel_tol=1e-9)


async def test_archived_memories_excluded_from_spread_activation(neo4j_session: AsyncSession) -> None:
    a = await _create_test_memory(neo4j_session, salience=0.5, updated_days_ago=0.0)
    b = await _create_test_memory(neo4j_session, salience=0.5, updated_days_ago=0.0)
    await neo4j_session.run(
        """
        MATCH (a:Memory {id: $a}), (b:Memory {id: $b})
        CREATE (a)-[:PRECEDES {strength: 1.0}]->(b)
        SET b:Archived
        """,
        a=a,
        b=b,
    )

    query, _ = MemoryQueries.spread_activation(2)
    result = await neo4j_session.run(query, seeds=[{"id": a, "score": 1.0}], hop_decay=0.7, limit=10)
    reached = [dict(record["m"])["id"] async for record in result]
    assert b not in reached


async def test_ordered_batch_and_temporal_edges_commit_together(neo4j_session: AsyncSession) -> None:
    ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    memories = [
        {
            "id": memory_id,
            "position": position,
            "memory_type": memory_type,
            "properties": {
                "id": memory_id,
                "memory_type": memory_type,
                "content": f"batch-{position}",
                "timestamp": NOW,
            },
        }
        for position, (memory_id, memory_type) in enumerate(
            zip(ids, ["friend_utterance", "claude_utterance"], strict=True)
        )
    ]

    try:
        query, _ = MemoryQueries.store_utterance_batch()
        result = await neo4j_session.run(query, memories=memories, create_temporal_links=True)
        assert (await result.single())["stored_ids"] == ids

        edge_result = await neo4j_session.run(
            """
            MATCH (a:Memory {id: $first})-[r:PRECEDES]->(b:Memory {id: $second})
            RETURN count(r) AS edges, a:FriendUtterance AS friend, b:ClaudeUtterance AS claude
            """,
            first=ids[0],
            second=ids[1],
        )
        edge = await edge_result.single()
        assert edge == {"edges": 1, "friend": True, "claude": True}
    finally:
        await neo4j_session.run("MATCH (m:Memory) WHERE m.id IN $ids DETACH DELETE m", ids=ids)
