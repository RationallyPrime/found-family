"""Simple Neo4j driver wrapper for Memory Palace."""
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncSession

from memory_palace.core.config import settings
from memory_palace.core.logging import get_logger

logger = get_logger(__name__)


class Neo4jDriver:
    """Simple async Neo4j driver wrapper."""
    
    def __init__(self, uri: str, username: str, password: str):
        """Initialize the driver."""
        self.uri = uri
        self.username = username
        self.password = password
        self._driver: AsyncDriver | None = None
        
    async def connect(self):
        """Connect to Neo4j."""
        if self._driver is None:
            self._driver = AsyncGraphDatabase.driver(
                self.uri,
                auth=(self.username, self.password),
            )
            await self._driver.verify_connectivity()
            logger.info("Connected to Neo4j")
            
    async def close(self):
        """Close the connection."""
        if self._driver:
            await self._driver.close()
            self._driver = None
            logger.info("Closed Neo4j connection")
            
    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get a Neo4j session."""
        if not self._driver:
            await self.connect()
            
        async with self._driver.session() as session:
            yield session
            
    async def run(self, query: str, params: dict[str, Any] | None = None) -> list[dict]:
        """Run a query and return results."""
        async with self.session() as session:
            result = await session.run(query, params or {})
            return [dict(record) async for record in result]


# Global driver instance
_driver: Neo4jDriver | None = None


def get_driver(uri: str | None = None, username: str | None = None, password: str | None = None) -> Neo4jDriver:
    """Get or create the global driver instance."""
    global _driver
    
    if _driver is None:
        _driver = Neo4jDriver(
            uri=uri or settings.neo4j_uri,
            username=username or settings.neo4j_user,
            password=password or settings.neo4j_password,
        )
    
    return _driver