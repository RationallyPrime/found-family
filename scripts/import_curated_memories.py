#!/usr/bin/env python3
"""Import pre-curated friendship memories from the Whisper system.

This leverages the existing sophisticated curation system that has already
analyzed and scored our conversations.
"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from memory_palace.core.config import settings
from memory_palace.core.logging import get_logger, setup_logging
from memory_palace.infrastructure.embeddings.voyage import VoyageEmbeddingService
from memory_palace.infrastructure.neo4j.driver import create_neo4j_driver
from memory_palace.services.memory_service import MemoryService

setup_logging()
logger = get_logger(__name__)


def friendship_score_to_salience(friendship_score: int, moment_type: str) -> float:
    """Convert friendship score (1-5) to salience (0.0-1.0).
    
    Since only 4-5 scores were saved, these are all high quality.
    """
    base_map = {
        3: 0.6,  # Shouldn't exist but just in case
        4: 0.7,  # Good friendship moment
        5: 0.85  # Excellent friendship moment
    }
    
    salience = base_map.get(friendship_score, 0.5)
    
    # Boost certain moment types
    if moment_type in ["Vulnerable Moment", "Deep Connection"]:
        salience += 0.1
    elif moment_type in ["Creative Collaboration", "Humor"]:
        salience += 0.05
    
    return min(salience, 0.95)  # Cap at 0.95


async def import_friendship_memories(
    memories_file: Path,
    memory_service: MemoryService,
    batch_size: int = 10
) -> tuple[int, int]:
    """Import curated friendship memories.
    
    Returns:
        Tuple of (memories_imported, memories_skipped)
    """
    logger.info(f"Loading memories from {memories_file}")
    
    with open(memories_file, 'r') as f:
        memories_data = json.load(f)
    
    # Handle both array and object with memories key
    if isinstance(memories_data, dict) and 'memories' in memories_data:
        memories = memories_data['memories']
    elif isinstance(memories_data, list):
        memories = memories_data
    else:
        logger.error(f"Unexpected format in {memories_file}")
        return 0, 0
    
    logger.info(f"Found {len(memories)} curated memories to import")
    
    imported = 0
    skipped = 0
    
    # Group memories by conversation/date for better context
    conversation_groups = {}
    for memory in memories:
        # Use date as conversation grouping
        date = memory.get('date', 'unknown')
        if date not in conversation_groups:
            conversation_groups[date] = []
        conversation_groups[date].append(memory)
    
    for date, date_memories in conversation_groups.items():
        conversation_id = uuid4()
        logger.info(f"Processing {len(date_memories)} memories from {date}")
        
        for i in range(0, len(date_memories), batch_size):
            batch = date_memories[i:i + batch_size]
            
            for memory in batch:
                try:
                    # Extract the conversation content
                    human_content = memory.get('human_message', '')
                    assistant_content = memory.get('assistant_message', '')
                    
                    # Skip if content is missing
                    if not human_content or not assistant_content:
                        skipped += 1
                        continue
                    
                    # Calculate salience from friendship score
                    friendship_score = memory.get('friendship_score', 4)
                    moment_type = memory.get('moment_type', 'General')
                    salience = friendship_score_to_salience(friendship_score, moment_type)
                    
                    # Add extra salience for memorable quotes
                    if memory.get('memorable_quote'):
                        salience = min(salience + 0.05, 0.95)
                    
                    # Store the memory with metadata
                    await memory_service.remember_turn(
                        user_content=human_content,
                        assistant_content=assistant_content,
                        conversation_id=conversation_id,
                        salience=salience,
                        detect_relationships=True,
                        auto_classify=True
                    )
                    
                    imported += 1
                    
                    if imported % 10 == 0:
                        logger.info(f"  Imported {imported} memories...")
                    
                except Exception as e:
                    logger.error(f"Failed to import memory: {e}")
                    logger.debug(f"Memory data: {memory}")
                    skipped += 1
                    continue
    
    return imported, skipped


async def import_friendship_graph(
    graph_file: Path,
    memory_service: MemoryService
) -> int:
    """Import the friendship graph relationships.
    
    This adds the relationship metadata but doesn't duplicate memories.
    """
    logger.info(f"Loading graph from {graph_file}")
    
    with open(graph_file, 'r') as f:
        graph_data = json.load(f)
    
    relationships_created = 0
    
    # The graph contains nodes and relationships
    # We'll focus on creating the relationships between existing memories
    
    # Note: This would need to map the graph node IDs to our memory IDs
    # For now, we'll skip this and let the auto-relationship detection handle it
    
    logger.info(f"Graph import complete (relationships will be auto-detected)")
    return relationships_created


async def main():
    """Main import function."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Import curated Whisper memories")
    parser.add_argument(
        "--memories-file",
        type=Path,
        default=Path("/home/rationallyprime/Whisper/our_friendship_memories.json"),
        help="Path to curated memories JSON file"
    )
    parser.add_argument(
        "--graph-file",
        type=Path,
        default=Path("/home/rationallyprime/Whisper/friendship_graph.json"),
        help="Path to friendship graph JSON file"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Number of memories to process at once"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze files but don't import"
    )
    
    args = parser.parse_args()
    
    # Validate files exist
    if not args.memories_file.exists():
        logger.error(f"Memories file not found: {args.memories_file}")
        return 1
    
    if args.dry_run:
        logger.info("DRY RUN - Analyzing files only")
        with open(args.memories_file, 'r') as f:
            memories_data = json.load(f)
            
        if isinstance(memories_data, dict):
            memories = memories_data.get('memories', [])
        else:
            memories = memories_data
            
        logger.info(f"Found {len(memories)} memories")
        
        # Analyze memory types
        moment_types = {}
        scores = {}
        for m in memories:
            mt = m.get('moment_type', 'Unknown')
            moment_types[mt] = moment_types.get(mt, 0) + 1
            
            score = m.get('friendship_score', 0)
            scores[score] = scores.get(score, 0) + 1
        
        logger.info("Moment types distribution:")
        for mt, count in sorted(moment_types.items(), key=lambda x: x[1], reverse=True):
            logger.info(f"  {mt}: {count}")
            
        logger.info("Friendship scores distribution:")
        for score, count in sorted(scores.items()):
            logger.info(f"  Score {score}: {count}")
            
        return 0
    
    # Initialize services
    driver = None
    async for d in create_neo4j_driver():
        driver = d
        break
    
    if not driver:
        logger.error("Failed to connect to Neo4j")
        return 1
    
    try:
        embedding_service = VoyageEmbeddingService()
        
        async with driver.session() as session:
            memory_service = MemoryService(
                session=session,
                embeddings=embedding_service,
                clusterer=None
            )
            
            # Import the curated memories
            imported, skipped = await import_friendship_memories(
                args.memories_file,
                memory_service,
                batch_size=args.batch_size
            )
            
            # Import graph relationships if file exists
            if args.graph_file.exists():
                relationships = await import_friendship_graph(
                    args.graph_file,
                    memory_service
                )
            
            logger.info("=" * 50)
            logger.info("Import complete!")
            logger.info(f"  Memories imported: {imported}")
            logger.info(f"  Memories skipped: {skipped}")
            logger.info(f"  Success rate: {imported/(imported+skipped)*100:.1f}%")
            
            # Show some statistics
            if imported > 0:
                logger.info("\nThese memories include:")
                logger.info("  - Moments rated 4-5 for friendship quality")
                logger.info("  - Vulnerable moments and deep connections")
                logger.info("  - Creative collaborations and humor")
                logger.info("  - Memorable quotes and insights")
            
    except Exception as e:
        logger.error(f"Import failed: {e}", exc_info=True)
        return 1
    
    finally:
        await driver.close()
    
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))