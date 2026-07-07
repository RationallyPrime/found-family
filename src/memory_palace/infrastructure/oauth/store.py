"""OAuth state store: one protocol, swappable backing.

DCR client registrations must survive restarts — claude.ai stores its
client_id and presents it on every reconnect; losing registrations forces
a full re-registration dance (or, historically, a spoofable auto-register
bypass). Auth codes are 10-minute single-use ephemera.

Backed by Neo4j because the palace already runs, backs up, and monitors
exactly one datastore. If state ever outgrows it, implement the protocol
over something else — callers never know.
"""

import json
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from neo4j import AsyncDriver

from memory_palace.core.base import ErrorLevel
from memory_palace.core.decorators import with_error_handling, with_session
from memory_palace.core.logging import get_logger
from memory_palace.infrastructure.neo4j.queries import OAuthQueries

logger = get_logger(__name__)


@runtime_checkable
class OAuthStateStore(Protocol):
    """Persistence contract for OAuth clients and authorization codes."""

    async def get_client(self, client_id: str) -> dict[str, Any] | None: ...

    async def save_client(self, client_id: str, data: dict[str, Any]) -> None: ...

    async def save_auth_code(self, code: str, data: dict[str, Any], ttl_seconds: int) -> None: ...

    async def consume_auth_code(self, code: str) -> dict[str, Any] | None:
        """Fetch-and-delete (single use). Returns None if unknown or expired."""
        ...


class Neo4jOAuthStateStore:
    """Neo4j-backed OAuth state store."""

    def __init__(self, driver: AsyncDriver):
        self.driver = driver

    @with_session()
    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def get_client(self, session, client_id: str) -> dict[str, Any] | None:
        query, _ = OAuthQueries.get_client()
        result = await session.run(query, client_id=client_id)
        record = await result.single()
        return json.loads(record["data_json"]) if record else None

    @with_session()
    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def save_client(self, session, client_id: str, data: dict[str, Any]) -> None:
        query, _ = OAuthQueries.save_client()
        await session.run(
            query,
            client_id=client_id,
            data_json=json.dumps(data),
            now=datetime.now(UTC).timestamp(),
        )
        logger.info("Persisted OAuth client registration", client_id=client_id)

    @with_session()
    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def save_auth_code(self, session, code: str, data: dict[str, Any], ttl_seconds: int) -> None:
        now = datetime.now(UTC).timestamp()
        query, _ = OAuthQueries.save_auth_code()
        await session.run(
            query,
            code=code,
            data_json=json.dumps(data),
            expires_at=now + ttl_seconds,
            now=now,
        )

    @with_session()
    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def consume_auth_code(self, session, code: str) -> dict[str, Any] | None:
        query, _ = OAuthQueries.consume_auth_code()
        result = await session.run(query, code=code, now=datetime.now(UTC).timestamp())
        record = await result.single()
        if record is None or not record["valid"]:
            return None
        return json.loads(record["data_json"])
