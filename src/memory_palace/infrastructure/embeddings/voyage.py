"""Voyage AI embedding service."""

import os
from typing import Any, cast

import voyageai
from pydantic import ConfigDict, Field

from memory_palace.core.base import ErrorLevel, ServiceErrorDetails
from memory_palace.core.circuit_breaker import CircuitBreaker, RetryWithCircuitBreaker
from memory_palace.core.config import settings
from memory_palace.core.decorators import with_error_handling
from memory_palace.core.errors import (
    AuthenticationError,
    ProcessingError,
    RateLimitError,
    ServiceError,
    TimeoutError,
)
from memory_palace.core.logging import get_logger
from memory_palace.domain.models import EmbeddingType
from memory_palace.infrastructure.embeddings.cache import EmbeddingCache

# Settings imported at the module level
logger = get_logger(__name__)

# TODO: Add pattern matcher when patterns module is implemented
# Initialize error pattern matcher
# pattern_matcher = PatternMatcher()

# Register embedding-specific error patterns
# pattern_matcher.register_pattern(
#     "model_load",
#     ErrorPattern(
#         error_type="EmbeddingError",
#         message_pattern=r"model.*not found|failed to load model",
#         code=ErrorCode.MODEL_INITIALIZATION_ERROR,
#         level=ErrorLevel.ERROR,
#         suggested_solution="Verify model availability and credentials",
#     ),
# )

# pattern_matcher.register_pattern(
#     "rate_limit",
#     ErrorPattern(
#         error_type="RateLimitError",
#         message_pattern=r"rate limit|too many requests|quota exceeded",
#         code=ErrorCode.RATE_LIMITED,
#         level=ErrorLevel.WARNING,
#         suggested_solution="Implement backoff strategy or reduce request frequency",
#     ),
# )


