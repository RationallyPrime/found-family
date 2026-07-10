"""Tests for model-scoped embedding cache identity."""

import hashlib
from dataclasses import dataclass, field
from types import TracebackType
from typing import cast

from neo4j import AsyncDriver

from memory_palace.infrastructure.embeddings.cache import EmbeddingCache


class _EmptyResult:
    async def single(self) -> None:
        return None


@dataclass
class _RecordingSession:
    parameters: list[dict[str, object]] = field(default_factory=list)

    async def run(self, _query: str, **parameters: object) -> _EmptyResult:
        self.parameters.append(parameters)
        return _EmptyResult()


class _SessionContext:
    def __init__(self, session: _RecordingSession) -> None:
        self._session = session

    async def __aenter__(self) -> _RecordingSession:
        return self._session

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None


class _RecordingDriver:
    def __init__(self) -> None:
        self.session_instance = _RecordingSession()

    def session(self) -> _SessionContext:
        return _SessionContext(self.session_instance)


async def test_cache_uses_model_scoped_sha256_key() -> None:
    driver = _RecordingDriver()
    cache = EmbeddingCache(cast(AsyncDriver, driver))

    assert await cache.get_cached("remember this", "voyage-4-large") is None

    expected = hashlib.sha256(b"voyage-4-large::remember this").hexdigest()
    assert driver.session_instance.parameters == [{"key": expected, "model": "voyage-4-large"}]
    assert len(expected) == 64
