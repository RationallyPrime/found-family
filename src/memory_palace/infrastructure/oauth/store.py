"""OAuth state store: one protocol, swappable backing.

DCR client registrations must survive restarts — claude.ai stores its
client_id and presents it on every reconnect; losing registrations forces
a full re-registration dance (or, historically, a spoofable auto-register
bypass). Auth codes are 10-minute single-use ephemera.

Backed by Neo4j because the palace already runs, backs up, and monitors
exactly one datastore. If state ever outgrows it, implement the protocol
over something else — callers never know.
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from neo4j import AsyncDriver, AsyncSession
from pydantic import ValidationError

from memory_palace.core.base import ErrorLevel
from memory_palace.core.decorators import with_error_handling, with_session
from memory_palace.core.logging import get_logger
from memory_palace.infrastructure.neo4j.queries import OAuthQueries
from memory_palace.infrastructure.oauth.models import AuthorizationCode, OAuthClient, RefreshTokenState

logger = get_logger(__name__)


@runtime_checkable
class OAuthStateStore(Protocol):
    """Persistence contract for OAuth clients and authorization codes."""

    async def get_client(self, client_id: str) -> OAuthClient | None: ...

    async def save_client(self, client: OAuthClient) -> None: ...

    async def save_auth_code(self, code: str, data: AuthorizationCode, ttl_seconds: int) -> None: ...

    async def get_auth_code(self, code: str) -> AuthorizationCode | None: ...

    async def consume_auth_code(self, code: str) -> AuthorizationCode | None:
        """Fetch-and-delete (single use). Returns None if unknown or expired."""
        ...

    async def save_refresh_token(self, token: str, data: RefreshTokenState, ttl_seconds: int) -> None: ...

    async def rotate_refresh_token(
        self,
        presented_token: str,
        replacement_token: str,
        data: RefreshTokenState,
        ttl_seconds: int,
    ) -> bool:
        """Atomically consume one token and persist its family replacement."""
        ...


class Neo4jOAuthStateStore:
    """Neo4j-backed OAuth state store."""

    def __init__(self, driver: AsyncDriver) -> None:
        self.driver = driver

    @staticmethod
    def _code_digest(code: str) -> str:
        """Never persist bearer-like authorization codes in recoverable form."""
        return hashlib.sha256(code.encode("utf-8")).hexdigest()

    @staticmethod
    def _refresh_digest(token: str) -> str:
        """Persist only a verifier for bearer-like refresh credentials."""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _decode_client(client_id: str, data_json: str) -> OAuthClient | None:
        """Read current records and safely migrate pre-hardening registrations."""
        data = json.loads(data_json)
        data["client_id"] = client_id
        if "scopes" not in data:
            data["scopes"] = tuple(str(data.pop("scope", "read write")).split())
        data.setdefault("token_endpoint_auth_method", "none")
        data.pop("client_secret", None)
        try:
            return OAuthClient.model_validate(data)
        except ValidationError:
            logger.warning("Rejected invalid persisted OAuth client", client_id=client_id)
            return None

    @with_session()
    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def get_client(self, session: AsyncSession, client_id: str) -> OAuthClient | None:
        query, _ = OAuthQueries.get_client()
        result = await session.run(query, client_id=client_id)
        record = await result.single()
        return self._decode_client(record["client_id"], record["data_json"]) if record else None

    @with_session()
    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def save_client(self, session: AsyncSession, client: OAuthClient) -> None:
        query, _ = OAuthQueries.save_client()
        await session.run(
            query,
            client_id=client.client_id,
            data_json=client.model_dump_json(),
            now=datetime.now(UTC).timestamp(),
        )
        logger.info("Persisted OAuth client registration", client_id=client.client_id)

    @with_session()
    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def save_auth_code(
        self,
        session: AsyncSession,
        code: str,
        data: AuthorizationCode,
        ttl_seconds: int,
    ) -> None:
        now = datetime.now(UTC).timestamp()
        query, _ = OAuthQueries.save_auth_code()
        await session.run(
            query,
            code=self._code_digest(code),
            client_id=data.client_id,
            data_json=data.model_dump_json(),
            expires_at=now + ttl_seconds,
            now=now,
        )

    @with_session()
    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def get_auth_code(self, session: AsyncSession, code: str) -> AuthorizationCode | None:
        query, _ = OAuthQueries.get_auth_code()
        result = await session.run(
            query,
            code=self._code_digest(code),
            now=datetime.now(UTC).timestamp(),
        )
        record = await result.single()
        return AuthorizationCode.model_validate_json(record["data_json"]) if record else None

    @with_session()
    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def consume_auth_code(self, session: AsyncSession, code: str) -> AuthorizationCode | None:
        query, _ = OAuthQueries.consume_auth_code()
        result = await session.run(
            query,
            code=self._code_digest(code),
            now=datetime.now(UTC).timestamp(),
        )
        record = await result.single()
        if record is None or not record["valid"]:
            return None
        return AuthorizationCode.model_validate_json(record["data_json"])

    @with_session()
    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def save_refresh_token(
        self,
        session: AsyncSession,
        token: str,
        data: RefreshTokenState,
        ttl_seconds: int,
    ) -> None:
        now = datetime.now(UTC).timestamp()
        query, _ = OAuthQueries.save_refresh_token()
        await session.run(
            query,
            token=self._refresh_digest(token),
            client_id=data.client_id,
            family_id=data.family_id,
            data_json=data.model_dump_json(),
            expires_at=now + ttl_seconds,
            now=now,
        )

    @with_session()
    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def rotate_refresh_token(
        self,
        session: AsyncSession,
        presented_token: str,
        replacement_token: str,
        data: RefreshTokenState,
        ttl_seconds: int,
    ) -> bool:
        now = datetime.now(UTC).timestamp()
        query, _ = OAuthQueries.rotate_refresh_token()
        result = await session.run(
            query,
            presented_token=self._refresh_digest(presented_token),
            replacement_token=self._refresh_digest(replacement_token),
            client_id=data.client_id,
            family_id=data.family_id,
            data_json=data.model_dump_json(),
            expires_at=now + ttl_seconds,
            now=now,
        )
        record = await result.single()
        return bool(record and record["rotated"])
