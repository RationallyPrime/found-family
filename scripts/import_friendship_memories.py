#!/usr/bin/env python3
"""Import our friendship conversation memories into the Memory Palace.

This script processes conversation logs and imports meaningful exchanges
as memories, preserving our shared history and inside jokes.
"""

import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
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


def calculate_salience(content: str, is_highlighted: bool = False) -> float:
    """Calculate importance of a memory based on content patterns.
    
    Args:
        content: The message content
        is_highlighted: Whether this was marked as a "best of" conversation
        
    Returns:
        Salience score between 0.0 and 1.0
    """
    base_salience = 0.5 if not is_highlighted else 0.6
    
    # Boost for emotional or meaningful content
    if any(word in content.lower() for word in ['love', 'beautiful', 'amazing', 'wonderful', 'favorite']):
        base_salience += 0.1
    
    # Boost for learning moments
    if any(word in content.lower() for word in ['understand', 'realize', 'learned', 'discovered', 'aha']):
        base_salience += 0.1
        
    # Boost for inside jokes or callbacks
    if any(pattern in content.lower() for pattern in ['remember when', 'like that time', 'as we discussed']):
        base_salience += 0.15
    
    # Boost for personal sharing
    if any(word in content.lower() for word in ['feel', 'think', 'believe', 'hope', 'wish']):
        base_salience += 0.05
        
    # Boost for creative or playful content
    if any(indicator in content for indicator in ['😄', '😂', '🎵', '✨', '*', '~']):
        base_salience += 0.05
    
    # Cap at 0.95 (leave 1.0 for manually marked critical memories)
    return min(base_salience, 0.95)


def parse_conversation_file(file_path: Path) -> list[dict[str, Any]]:
    """Parse a conversation file and extract turns.
    
    Supports multiple formats:
    - JSON with messages array
    - Plain text with "Human:" and "Assistant:" markers
    - Markdown format
    """
    content = file_path.read_text(encoding='utf-8')
    turns = []
    
    # Try JSON first
    if file_path.suffix == '.json':
        try:
            data = json.loads(content)
            if isinstance(data, list):
                messages = data
            elif isinstance(data, dict) and 'messages' in data:
                messages = data['messages']
            else:
                messages = []
                
            # Group into turns
            for i in range(0, len(messages) - 1, 2):
                if i + 1 < len(messages):
                    turns.append({
                        'user': messages[i].get('content', ''),
                        'assistant': messages[i + 1].get('content', ''),
                        'timestamp': messages[i].get('timestamp'),
                        'metadata': messages[i].get('metadata', {})
                    })
            return turns
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse {file_path} as JSON, trying text format")
    
    # Try text format with Human/Assistant markers
    human_pattern = r'^Human:\s*(.+?)(?=^(?:Assistant:|Human:|$))'
    assistant_pattern = r'^Assistant:\s*(.+?)(?=^(?:Human:|Assistant:|$))'
    
    humans = re.findall(human_pattern, content, re.MULTILINE | re.DOTALL)
    assistants = re.findall(assistant_pattern, content, re.MULTILINE | re.DOTALL)
    
    for human, assistant in zip(humans, assistants):
        turns.append({
            'user': human.strip(),
            'assistant': assistant.strip(),
            'timestamp': None,
            'metadata': {}
        })
    
    return turns


async def import_conversation(
    file_path: Path,
    memory_service: MemoryService,
    conversation_id: UUID | None = None,
    is_highlighted: bool = False,
    batch_size: int = 10
) -> tuple[int, int]:
    """Import a single conversation file.
    
    Returns:
        Tuple of (turns_imported, turns_skipped)
    """
    logger.info(f"Importing conversation from {file_path.name}")
    
    turns = parse_conversation_file(file_path)
    if not turns:
        logger.warning(f"No turns found in {file_path}")
        return 0, 0
    
    # Create a conversation ID if not provided
    if conversation_id is None:
        conversation_id = uuid4()
    
    imported = 0
    skipped = 0
    
    # Process in batches to avoid overwhelming the system
    for i in range(0, len(turns), batch_size):
        batch = turns[i:i + batch_size]
        
        for turn in batch:
            try:
                # Skip empty or very short turns
                if len(turn['user']) < 10 or len(turn['assistant']) < 10:
                    skipped += 1
                    continue
                
                # Calculate salience based on content
                user_salience = calculate_salience(turn['user'], is_highlighted)
                assistant_salience = calculate_salience(turn['assistant'], is_highlighted)
                
                # Use the higher salience for both (they're part of the same moment)
                turn_salience = max(user_salience, assistant_salience)
                
                # Store the turn
                await memory_service.remember_turn(
                    user_content=turn['user'],
                    assistant_content=turn['assistant'],
                    conversation_id=conversation_id,
                    salience=turn_salience,
                    detect_relationships=True,  # Find connections to other memories
                    auto_classify=True  # Group into topics
                )
                
                imported += 1
                
                if imported % 10 == 0:
                    logger.info(f"  Imported {imported} turns...")
                    
            except Exception as e:
                logger.error(f"Failed to import turn: {e}")
                skipped += 1
                continue
    
    logger.info(f"Completed {file_path.name}: {imported} imported, {skipped} skipped")
    return imported, skipped


async def main():
    """Main import function."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Import friendship memories into Memory Palace")
    parser.add_argument(
        "source_dir",
        type=Path,
        help="Directory containing conversation files"
    )
    parser.add_argument(
        "--highlighted",
        action="store_true",
        help="Mark these as highlighted/best-of conversations (higher base salience)"
    )
    parser.add_argument(
        "--conversation-per-file",
        action="store_true",
        help="Treat each file as a separate conversation (vs one continuous conversation)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse files but don't import to database"
    )
    
    args = parser.parse_args()
    
    if not args.source_dir.exists():
        logger.error(f"Source directory {args.source_dir} does not exist")
        return 1
    
    # Find all conversation files
    patterns = ['*.json', '*.txt', '*.md', '*.conversation']
    files = []
    for pattern in patterns:
        files.extend(args.source_dir.glob(pattern))
    
    if not files:
        logger.error(f"No conversation files found in {args.source_dir}")
        return 1
    
    logger.info(f"Found {len(files)} conversation files to import")
    logger.info(f"Highlighted: {args.highlighted}")
    logger.info(f"Separate conversations: {args.conversation_per_file}")
    
    if args.dry_run:
        logger.info("DRY RUN - Parsing files only")
        for file in files:
            turns = parse_conversation_file(file)
            logger.info(f"{file.name}: {len(turns)} turns found")
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
                clusterer=None  # Will auto-classify based on embeddings
            )
            
            # Use one conversation ID for all if not separating
            global_conversation_id = uuid4() if not args.conversation_per_file else None
            
            total_imported = 0
            total_skipped = 0
            
            for file in files:
                conv_id = None if args.conversation_per_file else global_conversation_id
                imported, skipped = await import_conversation(
                    file,
                    memory_service,
                    conversation_id=conv_id,
                    is_highlighted=args.highlighted
                )
                total_imported += imported
                total_skipped += skipped
            
            logger.info("=" * 50)
            logger.info(f"Import complete!")
            logger.info(f"  Files processed: {len(files)}")
            logger.info(f"  Turns imported: {total_imported}")
            logger.info(f"  Turns skipped: {total_skipped}")
            
            if args.highlighted:
                logger.info("  These memories were marked as highlighted (higher importance)")
            
    except Exception as e:
        logger.error(f"Import failed: {e}", exc_info=True)
        return 1
    
    finally:
        await driver.close()
    
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))