class VoyageEmbeddingService:
    """Voyage AI embedding service implementation.

    This service supports different types of embeddings through configuration:
    - Text embeddings (default): Uses voyage-code model
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Model configuration
    model: str = Field(default="")  # Will be set in __init__ from settings
    default_embedding_type: EmbeddingType = Field(default=EmbeddingType.TEXT)
    # voyageai client doesn't expose a public type, so we use Any here
    client: Any  # voyageai.AsyncClient
    cache: EmbeddingCache | None = None
    
    # Circuit breaker for API calls
    _circuit_breaker: CircuitBreaker
    _retry_handler: RetryWithCircuitBreaker

    @with_error_handling(error_level=ErrorLevel.ERROR)
    def __init__(
        self,
        model: str | None = None,
        default_embedding_type: EmbeddingType = EmbeddingType.TEXT,
        cache: EmbeddingCache | None = None,
    ) -> None:
        """Initialize the Voyage embedding service.

        Args:
            model: Optional model override (defaults to ai.voyage.code_model from settings)
            default_embedding_type: Default type of embedding to generate

        Raises:
            AuthenticationError: If the API key is not configured
        """
        # Settings imported at the module level

        # Handle both SecretStr and plain string for API key
        api_key = settings.voyage_api_key
        if hasattr(api_key, 'get_secret_value'):
            api_key = str(api_key.get_secret_value())  # type: ignore
        else:
            api_key = str(api_key) if api_key else ""

        if not api_key:
            details = ServiceErrorDetails(
                source="VoyageEmbeddingService",
                operation="initialization",
                service_name="Voyage AI",
                endpoint=None,
                status_code=None,
                request_id=None,
                latency_ms=None,
            )
            raise AuthenticationError(
                message="Voyage API key not found in settings",
                details=details,
            )

        # Use the model from settings or the one provided, or default to voyage-3-large
        self.model = model or getattr(settings, 'voyage_code_model', 'voyage-3-large')
        self.default_embedding_type = default_embedding_type

        # Set the environment variable for voyageai to pick up
        os.environ["VOYAGE_API_KEY"] = api_key

        # Initialize the client which will use the environment variable
        self.client = voyageai.AsyncClient()  # type: ignore
        self.cache = cache
        
        # Initialize circuit breaker for API calls
        self._circuit_breaker = CircuitBreaker(
            name="voyage_api",
            failure_threshold=3,
            recovery_timeout=30.0,
            expected_exception_types=(RateLimitError, TimeoutError, ServiceError),
            success_threshold=2,
        )
        
        # Initialize retry handler with circuit breaker
        self._retry_handler = RetryWithCircuitBreaker(
            circuit_breaker=self._circuit_breaker,
            max_retries=3,
            initial_delay=1.0,
            backoff_factor=2.0,
            max_delay=30.0,
            retryable_exceptions=(RateLimitError, TimeoutError),
        )

    async def _generate_embedding(self, text: str) -> list[float]:
        """Generate a fresh embedding via the Voyage API."""
        if not text.strip():
            raise ProcessingError(
                message="Cannot embed empty text",
                details={"text_length": len(text)},
            )

        batch_texts = [text]
        response = await self.client.embed(texts=batch_texts, model=self.model)

        embeddings = getattr(response, "embeddings", [])
        if not embeddings:
            raise ProcessingError(
                message="Failed to generate embedding for text",
                details={"text_length": len(text)},
            )

        return cast("list[float]", embeddings[0])

    @with_error_handling(error_level=ErrorLevel.ERROR)
    async def embed_text(self, text: str) -> list[float]:
        """Generate an embedding vector for the provided text with caching."""
        if self.cache:
            # Pass model name for cache key to prevent cross-model contamination
            cached = await self.cache.get_cached(text, self.model)
            if cached:
                logger.debug(f"Embedding cache hit for text: {text[:50]}... (model: {self.model})")
                return cached

        embedding = await self._generate_embedding(text)

        if self.cache:
            # Store with model and dimension metadata
            dimensions = self.get_model_dimensions()
            await self.cache.store(text, self.model, embedding, dimensions)

        return embedding

    async def _call_voyage_api_internal(self, texts: list[str]) -> list[list[float]]:
        """
        Internal method to call Voyage API.
        
        This is wrapped by the circuit breaker.
        """
        response = await self.client.embed(texts=texts, model=self.model)
        
        embeddings = getattr(response, "embeddings", [])
        if not embeddings or len(embeddings) != len(texts):
            # This is a processing error, not retryable
            raise ProcessingError(
                message="Voyage API returned incomplete embeddings",
                details=ServiceErrorDetails(
                    source="voyage_embedding",
                    operation="embed_batch",
                    service_name="voyage",
                    endpoint="/embeddings",
                    status_code=200,  # API returned 200 but bad data
                    request_id=None,
                    latency_ms=None,
                ),
            )
        
        # Success! Return the embeddings
        return [cast("list[float]", emb) for emb in embeddings]

    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embedding vectors for a batch of texts with circuit breaker and retry logic.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors corresponding to the input texts

        Raises:
            ProcessingError: If there was an error generating the embeddings
            ServiceError: If the circuit is open or service fails
        """
        if not texts:
            return []

        # Filter out empty strings
        valid_texts = [text for text in texts if text.strip()]
        if not valid_texts:
            raise ProcessingError(
                message="Batch contains only empty texts",
                details={
                    "source": "voyage_embedding",
                    "operation": "embed_batch",
                    "original_batch_size": len(texts),
                },
            )

        # Use circuit breaker with retries
        return await self._retry_handler.call_async(
            self._call_voyage_api_internal,
            valid_texts,
        )

    def _handle_error(
        self,
        e: Exception,
        batch_index: int,
        texts: list[str],
        model: str,
    ) -> ProcessingError | RateLimitError | TimeoutError | AuthenticationError:
        """Map errors to our exception types."""
        error_msg = str(e).lower()
        # Create proper ServiceErrorDetails for different error types
        if "rate limit" in error_msg:
            details = ServiceErrorDetails(
                source="VoyageEmbeddingService",
                operation="embed_batch",
                service_name="Voyage AI",
                endpoint="/embeddings",
                status_code=429,  # Rate limit status code
                request_id=None,
                latency_ms=None,
            )
            return RateLimitError(
                message="Rate limit exceeded for embeddings API",
                details=details,
            )
        if "timeout" in error_msg or "connection" in error_msg:
            details = ServiceErrorDetails(
                source="VoyageEmbeddingService",
                operation="embed_batch",
                service_name="Voyage AI",
                endpoint="/embeddings",
                status_code=408,  # Timeout status code
                request_id=None,
                latency_ms=None,
            )
            return TimeoutError(
                message="Embeddings API request timed out",
                details=details,
            )
        if "auth" in error_msg or "api key" in error_msg:
            details = ServiceErrorDetails(
                source="VoyageEmbeddingService",
                operation="embed_batch",
                service_name="Voyage AI",
                endpoint="/embeddings",
                status_code=401,  # Unauthorized status code
                request_id=None,
                latency_ms=None,
            )
            return AuthenticationError(
                message="Authentication failed for embeddings API",
                details=details,
            )

        # Default to ProcessingError with dict details
        error_context: dict[str, Any] = {
            "batch_index": batch_index,
            "batch_size": len(texts),
            "model": model,
            "original_error": str(e),
        }
        return ProcessingError(
            message=f"Failed to generate embeddings: {e!s}",
            details=error_context,
        )

    async def compute_similarity(
        self,
        vector_a: list[float],
        vector_b: list[float],
    ) -> float:
        """
        Compute the cosine similarity between two embedding vectors.

        Args:
            vector_a: First embedding vector
            vector_b: Second embedding vector

        Returns:
            Similarity score between 0 and 1

        Raises:
            ProcessingError: If similarity computation fails
        """
        if not vector_a or not vector_b:
            raise ProcessingError(
                message="Cannot compute similarity for empty vectors",
                details={
                    "vector_a_length": len(vector_a),
                    "vector_b_length": len(vector_b),
                },
            )

        # Use numpy for fast cosine similarity
        import numpy as np

        a = np.array(vector_a)
        b = np.array(vector_b)

        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return float(np.dot(a, b) / (norm_a * norm_b))

    def get_model_dimensions(self) -> int:
        """
        Get the dimensionality of the voyage embedding model.

        Returns:
            Number of dimensions in the embedding vectors
        """
        # Voyage model dimensions
        # This should be obtained from a config/settings
        MODEL_DIMENSIONS = {
            "voyage-01": 1024,
            "voyage-02": 1536,
            "voyage-large-2": 1536,
            "voyage-code-2": 1536,
            "voyage-3-large": 1024,  # New model with 1024 dimensions
            "voyage-3": 1024,
        }

        return MODEL_DIMENSIONS.get(self.model, 1024)  # Default to 1024

    async def close(self) -> None:
        """Close the client connection."""
        # Implement if needed for cleanup
