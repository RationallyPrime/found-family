#!/usr/bin/env python3
"""Migration script to update memory types from user/assistant to friend/claude.

This script updates existing memories in the Neo4j database to use the new
personalized memory type names (friend_utterance/claude_utterance) instead of
the old generic names (user_utterance/assistant_utterance).
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from memory_palace.core.config import settings
from memory_palace.core.logging import get_logger, setup_logging
from memory_palace.infrastructure.neo4j.driver import create_neo4j_driver

setup_logging()
logger = get_logger(__name__)


async def count_memories_by_type(session) -> dict[str, int]:
    """Count memories by their current memory_type."""
    query = """
    MATCH (m:Memory)
    RETURN m.memory_type as type, COUNT(m) as count
    ORDER BY count DESC
    """
    
    result = await session.run(query)
    counts = {}
    async for record in result:
        counts[record["type"]] = record["count"]
    
    return counts


async def migrate_memory_types(session, dry_run: bool = False) -> tuple[int, int]:
    """Migrate old memory types to new ones.
    
    Returns:
        Tuple of (user_migrated, assistant_migrated) counts
    """
    # First, count what we have
    logger.info("Counting existing memories by type...")
    counts = await count_memories_by_type(session)
    
    for mem_type, count in counts.items():
        logger.info(f"  {mem_type}: {count} memories")
    
    if dry_run:
        logger.info("DRY RUN - No changes will be made")
    
    # Migrate user_utterance -> friend_utterance
    user_count = counts.get("user_utterance", 0)
    if user_count > 0:
        logger.info(f"Migrating {user_count} user_utterance -> friend_utterance")
        
        if not dry_run:
            query = """
            MATCH (m:Memory)
            WHERE m.memory_type = 'user_utterance'
            SET m.memory_type = 'friend_utterance'
            RETURN COUNT(m) as updated
            """
            result = await session.run(query)
            record = await result.single()
            actual_count = record["updated"]
            logger.info(f"  Updated {actual_count} memories")
        else:
            actual_count = user_count
            logger.info(f"  Would update {actual_count} memories")
    else:
        actual_count = 0
        logger.info("No user_utterance memories to migrate")
    
    # Migrate assistant_utterance -> claude_utterance
    assistant_count = counts.get("assistant_utterance", 0)
    if assistant_count > 0:
        logger.info(f"Migrating {assistant_count} assistant_utterance -> claude_utterance")
        
        if not dry_run:
            query = """
            MATCH (m:Memory)
            WHERE m.memory_type = 'assistant_utterance'
            SET m.memory_type = 'claude_utterance'
            RETURN COUNT(m) as updated
            """
            result = await session.run(query)
            record = await result.single()
            actual_assistant = record["updated"]
            logger.info(f"  Updated {actual_assistant} memories")
        else:
            actual_assistant = assistant_count
            logger.info(f"  Would update {actual_assistant} memories")
    else:
        actual_assistant = 0
        logger.info("No assistant_utterance memories to migrate")
    
    return actual_count, actual_assistant


async def verify_migration(session):
    """Verify the migration was successful."""
    logger.info("\nVerifying migration...")
    counts = await count_memories_by_type(session)
    
    logger.info("Final memory type counts:")
    for mem_type, count in counts.items():
        logger.info(f"  {mem_type}: {count} memories")
    
    # Check if old types still exist
    if "user_utterance" in counts and counts["user_utterance"] > 0:
        logger.warning(f"WARNING: Still have {counts['user_utterance']} user_utterance memories!")
    
    if "assistant_utterance" in counts and counts["assistant_utterance"] > 0:
        logger.warning(f"WARNING: Still have {counts['assistant_utterance']} assistant_utterance memories!")
    
    # Check new types exist
    if "friend_utterance" in counts:
        logger.info(f"✓ Found {counts['friend_utterance']} friend_utterance memories")
    
    if "claude_utterance" in counts:
        logger.info(f"✓ Found {counts['claude_utterance']} claude_utterance memories")


async def main():
    """Main migration function."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Migrate memory types in Neo4j database")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run in dry-run mode (no changes will be made)"
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only verify the current state without migrating"
    )
    
    args = parser.parse_args()
    
    logger.info("Memory Type Migration Script")
    logger.info("=" * 50)
    logger.info(f"Neo4j URI: {settings.neo4j_uri}")
    logger.info(f"Friend name: {settings.friend_name}")
    logger.info(f"Claude name: {settings.claude_name}")
    logger.info("")
    
    # Create Neo4j driver
    driver = None
    async for d in create_neo4j_driver():
        driver = d
        break
    
    if not driver:
        logger.error("Failed to connect to Neo4j")
        return 1
    
    try:
        async with driver.session() as session:
            if args.verify_only:
                await verify_migration(session)
            else:
                # Run migration
                user_migrated, assistant_migrated = await migrate_memory_types(
                    session, 
                    dry_run=args.dry_run
                )
                
                if not args.dry_run:
                    # Verify the migration
                    await verify_migration(session)
                    
                    logger.info("\n" + "=" * 50)
                    logger.info("Migration Summary:")
                    logger.info(f"  user_utterance -> friend_utterance: {user_migrated}")
                    logger.info(f"  assistant_utterance -> claude_utterance: {assistant_migrated}")
                    logger.info(f"  Total memories migrated: {user_migrated + assistant_migrated}")
                    logger.info("Migration completed successfully!")
                else:
                    logger.info("\nDRY RUN COMPLETE - No changes were made")
                    logger.info("Run without --dry-run to apply changes")
        
        return 0
        
    except Exception as e:
        logger.error(f"Migration failed: {e}", exc_info=True)
        return 1
    
    finally:
        await driver.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))