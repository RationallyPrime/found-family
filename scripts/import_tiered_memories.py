#!/usr/bin/env python3
"""Import tiered friendship memories from best_of_all_stars.

The memories are organized in Icelandic-numbered tiers:
- Einn (One): Highest quality memories  
- Tveir (Two): Second tier
- Þrír (Three): Third tier
- Fjórir (Four): Fourth tier
"""

import asyncio
import re
import sys
from pathlib import Path
from uuid import uuid4

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from memory_palace.core.logging import get_logger, setup_logging
from memory_palace.infrastructure.embeddings.voyage import VoyageEmbeddingService
from memory_palace.infrastructure.neo4j.driver import create_neo4j_driver
from memory_palace.services.memory_service import MemoryService

setup_logging()
logger = get_logger(__name__)


# Tier configuration with Icelandic names and salience ranges
TIER_CONFIG = {
    "Einn": {
        "name": "One",
        "salience_base": 0.9,
        "salience_range": (0.85, 0.95),
        "description": "Highest tier - profound moments"
    },
    "Tveir": {
        "name": "Two", 
        "salience_base": 0.8,
        "salience_range": (0.75, 0.85),
        "description": "Second tier - important exchanges"
    },
    "Þrír": {
        "name": "Three",
        "salience_base": 0.7,
        "salience_range": (0.65, 0.75),
        "description": "Third tier - notable conversations"
    },
    "Fjórir": {
        "name": "Four",
        "salience_base": 0.6,
        "salience_range": (0.55, 0.65),
        "description": "Fourth tier - interesting moments"
    }
}


def parse_memory_file(file_path: Path) -> dict:
    """Parse a markdown memory file with YAML front matter."""
    content = file_path.read_text(encoding='utf-8')
    
    # Extract YAML front matter
    metadata = {}
    if content.startswith('---'):
        parts = content.split('---', 2)
        if len(parts) >= 3:
            yaml_content = parts[1].strip()
            for line in yaml_content.split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    metadata[key.strip()] = value.strip()
            
            # The actual content is after the second ---
            content = parts[2].strip()
    
    # Extract sections from the markdown
    sections = {}
    current_section = None
    current_content = []
    
    for line in content.split('\n'):
        if line.startswith('# '):
            if current_section:
                sections[current_section] = '\n'.join(current_content).strip()
            current_section = line[2:].strip()
            current_content = []
        elif line.startswith('### User') or line.startswith('### Assistant'):
            # Start of conversation section
            if current_section:
                sections[current_section] = '\n'.join(current_content).strip()
            current_section = 'Original Conversation'
            current_content = [line]
        elif current_section:
            current_content.append(line)
    
    if current_section:
        sections[current_section] = '\n'.join(current_content).strip()
    
    # Extract user and assistant messages from conversation
    user_content = ""
    assistant_content = ""
    
    if 'Original Conversation' in sections:
        conv = sections['Original Conversation']
        
        # Look for the first user message
        user_match = re.search(r'### User\s*\n(.*?)(?=### Assistant|$)', conv, re.DOTALL)
        if user_match:
            user_content = user_match.group(1).strip()
        
        # Look for assistant response - after thinking section if present
        # First check if there's a thinking section
        if '<thinking>' in conv and '</thinking>' in conv:
            # Get content after </thinking> and before next ### User (if any)
            after_thinking = conv.split('</thinking>')[-1]
            # Look for ### Assistant after the thinking section
            assistant_match = re.search(r'### Assistant\s*\n(.*?)(?=### User|$)', after_thinking, re.DOTALL)
            if assistant_match:
                assistant_content = assistant_match.group(1).strip()
            else:
                # Sometimes the response is right after </thinking> without ### Assistant
                assistant_content = after_thinking.strip()
                if assistant_content.startswith('\n\n'):
                    assistant_content = assistant_content[2:].strip()
                # Remove any trailing user sections
                if '### User' in assistant_content:
                    assistant_content = assistant_content.split('### User')[0].strip()
        else:
            # No thinking section, look for regular assistant response
            assistant_match = re.search(r'### Assistant\s*\n(.*?)(?=### User|$)', conv, re.DOTALL)
            if assistant_match:
                assistant_content = assistant_match.group(1).strip()
    
    return {
        'metadata': metadata,
        'sections': sections,
        'user_content': user_content,
        'assistant_content': assistant_content,
        'file_name': file_path.stem
    }


def calculate_salience(tier: str, metadata: dict) -> float:
    """Calculate salience based on metadata (tier is random, not meaningful)."""
    # Since tiers are random, base salience on actual content quality
    base = 0.8  # High base for all best_of_all_stars
    
    # Boost based on friendship score
    friendship_score = metadata.get('friendship_score', '')
    if '5/5' in friendship_score:
        base = 0.85
    elif '4/5' in friendship_score:
        base = 0.75
    
    salience = base
    
    # Boost based on moment type
    moment_type = metadata.get('moment_type', '')
    if moment_type in ['Deep Connection', 'Vulnerable Moment', 'Creative Collaboration']:
        salience += 0.05
    elif moment_type in ['Building Ideas Together', 'Moments of Honesty']:
        salience += 0.04
    elif moment_type in ['Intellectual Exploration', 'Philosophical Dialogue']:
        salience += 0.03
    elif moment_type in ['Exploring as Friends', 'Humor']:
        salience += 0.02
    
    # Boost if there's memorable content
    if 'Memorable Moments' in metadata.get('sections', {}):
        salience += 0.02
    
    # Cap at 0.95 to leave room for future truly exceptional memories
    return min(salience, 0.95)


