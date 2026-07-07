"""Integration tests for the Neo4j OAuth state store.

Requires the dev Neo4j. Creates uniquely-named nodes and removes them.
"""

import secrets

import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase

from memory_palace.core.config import settings
from memory_palace.infrastructure.oauth import Neo4jOAuthStateStore, OAuthStateStore

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def store():
    driver = AsyncGraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
    yield Neo4jOAuthStateStore(driver)
    async with driver.session() as session:
        await session.run(
            "MATCH (n) WHERE (n:OAuthClient OR n:OAuthCode) "
            "AND (n.client_id STARTS WITH 'test_' OR n.code STARTS WITH 'test_') DETACH DELETE n"
        )
    await driver.close()


def test_neo4j_store_satisfies_protocol():
    assert isinstance(Neo4jOAuthStateStore(None), OAuthStateStore)


async def test_client_registration_round_trip(store):
    client_id = f"test_{secrets.token_urlsafe(8)}"
    data = {"client_name": "test", "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]}

    assert await store.get_client(client_id) is None
    await store.save_client(client_id, data)
    assert await store.get_client(client_id) == data

    # Re-registration updates rather than duplicates
    data2 = {**data, "client_name": "renamed"}
    await store.save_client(client_id, data2)
    assert (await store.get_client(client_id))["client_name"] == "renamed"


async def test_auth_code_is_single_use(store):
    code = f"test_{secrets.token_urlsafe(8)}"
    await store.save_auth_code(code, {"client_id": "test_c", "code_challenge": "x"}, ttl_seconds=600)

    first = await store.consume_auth_code(code)
    assert first is not None and first["client_id"] == "test_c"

    # Replay must fail
    assert await store.consume_auth_code(code) is None


async def test_expired_auth_code_rejected(store):
    code = f"test_{secrets.token_urlsafe(8)}"
    await store.save_auth_code(code, {"client_id": "test_c"}, ttl_seconds=-1)  # already expired

    assert await store.consume_auth_code(code) is None


async def test_unknown_code_rejected(store):
    assert await store.consume_auth_code(f"test_{secrets.token_urlsafe(8)}") is None
