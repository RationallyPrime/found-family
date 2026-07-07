#!/usr/bin/env python3
"""Dump the entire Neo4j graph to a timestamped JSON file.

Backs up every node (labels + properties, including embeddings) and every
relationship (type + properties + endpoint node ids) so the graph can be
fully reconstructed. Run before any schema migration or destructive job.

Usage:
    uv run python scripts/backup_graph.py [output_dir]
"""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).parent.parent))

from neo4j import AsyncGraphDatabase

from memory_palace.core.config import settings
from memory_palace.core.logging import get_logger

logger = get_logger(__name__)


def _jsonable(value: Any) -> Any:
    """Coerce Neo4j driver types (DateTime, Date, etc.) to JSON-safe values."""
    if hasattr(value, "iso_format"):
        return value.iso_format()
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


async def backup(output_dir: Path) -> Path:
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )

    async with driver.session() as session:
        node_result = await session.run(
            "MATCH (n) RETURN elementId(n) AS eid, labels(n) AS labels, properties(n) AS props"
        )
        nodes = [
            {
                "element_id": record["eid"],
                "labels": record["labels"],
                "properties": _jsonable(record["props"]),
            }
            async for record in node_result
        ]

        rel_result = await session.run(
            "MATCH (a)-[r]->(b) "
            "RETURN elementId(r) AS eid, type(r) AS type, properties(r) AS props, "
            "elementId(a) AS source_eid, elementId(b) AS target_eid"
        )
        relationships = [
            {
                "element_id": record["eid"],
                "type": record["type"],
                "properties": _jsonable(record["props"]),
                "source_element_id": record["source_eid"],
                "target_element_id": record["target_eid"],
            }
            async for record in rel_result
        ]

    await driver.close()

    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = output_dir / f"graph-{stamp}.json"
    path.write_text(
        json.dumps(
            {
                "backed_up_at": datetime.now(UTC).isoformat(),
                "node_count": len(nodes),
                "relationship_count": len(relationships),
                "nodes": nodes,
                "relationships": relationships,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    logger.info(
        "Graph backup complete",
        path=str(path),
        nodes=len(nodes),
        relationships=len(relationships),
    )
    return path


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent / "data" / "backups"
    result_path = asyncio.run(backup(out))
    print(f"Backup written to {result_path}")