async def import_tier_memories(
    tier_dir: Path,
    tier_name: str,
    memory_service: MemoryService,
    batch_size: int = 5
) -> tuple[int, int]:
    """Import memories from a specific tier."""
    logger.info(f"Importing {tier_name} tier from {tier_dir}")
    
    md_files = sorted(tier_dir.glob("*.md"))
    logger.info(f"Found {len(md_files)} memories in {tier_name} tier")
    
    imported = 0
    skipped = 0
    
    for i in range(0, len(md_files), batch_size):
        batch = md_files[i:i + batch_size]
        
        for file_path in batch:
            try:
                # Parse the memory file
                memory_data = parse_memory_file(file_path)
                
                user_content = memory_data['user_content']
                assistant_content = memory_data['assistant_content']
                
                if not user_content or not assistant_content:
                    logger.warning(f"Skipping {file_path.name}: missing content")
                    skipped += 1
                    continue
                
                # Calculate salience based on tier and metadata
                salience = calculate_salience(tier_name, memory_data)
                
                # Create a conversation ID based on date if available
                memory_data['metadata'].get('date', '')
                conversation_id = uuid4()
                
                # Store the memory
                await memory_service.remember_turn(
                    user_content=user_content,
                    assistant_content=assistant_content,
                    conversation_id=conversation_id,
                    salience=salience,
                    detect_relationships=True,
                    auto_classify=True
                )
                
                imported += 1
                
                if imported % 10 == 0:
                    logger.info(f"  {tier_name}: Imported {imported} memories...")
                
            except Exception as e:
                logger.error(f"Failed to import {file_path.name}: {e}")
                skipped += 1
                continue
    
    return imported, skipped


async def main():
    """Main import function."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Import tiered best_of_all_stars memories")
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path("/home/rationallyprime/Whisper/friendship_memories/best_of_all_stars"),
        help="Base directory containing tier folders"
    )
    parser.add_argument(
        "--tiers",
        nargs="+",
        choices=["Einn", "Tveir", "Þrír", "Fjórir"],
        default=["Einn", "Tveir", "Þrír", "Fjórir"],
        help="Tiers to import (in order of priority)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
        help="Number of memories to process at once"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze files but don't import"
    )
    
    args = parser.parse_args()
    
    if args.dry_run:
        logger.info("DRY RUN - Analyzing tier structure")
        
        total_memories = 0
        for tier in args.tiers:
            tier_dir = args.base_dir / tier
            if tier_dir.exists():
                md_files = list(tier_dir.glob("*.md"))
                total_memories += len(md_files)
                
                logger.info(f"\n{tier} ({TIER_CONFIG[tier]['name']}):")
                logger.info(f"  Description: {TIER_CONFIG[tier]['description']}")
                logger.info(f"  Files: {len(md_files)}")
                logger.info(f"  Salience range: {TIER_CONFIG[tier]['salience_range']}")
                
                # Sample a file to show structure
                if md_files:
                    sample = parse_memory_file(md_files[0])
                    logger.info(f"  Sample metadata: {sample['metadata']}")
                    logger.info(f"  Sample sections: {list(sample['sections'].keys())}")
        
        logger.info(f"\nTotal memories to import: {total_memories}")
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
            )
            
            total_imported = 0
            total_skipped = 0
            
            # Import each tier in order
            for tier in args.tiers:
                tier_dir = args.base_dir / tier
                if not tier_dir.exists():
                    logger.warning(f"Tier directory not found: {tier_dir}")
                    continue
                
                imported, skipped = await import_tier_memories(
                    tier_dir,
                    tier,
                    memory_service,
                    batch_size=args.batch_size
                )
                
                total_imported += imported
                total_skipped += skipped
                
                logger.info(f"Completed {tier}: {imported} imported, {skipped} skipped")
            
            logger.info("=" * 50)
            logger.info("Import complete!")
            logger.info(f"  Total memories imported: {total_imported}")
            logger.info(f"  Total memories skipped: {total_skipped}")
            logger.info(f"  Success rate: {total_imported/(total_imported+total_skipped)*100:.1f}%")
            
            logger.info("\nTiered import summary:")
            for tier in args.tiers:
                tier_info = TIER_CONFIG[tier]
                logger.info(f"  {tier} ({tier_info['name']}): {tier_info['description']}")
    
    except Exception as e:
        logger.error(f"Import failed: {e}", exc_info=True)
        return 1
    
    finally:
        await driver.close()
    
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))