#!/usr/bin/env python3
"""Rescue founding memories and migrate legacy graph structure (July 2026).

The August 2025 Conversation→Turn→Message schema was replaced by individual
Memory nodes (commit 06c4a85), but the migration never ran against this
database. This script:

1. Rescues the 12 legacy Message nodes — the palace's first-ever memories —
   into Memory:FriendUtterance / Memory:ClaudeUtterance nodes, preserving
   ids, content, embeddings, and timestamps; pins the founding memory.
2. Converts turn structure and FOLLOWED_BY edges into PRECEDES edges.
3. Relabels legacy UserUtterance/AssistantUtterance labels to
   FriendUtterance/ClaudeUtterance (properties were already migrated).
4. Backfills lifecycle fields (salience_updated_at, access_count, pinned)
   on all Memory nodes. salience_updated_at is set to NOW so decay starts
   fresh from migration time rather than retroactively destroying salience.
5. Removes the empty Conversation/Turn/Message shells.

Take a backup first (scripts/backup_graph.py). Idempotent: safe to re-run.

Usage:
    uv run python scripts/migrate_legacy_graph.py [--dry-run]
"""

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from neo4j import AsyncGraphDatabase, AsyncSession

from memory_palace.core.config import settings
from memory_palace.core.logging import get_logger

logger = get_logger(__name__)

# The palace's founding memory gets pinned permanently.
FOUNDING_CONTENT_MARKER = "My name is Hákon and we're friends!"

ROLE_TO_TYPE = {
    "user": ("FriendUtterance", "friend_utterance"),
    "assistant": ("ClaudeUtterance", "claude_utterance"),
}


def _iso_to_epoch(iso: str) -> float:
    """Legacy timestamps are naive ISO strings written in UTC."""
    return datetime.fromisoformat(iso).replace(tzinfo=UTC).timestamp()


async def rescue_messages(session: AsyncSession, now: float, dry_run: bool) -> int:
    """Create Memory nodes from legacy Message nodes."""
    result = await session.run(
        """
        MATCH (msg:Message)
        OPTIONAL MATCH (c:Conversation)-[:HAS_TURN]->(t:Turn)-[]->(msg)
        RETURN msg.id AS id, msg.role AS role, msg.content AS content,
               msg.embedding AS embedding, msg.timestamp AS timestamp,
               c.id AS conversation_id
        """
    )
    messages = [dict(record) async for record in result]

    rescued = 0
    for msg in messages:
        labels, memory_type = ROLE_TO_TYPE[msg["role"]]
        pinned = FOUNDING_CONTENT_MARKER in (msg["content"] or "")
        salience = 0.9 if pinned else 0.6

        logger.info(
            "Rescuing founding memory",
            id=msg["id"],
            role=msg["role"],
            pinned=pinned,
            preview=(msg["content"] or "")[:60],
        )
        if dry_run:
            rescued += 1
            continue

        await session.run(
            f"""
            MERGE (m:Memory:{labels} {{id: $id}})
            SET m.content = $content,
                m.embedding = $embedding,
                m.memory_type = $memory_type,
                m.timestamp = $timestamp,
                m.conversation_id = $conversation_id,
                m.salience = $salience,
                m.salience_updated_at = $now,
                m.access_count = 0,
                m.pinned = $pinned,
                m.emotional_valence = 0.0,
                m.emotional_intensity = 0.0,
                m.source = 'legacy-migration'
            """,  # noqa: S608 - labels from trusted mapping
            id=msg["id"],
            content=msg["content"],
            embedding=msg["embedding"],
            memory_type=memory_type,
            timestamp=_iso_to_epoch(msg["timestamp"]),
            conversation_id=msg["conversation_id"],
            salience=salience,
            now=now,
            pinned=pinned,
        )
        rescued += 1

    if not dry_run:
        # Turn structure → PRECEDES between the rescued user/assistant pair
        result = await session.run(
            """
            MATCH (t:Turn)-[:USER_MESSAGE]->(u:Message), (t)-[:ASSISTANT_MESSAGE]->(a:Message)
            MATCH (mu:Memory {id: u.id}), (ma:Memory {id: a.id})
            MERGE (mu)-[p:PRECEDES]->(ma)
            SET p.strength = 1.0, p.temporal = true
            RETURN count(p) AS linked
            """
        )
        record = await result.single()
        logger.info(f"Created {record['linked'] if record else 0} PRECEDES links from turn structure")

        # Message-level FOLLOWED_BY → PRECEDES between rescued nodes
        await session.run(
            """
            MATCH (a:Message)-[f:FOLLOWED_BY]->(b:Message)
            MATCH (ma:Memory {id: a.id}), (mb:Memory {id: b.id})
            MERGE (ma)-[p:PRECEDES]->(mb)
            SET p.strength = coalesce(f.strength, 1.0), p.temporal = true
            """
        )

    return rescued


