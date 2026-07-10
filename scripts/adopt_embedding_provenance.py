#!/usr/bin/env python3
"""Adopt a proven uniform legacy embedding corpus without regenerating vectors.

This migration is intentionally explicit. Use it only when deployment history
proves which model produced the existing vectors. It validates every embedded
memory's dimensions and rejects conflicting model metadata or schema state.

Usage:
    uv run python scripts/adopt_embedding_provenance.py \
        --model voyage-4-large --dimensions 1024
    uv run python scripts/adopt_embedding_provenance.py \
        --model voyage-4-large --dimensions 1024 --apply
"""

import argparse
import asyncio
from collections.abc import Mapping

from neo4j import AsyncGraphDatabase

from memory_palace.core.config import settings
from memory_palace.infrastructure.neo4j.queries import EmbeddingSchemaQueries


def _metadata_values(record: Mapping[str, object], key: str) -> list[object]:
    value = record[key]
    if not isinstance(value, list):
        raise RuntimeError(f"Unexpected corpus metadata for {key}")
    return value


async def migrate(*, model: str, dimensions: int, apply: bool) -> int:
    """Validate and optionally stamp legacy vector-space metadata."""
    if not 1 <= dimensions <= 4_096:
        raise ValueError("dimensions must be between 1 and 4096")

    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password_value),
    )
    try:
        async with driver.session() as session:
            inspect_query, inspect_params = EmbeddingSchemaQueries.inspect_corpus()
            inspect_result = await session.run(inspect_query, inspect_params)
            corpus = await inspect_result.single(strict=True)

            embedded = int(corpus["embedded"])
            declared_dimensions = {int(value) for value in _metadata_values(corpus, "declared_dimensions")}
            vector_dimensions = {
                int(value) for key in ("min_dimensions", "max_dimensions") if (value := corpus[key]) is not None
            }
            existing_models = {str(value) for value in _metadata_values(corpus, "models")}

            if embedded == 0:
                raise RuntimeError("No embedded memories exist to adopt")
            if vector_dimensions != {dimensions}:
                raise RuntimeError(
                    f"Legacy vectors are not uniformly {dimensions}-dimensional: {sorted(vector_dimensions)}"
                )
            if existing_models - {model}:
                raise RuntimeError(f"Corpus already declares conflicting models: {sorted(existing_models)}")
            if declared_dimensions - {dimensions}:
                raise RuntimeError(f"Corpus already declares conflicting dimensions: {sorted(declared_dimensions)}")

            descriptor_query, descriptor_params = EmbeddingSchemaQueries.get_descriptor()
            descriptor_result = await session.run(descriptor_query, descriptor_params)
            descriptor = await descriptor_result.single()
            if descriptor is not None and (descriptor["model"] != model or int(descriptor["dimensions"]) != dimensions):
                raise RuntimeError("Embedding schema descriptor conflicts with the requested vector space")

            if not apply:
                print(f"DRY RUN: would adopt {embedded} vectors as {model} ({dimensions} dims)")
                return embedded

            adopt_query, adopt_params = EmbeddingSchemaQueries.adopt_legacy_provenance()
            adopt_result = await session.run(
                adopt_query,
                {**adopt_params, "model": model, "dimensions": dimensions},
            )
            adopted = await adopt_result.single()
            if adopted is None or int(adopted["adopted"]) != embedded:
                raise RuntimeError("Corpus changed or failed validation during provenance adoption")
            await adopt_result.consume()
            print(f"Adopted {embedded} vectors as {model} ({dimensions} dims)")
            return embedded
    finally:
        await driver.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Exact model that produced every existing vector")
    parser.add_argument("--dimensions", required=True, type=int)
    parser.add_argument("--apply", action="store_true", help="Apply metadata; default is a read-only proof")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    asyncio.run(
        migrate(
            model=arguments.model,
            dimensions=arguments.dimensions,
            apply=arguments.apply,
        )
    )
