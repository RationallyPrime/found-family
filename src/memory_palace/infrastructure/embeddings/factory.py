"""Dependency injection for embedding services.

This module provides dependency injection configuration for embedding services,
ensuring consistent initialization and proper lifecycle management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from memory_palace.core.base import ServiceErrorDetails
from memory_palace.core.config import settings
from memory_palace.core.decorators import with_error_handling
from memory_palace.core.errors import ServiceError
from memory_palace.core.logging import get_logger
from memory_palace.infrastructure.embeddings.cache import EmbeddingCache
from memory_palace.infrastructure.embeddings.voyage import VoyageEmbeddingService

if TYPE_CHECKING:
    from neo4j import AsyncDriver

logger = get_logger(__name__)


class EmbeddingServiceProvider(Protocol):
    """Protocol for embedding service providers."""

    async def embed_text(self, text: str) -> list[float]:
        """Generate embedding for text."""
        ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        ...

    def get_model_dimensions(self) -> int:
        """Get the dimensions of the embedding model."""
        ...


class EmbeddingServiceBuilder:
    """Builder for creating properly configured embedding service instances.

    This follows the dependency injection pattern, allowing services to be
    configured and injected rather than using singletons.
    """

    def __init__(self, neo4j_driver: AsyncDriver | None = None):
        """Initialize the builder with optional Neo4j driver for caching.

        Args:
            neo4j_driver: Optional Neo4j driver for embedding cache
        """
        self.neo4j_driver = neo4j_driver
        self._cache: EmbeddingCache | None = None
        self._use_cache = True
        self._api_key: str | None = None
        self._model: str | None = None

    def with_cache(self, enabled: bool = True) -> EmbeddingServiceBuilder:
        """Configure whether to use embedding cache.

        Args:
            enabled: Whether to enable caching

        Returns:
            Self for method chaining
        """
        self._use_cache = enabled
        return self

    def with_api_key(self, api_key: str) -> EmbeddingServiceBuilder:
        """Set the API key for the embedding service.

        Args:
            api_key: API key for the service

        Returns:
            Self for method chaining
        """
        self._api_key = api_key
        return self

    def with_model(self, model: str) -> EmbeddingServiceBuilder:
        """Set the embedding model to use.

        Args:
            model: Model identifier

        Returns:
            Self for method chaining
        """
        self._model = model
        return self

    @with_error_handling(reraise=True)
    def build(self) -> EmbeddingServiceProvider:
        """Build the configured embedding service.

        Returns:
            Configured embedding service instance

        Raises:
            ServiceError: If required configuration is missing
        """
        # Use provided API key or fall back to settings
        api_key = self._api_key or settings.voyage_api_key
        if not api_key:
            raise ServiceError(
                message="VOYAGE_API_KEY not configured",
                details=ServiceErrorDetails(
                    source="embedding_builder",
                    operation="build",
                    service_name="voyage",
                    endpoint="/embeddings",
                    status_code=0,
                ),
            )
            # Note: ServiceError will use CONFIG_MISSING error code internally

        # Create cache if requested and driver available
        cache = None
        if self._use_cache:
            if self.neo4j_driver is None:
                logger.warning("Neo4j driver not provided, embedding cache disabled")
            else:
                logger.info("Initializing embedding cache with Neo4j backend")
                cache = EmbeddingCache(self.neo4j_driver)
                self._cache = cache

        # Create embedding service with optional model override
        logger.info(f"Creating VoyageEmbeddingService instance{f' with model {self._model}' if self._model else ''}")
        service = VoyageEmbeddingService(cache=cache)

        # Override model if specified
        if self._model:
            service.model = self._model

        # Validate service is working
        self._validate_service(service)

        return service

    def _validate_service(self, service: EmbeddingServiceProvider) -> None:
        """Validate that the service is properly configured.

        Args:
            service: Service to validate

        Raises:
            ServiceError: If service validation fails
        """
        dimensions = service.get_model_dimensions()
        if dimensions <= 0:
            raise ServiceError(
                message=f"Invalid embedding dimensions: {dimensions}",
                details=ServiceErrorDetails(
                    source="embedding_builder",
                    operation="validate",
                    service_name="voyage",
                    endpoint="/embeddings",
                    status_code=0,
                ),
            )

        logger.info(f"✅ Embedding service validated: dimensions={dimensions}")


def create_embedding_service(
    neo4j_driver: AsyncDriver | None = None,
    use_cache: bool = True,
    api_key: str | None = None,
    model: str | None = None,
) -> EmbeddingServiceProvider:
    """Convenience function to create an embedding service with dependency injection.

    Args:
        neo4j_driver: Optional Neo4j driver for caching
        use_cache: Whether to enable caching (default: True)
        api_key: Optional API key override
        model: Optional model override

    Returns:
        Configured embedding service

    Example:
        ```python
        # In startup
        embedding_service = create_embedding_service(neo4j_driver=driver, use_cache=True)

        # Inject into services
        memory_service = MemoryService(session=session, embeddings=embedding_service)
        ```
    """
    builder = EmbeddingServiceBuilder(neo4j_driver)
    builder.with_cache(use_cache)

    if api_key:
        builder.with_api_key(api_key)

    if model:
        builder.with_model(model)

    return builder.build()


def validate_embedding_service(service: EmbeddingServiceProvider) -> None:
    """Validate an embedding service instance.

    Args:
        service: Service to validate

    Raises:
        ServiceError: If validation fails
    """
    if service is None:
        raise ServiceError(
            message="Embedding service is None",
            details=ServiceErrorDetails(
                source="embedding_validation",
                operation="validate",
                service_name="voyage",
                endpoint="/embeddings",
                status_code=0,
            ),
        )

    dimensions = service.get_model_dimensions()
    if dimensions <= 0:
        raise ServiceError(
            message=f"Invalid embedding dimensions: {dimensions}",
            details=ServiceErrorDetails(
                source="embedding_validation",
                operation="validate",
                service_name=type(service).__name__,
                endpoint="get_model_dimensions",
                status_code=0,
            ),
        )

    logger.debug(f"Embedding service validation passed: {dimensions} dimensions")
