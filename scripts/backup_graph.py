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
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

sys.path.append(str(Path(__file__).parent.parent))

from neo4j import AsyncGraphDatabase, AsyncManagedTransaction
from pydantic import JsonValue

from memory_palace.core.config import settings
from memory_palace.core.logging import get_logger

logger = get_logger(__name__)


def _jsonable(value: object) -> JsonValue:
    """Coerce Neo4j driver types (DateTime, Date, etc.) to JSON-safe values."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    iso_format = getattr(value, "iso_format", None)
    if callable(iso_format):
        return cast("Callable[[], str]", iso_format)()
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


async def _read_consistent_snapshot(tx: AsyncManagedTransaction) -> dict[str, JsonValue]:
    """Read nodes and relationships inside one Neo4j snapshot transaction."""
    node_result = await tx.run(
        "MATCH (n) WHERE NOT n:OAuthCode RETURN elementId(n) AS eid, labels(n) AS labels, properties(n) AS props"
    )
    nodes = [
        {
            "element_id": record["eid"],
            "labels": record["labels"],
            "properties": _jsonable(record["props"]),
        }
        async for record in node_result
    ]
    included_ids = {node["element_id"] for node in nodes}

    rel_result = await tx.run(
        "MATCH (a)-[r]->(b) "
        "WHERE NOT a:OAuthCode AND NOT b:OAuthCode "
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
        if record["source_eid"] in included_ids and record["target_eid"] in included_ids
    ]
    return {
        "format_version": 1,
        "backed_up_at": datetime.now(UTC).isoformat(),
        "node_count": len(nodes),
        "relationship_count": len(relationships),
        "nodes": nodes,
        "relationships": relationships,
    }


def _write_private_atomic(path: Path, payload: str) -> None:
    """Write a complete owner-only backup, then atomically publish it."""
    temporary_path = path.with_suffix(f"{path.suffix}.tmp-{os.getpid()}")
    descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


async def backup(output_dir: Path) -> Path:
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password_value),
    )
    try:
        async with driver.session() as session:
            snapshot = await session.execute_read(_read_consistent_snapshot)
    finally:
        await driver.close()

    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_dir.chmod(0o700)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    path = output_dir / f"graph-{stamp}.json"
    _write_private_atomic(path, json.dumps(snapshot, indent=2, default=str))
    logger.info(
        "Graph backup complete",
        path=str(path),
        nodes=snapshot["node_count"],
        relationships=snapshot["relationship_count"],
    )
    return path


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent / "data" / "backups"
    result_path = asyncio.run(backup(out))
    print(f"Backup written to {result_path}")
