#!/usr/bin/env python
"""Test the embedding service with circuit breaker."""

import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from memory_palace.core.logging import get_logger, setup_logging
from memory_palace.infrastructure.embeddings.voyage import VoyageEmbeddingService

setup_logging()
logger = get_logger(__name__)


async def test_embedding_service():
    """Test that the embedding service works with circuit breaker."""
    
    try:
        # Initialize service
        logger.info("Initializing VoyageEmbeddingService...")
        service = VoyageEmbeddingService()
        
        # Test single embedding
        logger.info("Testing single embedding...")
        text = "This is a test of the embedding service with circuit breaker pattern."
        embedding = await service.embed_text(text)
        logger.info(f"✅ Single embedding successful! Dimensions: {len(embedding)}")
        
        # Test batch embedding
        logger.info("Testing batch embedding...")
        texts = [
            "First test text",
            "Second test text", 
            "Third test text with more content to embed",
        ]
        embeddings = await service.embed_batch(texts)
        logger.info(f"✅ Batch embedding successful! Got {len(embeddings)} embeddings")
        for i, emb in enumerate(embeddings):
            logger.info(f"  - Text {i+1}: {len(emb)} dimensions")
        
        # Test circuit breaker state
        circuit_state = service._circuit_breaker.get_state()
        logger.info(f"Circuit breaker state: {circuit_state}")
        
        logger.info("\n🎉 All tests passed! The embedding service with circuit breaker works correctly.")
        
    except Exception as e:
        logger.error(f"❌ Test failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(test_embedding_service())