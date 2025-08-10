"""Voyage AI embedding service."""

import asyncio
import os
from typing import Any, cast

import voyageai
from pydantic import ConfigDict, Field

from memory_palace.core.base import ErrorLevel, ServiceErrorDetails
from memory_palace.core.config import settings
from memory_palace.core.decorators import with_error_handling
from memory_palace.core.errors import (
    AuthenticationError,
    ProcessingError,
    RateLimitError,
    TimeoutError,
)
from memory_palace.core.logging import get_logger
from memory_palace.domain.models import EmbeddingType

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
    # voyageai client doesn't expose a public type, but we can type it as voyageai.Client
    # which is what we're actually using. This avoids the Any type.
    client: voyageai.AsyncClient

    @with_error_handling(error_level=ErrorLevel.ERROR)
    def __init__(
        self,
        model: str | None = None,
        default_embedding_type: EmbeddingType = EmbeddingType.TEXT,
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
        if hasattr(api_key, 'get_secret_value') and callable(api_key.get_secret_value):
            api_key = api_key.get_secret_value()  # type: ignore

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
        self.client = voyageai.AsyncClient()

    @with_error_handling(error_level=ErrorLevel.ERROR)
    async def embed_text(self, text: str) -> list[float]:
        """
        Generate an embedding vector for the provided text.

        Args:
            text: The text to embed

        Returns:
            A list of floating point values representing the embedding vector

        Raises:
            EmbeddingError: If there was an error generating the embedding
        """
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
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embedding vectors for a batch of texts.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors corresponding to the input texts

        Raises:
            EmbeddingError: If there was an error generating the embeddings
        """
        if not texts:
            return []

        # Filter out empty strings
        valid_texts = [text for text in texts if text.strip()]
        if not valid_texts:
            raise ProcessingError(
                message="Batch contains only empty texts",
                details={"original_batch_size": len(texts)},
            )

        # Handle retries and rate limits
        max_retries = 3
        backoff_factor = 1.5
        delay = 1.0

        for attempt in range(1, max_retries + 1):
            try:
                response = await self.client.embed(texts=valid_texts, model=self.model)

                embeddings = getattr(response, "embeddings", [])
                if not embeddings or len(embeddings) != len(valid_texts):
                    raise ProcessingError(
                        message="Failed to generate embeddings for batch",
                        details={"batch_size": len(valid_texts)},
                    )

                # Build the result with proper type casting
                return [cast("list[float]", emb) for emb in embeddings]

            except Exception as e:
                error_msg = str(e).lower()
                is_retryable = any(
                    msg in error_msg for msg in ["rate limit", "timeout", "connection", "try again"]
                )

                if is_retryable and attempt < max_retries:
                    logger.warning(
                        "Retryable error on attempt %d/%d. Backing off for %.1f seconds: %s",
                        attempt,
                        max_retries,
                        delay,
                        str(e),
                    )
                    await asyncio.sleep(delay)
                    delay *= backoff_factor
                    continue

                # If we're here, either it's not retryable or we're out of retries
                raise self._handle_error(e, 0, valid_texts, self.model) from e

        # Should never reach here due to the raise above
        raise ProcessingError(
            message="Failed to generate embeddings after retries",
            details={"batch_size": len(valid_texts)},
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
