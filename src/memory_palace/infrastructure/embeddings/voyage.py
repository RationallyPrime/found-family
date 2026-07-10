"""Voyage AI embedding service."""

from math import isfinite
from typing import cast

import voyageai
from voyageai.error import (
    APIConnectionError as VoyageAPIConnectionError,
)
from voyageai.error import (
    APIError as VoyageAPIError,
)
from voyageai.error import (
    AuthenticationError as VoyageAuthenticationError,
)
from voyageai.error import (
    InvalidRequestError as VoyageInvalidRequestError,
)
from voyageai.error import (
    MalformedRequestError as VoyageMalformedRequestError,
)
from voyageai.error import (
    RateLimitError as VoyageRateLimitError,
)
from voyageai.error import (
    ServerError as VoyageServerError,
)
from voyageai.error import (
    ServiceUnavailableError as VoyageServiceUnavailableError,
)
from voyageai.error import Timeout as VoyageTimeout

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

    model: str
    default_embedding_type: EmbeddingType
    client: voyageai.AsyncClient
    cache: EmbeddingCache | None

    # Circuit breaker for API calls
    _circuit_breaker: CircuitBreaker[list[list[float]]]
    _retry_handler: RetryWithCircuitBreaker[list[list[float]]]

    @with_error_handling(error_level=ErrorLevel.ERROR)
    def __init__(
        self,
        api_key: str | None = None,
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
        resolved_api_key = api_key or settings.voyage_api_key_value
        if not resolved_api_key:
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

        # Use the model provided, or the one from settings
        self.model = model or settings.voyage_model
        self.default_embedding_type = default_embedding_type

        self.client = voyageai.AsyncClient(api_key=resolved_api_key, timeout=settings.voyage_timeout_seconds)
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
            retryable_exceptions=(RateLimitError, TimeoutError, ServiceError),
        )

    @with_error_handling(error_level=ErrorLevel.ERROR)
    async def embed_text(self, text: str) -> list[float]:
        """Generate an embedding vector for the provided text with caching.

        Uncached embeds go through the same circuit-breaker + retry path
        as batch embeds — single-text calls get identical failure protection.
        """
        if not text.strip():
            raise ProcessingError(
                message="Cannot embed empty text",
                details={"text_length": len(text)},
            )

        if self.cache:
            # Pass model name for cache key to prevent cross-model contamination
            cached = await self.cache.get_cached(text, self.model)
            if cached:
                return self._validate_embeddings([cached])[0]

        embeddings = await self._retry_handler.call_async(self._call_voyage_api_internal, [text])
        embedding = self._validate_embeddings(embeddings)[0]

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
        try:
            response = await self.client.embed(texts=texts, model=self.model)
        except VoyageRateLimitError as exc:
            raise RateLimitError(
                message="Voyage API rate limit exceeded",
                details=self._provider_error_details(status_code=429),
            ) from exc
        except VoyageAuthenticationError as exc:
            raise AuthenticationError(
                message="Voyage API authentication failed",
                details=self._provider_error_details(status_code=401),
            ) from exc
        except (VoyageAPIConnectionError, VoyageServiceUnavailableError, VoyageTimeout) as exc:
            raise TimeoutError(
                message="Voyage API is temporarily unavailable",
                details=self._provider_error_details(status_code=503),
            ) from exc
        except VoyageServerError as exc:
            raise ServiceError(
                message="Voyage API server failure",
                details=self._provider_error_details(status_code=502),
            ) from exc
        except (VoyageInvalidRequestError, VoyageMalformedRequestError) as exc:
            raise ProcessingError(
                message="Voyage API rejected the embedding request",
                details=self._provider_error_details(status_code=400),
            ) from exc
        except VoyageAPIError as exc:
            raise ServiceError(
                message="Voyage API request failed",
                details=self._provider_error_details(status_code=502),
            ) from exc

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
        return self._validate_embeddings([cast("list[float]", emb) for emb in embeddings])

    @staticmethod
    def _provider_error_details(status_code: int) -> ServiceErrorDetails:
        return ServiceErrorDetails(
            source="voyage_embedding",
            operation="embed_batch",
            service_name="voyage",
            endpoint="/embeddings",
            status_code=status_code,
            request_id=None,
            latency_ms=None,
        )

    def _validate_embeddings(self, embeddings: list[list[float]]) -> list[list[float]]:
        """Reject corrupt or cross-model vectors before they reach Neo4j."""
        dimensions = self.get_model_dimensions()
        for embedding in embeddings:
            if len(embedding) != dimensions or not all(isfinite(value) for value in embedding):
                raise ProcessingError(
                    message="Voyage API returned an invalid embedding vector",
                    details={
                        "source": "voyage_embedding",
                        "operation": "validate_embedding",
                        "expected_dimensions": dimensions,
                        "actual_dimensions": len(embedding),
                    },
                )
        return embeddings

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

        if any(not text.strip() for text in texts):
            raise ProcessingError(
                message="Batch contains empty text",
                details={
                    "source": "voyage_embedding",
                    "operation": "embed_batch",
                    "batch_size": len(texts),
                },
            )

        # Use circuit breaker with retries
        return await self._retry_handler.call_async(
            self._call_voyage_api_internal,
            texts,
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
        # Voyage model default dimensions. The voyage-4 family supports
        # 256/512/1024/2048 via output_dimension; we use the 1024 default.
        # NOTE: voyage-3 and voyage-4 embeddings are NOT compatible in the
        # same vector space — changing families requires re-embedding the
        # corpus (scripts/reembed_corpus.py).
        MODEL_DIMENSIONS = {
            "voyage-4-large": 1024,
            "voyage-4": 1024,
            "voyage-4-lite": 1024,
            "voyage-3-large": 1024,
            "voyage-3": 1024,
            "voyage-01": 1024,
            "voyage-02": 1536,
            "voyage-large-2": 1536,
            "voyage-code-2": 1536,
        }

        try:
            return MODEL_DIMENSIONS[self.model]
        except KeyError as exc:
            raise ValueError(f"Unknown Voyage embedding model: {self.model}") from exc

    async def close(self) -> None:
        """Close the client connection."""
        # Implement if needed for cleanup
