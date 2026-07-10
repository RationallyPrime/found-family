#!/usr/bin/env python3
"""Re-embed every content-bearing memory with the current voyage model.

Required when changing embedding model families (e.g. voyage-3 → voyage-4):
embeddings from different families are not compatible in one vector space,
and query embeddings must live in the same space as document embeddings.

Dimension changes are handled automatically: if the new model's dimensions
differ from the existing vector index, the index is dropped and recreated
(the app's startup ensure_vector_index would do the same).

Take a backup first (scripts/backup_graph.py). Idempotent.

Usage:
    uv run python scripts/reembed_corpus.py          # read-only plan
    uv run python scripts/reembed_corpus.py --apply  # mutate the corpus
"""

import argparse
import asyncio

from neo4j import AsyncGraphDatabase

from memory_palace.core.config import settings
from memory_palace.core.logging import get_logger, setup_logging
from memory_palace.infrastructure.embeddings.factory import create_embedding_service
from memory_palace.infrastructure.neo4j.driver import ensure_vector_index
from memory_palace.infrastructure.neo4j.queries import EmbeddingSchemaQueries

setup_logging()
logger = get_logger(__name__)

BATCH_SIZE = 50


async def main(dry_run: bool) -> None:
    driver = AsyncGraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password_value))
    try:
        embeddings = create_embedding_service(neo4j_driver=driver, use_cache=False)
        model = embeddings.model if hasattr(embeddings, "model") else settings.voyage_model
        dimensions = embeddings.get_model_dimensions()

        async with driver.session() as session:
            result = await session.run(
                "MATCH (m:Memory) WHERE m.content IS NOT NULL "
                "RETURN m.id AS id, m.content AS content ORDER BY m.timestamp"
            )
            rows = [dict(record) async for record in result]
            logger.info("Corpus re-embedding plan", memories=len(rows), model=model, dimensions=dimensions)

            if dry_run:
                print(f"DRY RUN: would re-embed {len(rows)} memories with {model}")
                return

            done = 0
            for start in range(0, len(rows), BATCH_SIZE):
                batch = rows[start : start + BATCH_SIZE]
                vectors = await embeddings.embed_batch([r["content"] for r in batch])
                await session.run(
                    """
                    UNWIND $updates AS u
                    MATCH (m:Memory {id: u.id})
                    SET m.embedding = u.embedding,
                        m.embedding_model = $model,
                        m.embedding_dimensions = $dimensions
                    """,
                    updates=[{"id": r["id"], "embedding": v} for r, v in zip(batch, vectors, strict=True)],
                    model=model,
                    dimensions=dimensions,
                )
                done += len(batch)
                logger.info("Corpus re-embedding progress", completed=done, total=len(rows))

        async with driver.session() as session:
            descriptor_query, descriptor_params = EmbeddingSchemaQueries.replace_descriptor()
            await session.run(
                descriptor_query,
                {**descriptor_params, "model": model, "dimensions": dimensions},
            )

        await ensure_vector_index(driver, dimensions=dimensions)
        print(f"Re-embedded {len(rows)} memories with {model} ({dimensions} dims)")
    finally:
        await driver.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply changes; default is a read-only plan")
    arguments = parser.parse_args()
    asyncio.run(main(dry_run=not arguments.apply))
