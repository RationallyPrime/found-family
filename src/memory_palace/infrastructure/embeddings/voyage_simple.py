"""Simple Voyage AI embedding service."""
import os
from typing import Any

import voyageai

from memory_palace.core.config import settings
from memory_palace.core.logging import get_logger
from memory_palace.domain.models import EmbeddingType


logger = get_logger(__name__)


class VoyageEmbeddingService:
    """Simple Voyage AI embedding service."""
    
    def __init__(self, api_key: str | None = None):
        """Initialize the Voyage client."""
        self.api_key = api_key or settings.voyage_api_key or os.getenv("VOYAGE_API_KEY")
        if not self.api_key:
            raise ValueError("Voyage API key not provided")
        
        self.client = voyageai.AsyncClient(api_key=self.api_key)
        self.model = "voyage-3"  # Default model for memory embeddings
        
    async def embed(
        self,
        texts: list[str],
        embedding_type: EmbeddingType = EmbeddingType.DOCUMENT,
    ) -> list[list[float]]:
        """Generate embeddings for the given texts."""
        if not texts:
            return []
            
        # Adjust model based on embedding type
        if embedding_type == EmbeddingType.QUERY:
            input_type = "query"
        else:
            input_type = "document"
            
        try:
            result = await self.client.embed(
                texts=texts,
                model=self.model,
                input_type=input_type,
            )
            return result.embeddings
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            raise
            
    async def embed_single(
        self,
        text: str,
        embedding_type: EmbeddingType = EmbeddingType.DOCUMENT,
    ) -> list[float]:
        """Generate embedding for a single text."""
        embeddings = await self.embed([text], embedding_type)
        return embeddings[0] if embeddings else []