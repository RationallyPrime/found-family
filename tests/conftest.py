"""Shared fixtures for Memory Palace tests."""

import pytest_asyncio
from neo4j import AsyncGraphDatabase

from memory_palace.core.config import settings


@pytest_asyncio.fixture
async def neo4j_session():
    """Session against the dev Neo4j; cleans up :TestMemory nodes after.

    Integration tests must add the :TestMemory label to every node they
    create so teardown can remove them without touching real memories.
    """
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    async with driver.session() as session:
        yield session
        await session.run("MATCH (m:TestMemory) DETACH DELETE m")
    await driver.close()
