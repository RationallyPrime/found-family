"""Shared fixtures for Memory Palace tests."""

import os
from collections.abc import AsyncIterator

# Application modules deliberately refuse to mint tokens without a stable key.
# Tests use an isolated, deterministic key and never depend on a developer .env.
os.environ.setdefault("JWT_SECRET_KEY", "test-only-jwt-secret-key-with-at-least-32-bytes")
os.environ.setdefault("LOGFIRE_IGNORE_NO_CONFIG", "1")

import pytest_asyncio
from neo4j import AsyncGraphDatabase, AsyncSession

from memory_palace.core.config import settings


@pytest_asyncio.fixture
async def neo4j_session() -> AsyncIterator[AsyncSession]:
    """Session against the dev Neo4j; cleans up :TestMemory nodes after.

    Integration tests must add the :TestMemory label to every node they
    create so teardown can remove them without touching real memories.
    """
    driver = AsyncGraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password_value))
    async with driver.session() as session:
        yield session
        await session.run("MATCH (m:TestMemory) DETACH DELETE m")
    await driver.close()
