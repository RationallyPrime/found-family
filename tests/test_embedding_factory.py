"""Embedding dependency injection honors its public construction contract."""

import pytest
from pydantic import SecretStr
from voyageai.error import Timeout as VoyageTimeout

from memory_palace.core.config import settings
from memory_palace.core.errors import TimeoutError
from memory_palace.infrastructure.embeddings.factory import create_embedding_service
from memory_palace.infrastructure.embeddings.voyage import VoyageEmbeddingService


def test_explicit_api_key_and_model_override_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voyage_api_key", SecretStr(""))

    service = create_embedding_service(
        use_cache=False,
        api_key="injected-provider-key",
        model="voyage-4-lite",
    )

    assert isinstance(service, VoyageEmbeddingService)
    assert service.model == "voyage-4-lite"
    assert service.get_model_dimensions() == 1024
    assert service.client.api_key == "injected-provider-key"
    assert service.client._params["request_timeout"] == settings.voyage_timeout_seconds


async def test_sdk_timeout_maps_into_retryable_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    service = VoyageEmbeddingService(api_key="injected-provider-key", model="voyage-4-lite")

    class TimeoutClient:
        async def embed(self, **_kwargs: object) -> None:
            raise VoyageTimeout("provider timed out")

    monkeypatch.setattr(service, "client", TimeoutClient())

    with pytest.raises(TimeoutError):
        await service._call_voyage_api_internal(["memory"])