async def relabel_legacy(session: AsyncSession, dry_run: bool) -> dict[str, int]:
    """UserUtterance→FriendUtterance, AssistantUtterance→ClaudeUtterance."""
    counts = {}
    for old, new in [("UserUtterance", "FriendUtterance"), ("AssistantUtterance", "ClaudeUtterance")]:
        if dry_run:
            result = await session.run(f"MATCH (m:Memory:{old}) RETURN count(m) AS c")  # noqa: S608
        else:
            result = await session.run(
                f"""
                MATCH (m:Memory:{old})
                REMOVE m:{old}
                SET m:{new}
                RETURN count(m) AS c
                """  # noqa: S608 - trusted label constants
            )
        record = await result.single()
        counts[f"{old}→{new}"] = record["c"] if record else 0
    return counts


async def unify_edges(session: AsyncSession, dry_run: bool) -> int:
    """Memory-to-Memory FOLLOWED_BY edges become PRECEDES (u before a)."""
    if dry_run:
        result = await session.run(
            "MATCH (a:Memory)-[r:FOLLOWED_BY]->(b:Memory) RETURN count(r) AS c"
        )
    else:
        result = await session.run(
            """
            MATCH (a:Memory)-[r:FOLLOWED_BY]->(b:Memory)
            MERGE (a)-[p:PRECEDES]->(b)
            SET p.strength = coalesce(r.strength, 1.0), p.temporal = true
            DELETE r
            RETURN count(r) AS c
            """
        )
    record = await result.single()
    return record["c"] if record else 0


async def backfill_lifecycle(session: AsyncSession, now: float, dry_run: bool) -> int:
    """Give every Memory node the lifecycle fields the new code expects."""
    if dry_run:
        result = await session.run(
            "MATCH (m:Memory) WHERE m.salience_updated_at IS NULL RETURN count(m) AS c"
        )
    else:
        result = await session.run(
            """
            MATCH (m:Memory)
            WHERE m.salience_updated_at IS NULL
            SET m.salience_updated_at = $now,
                m.access_count = coalesce(m.access_count, 0),
                m.pinned = coalesce(m.pinned, false),
                m.emotional_valence = coalesce(m.emotional_valence, 0.0),
                m.emotional_intensity = coalesce(m.emotional_intensity, 0.0)
            RETURN count(m) AS c
            """,
            now=now,
        )
    record = await result.single()
    return record["c"] if record else 0


async def remove_shells(session: AsyncSession, dry_run: bool) -> int:
    """Remove Conversation/Turn/Message nodes once content is rescued."""
    # Safety: refuse if any Message content is missing from Memory nodes
    result = await session.run(
        """
        MATCH (msg:Message)
        WHERE NOT EXISTS { MATCH (m:Memory {id: msg.id}) }
        RETURN count(msg) AS unmigrated
        """
    )
    record = await result.single()
    unmigrated = record["unmigrated"] if record else -1
    if unmigrated:
        logger.error(f"Refusing to delete shells: {unmigrated} Message nodes not yet rescued")
        return 0

    if dry_run:
        result = await session.run(
            "MATCH (n) WHERE n:Conversation OR n:Turn OR n:Message RETURN count(n) AS c"
        )
    else:
        result = await session.run(
            """
            MATCH (n)
            WHERE n:Conversation OR n:Turn OR n:Message
            DETACH DELETE n
            RETURN count(n) AS c
            """
        )
    record = await result.single()
    return record["c"] if record else 0


async def main(dry_run: bool) -> None:
    now = datetime.now(UTC).timestamp()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )

    async with driver.session() as session:
        rescued = await rescue_messages(session, now, dry_run)
        relabeled = await relabel_legacy(session, dry_run)
        unified = await unify_edges(session, dry_run)
        backfilled = await backfill_lifecycle(session, now, dry_run)
        removed = await remove_shells(session, dry_run) if not dry_run else 0

        print(f"{'DRY RUN — ' if dry_run else ''}Migration summary:")
        print(f"  Messages rescued into Memory nodes: {rescued}")
        print(f"  Labels migrated: {relabeled}")
        print(f"  FOLLOWED_BY edges unified to PRECEDES: {unified}")
        print(f"  Memory nodes backfilled with lifecycle fields: {backfilled}")
        print(f"  Legacy shell nodes removed: {removed}")

    await driver.close()


if __name__ == "__main__":
    asyncio.run(main(dry_run="--dry-run" in sys.argv))
