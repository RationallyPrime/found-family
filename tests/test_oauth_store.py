"""Integration tests for the Neo4j OAuth state store.

Requires the dev Neo4j. Creates uniquely-named nodes and removes them.
"""

import secrets
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase

from memory_palace.core.config import settings
from memory_palace.infrastructure.oauth import (
    AuthorizationCode,
    Neo4jOAuthStateStore,
    OAuthClient,
    OAuthStateStore,
    RefreshTokenState,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def store() -> AsyncIterator[Neo4jOAuthStateStore]:
    driver = AsyncGraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password_value))
    yield Neo4jOAuthStateStore(driver)
    async with driver.session() as session:
        await session.run(
            "MATCH (n) WHERE (n:OAuthClient OR n:OAuthCode OR n:OAuthRefreshToken) "
            "AND (n.client_id STARTS WITH 'test_' OR n.code STARTS WITH 'test_') DETACH DELETE n"
        )
    await driver.close()


def test_neo4j_store_satisfies_protocol() -> None:
    assert isinstance(Neo4jOAuthStateStore(None), OAuthStateStore)


async def test_client_registration_round_trip(store: Neo4jOAuthStateStore) -> None:
    client_id = f"test_{secrets.token_urlsafe(8)}"
    client = OAuthClient(
        client_id=client_id,
        client_name="test",
        redirect_uris=("https://claude.ai/api/mcp/auth_callback",),
    )

    assert await store.get_client(client_id) is None
    await store.save_client(client)
    assert await store.get_client(client_id) == client

    # Re-registration updates rather than duplicates
    renamed = client.model_copy(update={"client_name": "renamed"})
    await store.save_client(renamed)
    stored = await store.get_client(client_id)
    assert stored is not None and stored.client_name == "renamed"


async def test_auth_code_is_single_use(store: Neo4jOAuthStateStore) -> None:
    code = f"test_{secrets.token_urlsafe(8)}"
    data = AuthorizationCode(
        client_id="test_client",
        redirect_uri="https://claude.ai/api/mcp/auth_callback",
        scopes=("read", "write"),
        code_challenge="x" * 43,
    )
    await store.save_auth_code(code, data, ttl_seconds=600)

    assert await store.get_auth_code(code) == data

    first = await store.consume_auth_code(code)
    assert first == data

    # Replay must fail
    assert await store.consume_auth_code(code) is None


async def test_expired_auth_code_rejected(store: Neo4jOAuthStateStore) -> None:
    code = f"test_{secrets.token_urlsafe(8)}"
    data = AuthorizationCode(
        client_id="test_client",
        redirect_uri="https://claude.ai/api/mcp/auth_callback",
        scopes=("read",),
        code_challenge="x" * 43,
    )
    await store.save_auth_code(code, data, ttl_seconds=-1)  # already expired

    assert await store.consume_auth_code(code) is None


async def test_unknown_code_rejected(store: Neo4jOAuthStateStore) -> None:
    assert await store.consume_auth_code(f"test_{secrets.token_urlsafe(8)}") is None


async def test_refresh_token_is_hashed_and_single_use(store: Neo4jOAuthStateStore) -> None:
    token = f"test_{secrets.token_urlsafe(24)}"
    replacement = f"test_{secrets.token_urlsafe(24)}"
    state = RefreshTokenState(client_id="test_client", scopes=("read", "write"), family_id="test_family_123456")
    await store.save_refresh_token(token, state, ttl_seconds=600)

    assert await store.rotate_refresh_token(token, replacement, state, ttl_seconds=600) is True
    assert await store.rotate_refresh_token(token, "unused", state, ttl_seconds=600) is False
    assert await store.rotate_refresh_token(replacement, "unused-again", state, ttl_seconds=600) is False


async def test_independent_refresh_families_do_not_invalidate_each_other(store: Neo4jOAuthStateStore) -> None:
    first_token = f"test_{secrets.token_urlsafe(24)}"
    second_token = f"test_{secrets.token_urlsafe(24)}"
    first = RefreshTokenState(client_id="test_client", scopes=("read",), family_id="test_family_first")
    second = RefreshTokenState(client_id="test_client", scopes=("read",), family_id="test_family_second")
    await store.save_refresh_token(first_token, first, ttl_seconds=600)
    await store.save_refresh_token(second_token, second, ttl_seconds=600)

    assert await store.rotate_refresh_token(first_token, f"test_{secrets.token_urlsafe(24)}", first, 600)
    assert await store.rotate_refresh_token(second_token, f"test_{secrets.token_urlsafe(24)}", second, 600)
