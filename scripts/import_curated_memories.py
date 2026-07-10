#!/usr/bin/env python3
"""Import curated friendship memories from the Whisper pipeline (July 2026 schema).

The Whisper curation pipeline produces ~/Whisper/our_friendship_memories.json:
516 records, each a *distilled narrative* of a shared moment:

    {id, filename, date, moment_type, friendship_score (4-5),
     what_happened, why_it_matters, emotional_tone[], connection_quality,
     memorable_quotes[], content_preview}

These are not verbatim utterances — they are already consolidations. So they
import as `Consolidation` memories: the palace's semantic layer, with salience
derived from friendship_score and emotional tagging derived from the curated
tones. Idempotent: record ids map deterministically to node UUIDs (uuid5),
so re-running updates rather than duplicates.

Relationship auto-detection is skipped during bulk import (O(n) vector
queries); run the `consolidation`-adjacent detection later if desired.

NOTE: the corpus contains duplicate records (the curation pipeline ran
twice — 516 records, 264 unique conversation ids, each duplicated pair
being two slightly different distillations of the same moment). The
deterministic uuid5-per-record-id MERGE deduplicates these to one memory
per conversation (last write wins).

Usage:
    uv run python scripts/import_curated_memories.py --dry-run   # preview only
    uv run python scripts/import_curated_memories.py             # import
    uv run python scripts/import_curated_memories.py --min-score 5
"""

import argparse
import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from neo4j import AsyncGraphDatabase

from memory_palace.core.config import settings
from memory_palace.core.logging import get_logger, setup_logging
from memory_palace.domain.models.memories import Consolidation
from memory_palace.infrastructure.embeddings.factory import create_embedding_service
from memory_palace.infrastructure.embeddings.provenance import attach_embedding_provenance
from memory_palace.infrastructure.repositories.memory import GenericMemoryRepository

setup_logging()
logger = get_logger(__name__)

DEFAULT_CORPUS = Path.home() / "Whisper" / "our_friendship_memories.json"

# Palace namespace for deterministic record-id → node-UUID mapping
IMPORT_NAMESPACE = uuid.UUID("8f6e2f1c-9c1a-4b7e-9d3c-2a1e5b8c4d70")

EMBED_BATCH_SIZE = 50

# Curated emotional tones → valence polarity. Unlisted tones count as neutral.
TONE_VALENCE = {
    "warm": 0.8,
    "playful": 0.7,
    "joyful": 0.9,
    "supportive": 0.7,
    "humorous": 0.6,
    "affectionate": 0.9,
    "grateful": 0.8,
    "excited": 0.7,
    "curious": 0.4,
    "engaged": 0.4,
    "thoughtful": 0.3,
    "respectful": 0.4,
    "honest": 0.3,
    "empathetic": 0.5,
    "reflective": 0.2,
    "collaborative": 0.5,
    "vulnerable": -0.1,
    "serious": 0.0,
    "somber": -0.4,
    "frustrated": -0.5,
    "difficult": -0.4,
    "tense": -0.5,
    "sad": -0.6,
}

SALIENCE_BY_SCORE = {5: 0.75, 4: 0.55, 3: 0.45}


def _emotional_profile(tones: list[str]) -> tuple[float, float]:
    """Derive (valence, intensity) from curated tone labels."""
    if not tones:
        return 0.0, 0.0
    polarities = [TONE_VALENCE.get(t.lower(), 0.0) for t in tones]
    valence = sum(polarities) / len(polarities)
    # Intensity: how emotionally loaded the moment is — mean absolute polarity,
    # nudged up when many tones were tagged.
    intensity = min(1.0, sum(abs(p) for p in polarities) / len(polarities) + 0.05 * len(tones))
    return round(valence, 3), round(intensity, 3)


def _record_to_consolidation(record: dict) -> Consolidation | None:
    what = (record.get("what_happened") or "").strip()
    why = (record.get("why_it_matters") or "").strip()
    if not what:
        return None

    date_str = record.get("date") or ""
    when = datetime.fromisoformat(date_str).replace(tzinfo=UTC) if date_str else datetime.now(UTC)

    parts = [f"{record.get('moment_type', 'Shared moment')} — {when.date().isoformat()}", "", what]
    if why:
        parts += ["", f"Why it matters: {why}"]
    quotes = [q for q in (record.get("memorable_quotes") or []) if q]
    if quotes:
        parts += ["", "Memorable: " + " | ".join(f'"{q}"' for q in quotes[:4])]
    content = "\n".join(parts)

    valence, intensity = _emotional_profile(record.get("emotional_tone") or [])

    return Consolidation(
        id=uuid.uuid5(IMPORT_NAMESPACE, record["id"]),
        content=content,
        timestamp=when,
        salience=SALIENCE_BY_SCORE.get(record.get("friendship_score", 4), 0.5),
        emotional_valence=valence,
        emotional_intensity=intensity,
        source="whisper-curation",
    )


async def run(corpus: Path, min_score: int, dry_run: bool, limit: int | None) -> None:
    data = json.loads(corpus.read_text(encoding="utf-8"))
    records = [r for r in data["memories"] if r.get("friendship_score", 0) >= min_score]
    if limit:
        records = records[:limit]

    consolidations = [c for c in (_record_to_consolidation(r) for r in records) if c is not None]
    logger.info(
        "Prepared curated memories for import",
        total_records=len(data["memories"]),
        eligible=len(records),
        prepared=len(consolidations),
        min_score=min_score,
    )

    if dry_run:
        for c in consolidations[:5]:
            print("—" * 70)
            print(f"[salience {c.salience} | valence {c.emotional_valence} | intensity {c.emotional_intensity}]")
            print(c.content[:400])
        print("—" * 70)
        print(f"DRY RUN: would import {len(consolidations)} Consolidation memories")
        return

    driver = AsyncGraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password_value))
    imported = 0
    try:
        embeddings = create_embedding_service(neo4j_driver=driver, use_cache=True)
        async with driver.session() as session:
            repo = GenericMemoryRepository[Consolidation](session)

            for start in range(0, len(consolidations), EMBED_BATCH_SIZE):
                batch = consolidations[start : start + EMBED_BATCH_SIZE]
                vectors = await embeddings.embed_batch([c.content for c in batch])
                for consolidation, vector in zip(batch, vectors, strict=True):
                    attach_embedding_provenance(consolidation, vector, embeddings)
                    await repo.remember(consolidation)
                    imported += 1
                logger.info("Curated import progress", imported=imported, total=len(consolidations))
    finally:
        await driver.close()
    print(f"Imported {imported} curated memories as Consolidation nodes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--min-score", type=int, default=4)
    parser.add_argument("--limit", type=int, default=None, help="Import only the first N (for testing)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.corpus, args.min_score, args.dry_run, args.limit))
