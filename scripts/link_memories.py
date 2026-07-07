#!/usr/bin/env python3
"""Post-hoc relationship detection for memories with no edges.

Bulk imports skip per-memory relationship detection for speed, leaving
new memories disconnected — invisible to spread-activation recall. This
pass finds unlinked, unarchived memories and runs the same semantic
detection encoding uses (vector top-k above a similarity threshold).

Usage:
    uv run python scripts/link_memories.py [--threshold 0.78] [--dry-run]
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from neo4j import AsyncGraphDatabase

from memory_palace.core.config import settings
from memory_palace.core.logging import get_logger, setup_logging
from memory_palace.infrastructure.embeddings.factory import create_embedding_service
from memory_palace.infrastructure.repositories.memory import MemoryRepository
from memory_palace.services.memory_service import MemoryService

setup_logging()
logger = get_logger(__name__)


async def main(threshold: float, dry_run: bool) -> None:
    driver = AsyncGraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
    embeddings = create_embedding_service(neo4j_driver=driver, use_cache=True)

    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (m:Memory)
            WHERE NOT m:Archived AND m.embedding IS NOT NULL
              AND NOT (m)--(:Memory)
            RETURN m
            ORDER BY m.timestamp
            """
        )
        repo = MemoryRepository(session)
        unlinked = []
        async for record in result:
            memory = repo._validate_union_record(record["m"])
            if memory is not None:
                unlinked.append(memory)

        logger.info(f"Found {len(unlinked)} unlinked memories")
        if dry_run:
            print(f"DRY RUN: would attempt linking for {len(unlinked)} memories at threshold {threshold}")
            await driver.close()
            return

        service = MemoryService(session=session, embeddings=embeddings, clusterer=None)
        linked_count = 0
        edge_count = 0
        for i, memory in enumerate(unlinked, 1):
            relationships = await service._detect_and_create_relationships(memory, threshold) or []
            if relationships:
                linked_count += 1
                edge_count += len(relationships)
            if i % 50 == 0:
                logger.info(f"Processed {i}/{len(unlinked)} ({edge_count} edges so far)")

    await driver.close()
    print(f"Linked {linked_count}/{len(unlinked)} memories with {edge_count} new edges (threshold {threshold})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=float, default=0.78)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.threshold, args.dry_run))
