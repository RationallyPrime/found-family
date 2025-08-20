#!/usr/bin/env python3
"""
Migration script to remove ConversationTurn nodes and create PRECEDES relationships.

This script:
1. Finds all existing ConversationTurn nodes
2. Creates PRECEDES relationships between the friend and claude utterances
3. Deletes the ConversationTurn nodes
4. Verifies no orphaned turns remain
"""

import asyncio
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))

from neo4j import AsyncGraphDatabase
from memory_palace.core.config import settings
from memory_palace.core.logging import get_logger

logger = get_logger(__name__)


async def migrate_conversation_turns():
    """Migrate ConversationTurn nodes to PRECEDES relationships."""
    
    # Create driver
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )
    
    try:
        async with driver.session() as session:
            # Count existing ConversationTurn nodes
            count_result = await session.run(
                "MATCH (t:ConversationTurn) RETURN count(t) as count"
            )
            record = await count_result.single()
            total_turns = record["count"] if record else 0
            
            if total_turns == 0:
                logger.info("No ConversationTurn nodes found. Migration not needed.")
                return
            
            logger.info(f"Found {total_turns} ConversationTurn nodes to migrate")
            
            # Migrate each turn
            migration_query = """
                MATCH (t:ConversationTurn)
                WITH t LIMIT 100
                
                // Find the related utterances
                MATCH (f:Memory {id: t.friend_utterance_id})
                MATCH (c:Memory {id: t.claude_utterance_id})
                
                // Create PRECEDES relationship if it doesn't exist
                MERGE (f)-[r:PRECEDES]->(c)
                ON CREATE SET 
                    r.strength = 1.0,
                    r.temporal = true,
                    r.migrated_from = 'conversation_turn',
                    r.migration_date = datetime()
                
                // Delete the turn node
                DELETE t
                
                RETURN count(t) as migrated
                """
            
            migrated = 0
            while True:
                result = await session.run(migration_query)
                record = await result.single()
                batch_count = record["migrated"] if record else 0
                
                if batch_count == 0:
                    break
                    
                migrated += batch_count
                logger.info(f"Migrated {migrated}/{total_turns} turns...")
            
            # Verify no turns remain
            verify_result = await session.run(
                "MATCH (t:ConversationTurn) RETURN count(t) as remaining"
            )
            record = await verify_result.single()
            remaining = record["remaining"] if record else 0
            
            if remaining > 0:
                logger.warning(f"WARNING: {remaining} ConversationTurn nodes could not be migrated")
                
                # Log details of remaining turns
                detail_result = await session.run(
                    """
                    MATCH (t:ConversationTurn)
                    RETURN t.id as turn_id, 
                           t.friend_utterance_id as friend_id,
                           t.claude_utterance_id as claude_id
                    LIMIT 10
                    """
                )
                
                async for record in detail_result:
                    logger.warning(
                        f"Unmigrated turn: {record['turn_id']} "
                        f"(friend: {record['friend_id']}, claude: {record['claude_id']})"
                    )
            else:
                logger.info(f"✅ Successfully migrated all {total_turns} ConversationTurn nodes")
            
            # Count new PRECEDES relationships
            precedes_result = await session.run(
                """
                MATCH ()-[r:PRECEDES {migrated_from: 'conversation_turn'}]->()
                RETURN count(r) as count
                """
            )
            record = await precedes_result.single()
            precedes_count = record["count"] if record else 0
            
            logger.info(f"Created {precedes_count} PRECEDES relationships")
            
    finally:
        await driver.close()


async def verify_migration():
    """Verify the migration was successful."""
    
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )
    
    try:
        async with driver.session() as session:
            # Check for any remaining ConversationTurn nodes
            result = await session.run(
                "MATCH (t:ConversationTurn) RETURN count(t) as count"
            )
            record = await result.single()
            turn_count = record["count"] if record else 0
            
            # Check for PRECEDES relationships
            result = await session.run(
                "MATCH ()-[r:PRECEDES]->() RETURN count(r) as count"
            )
            record = await result.single()
            precedes_count = record["count"] if record else 0
            
            # Check for orphaned memories (no relationships)
            result = await session.run(
                """
                MATCH (m:Memory)
                WHERE NOT (m)-[]-()
                RETURN count(m) as count
                """
            )
            record = await result.single()
            orphan_count = record["count"] if record else 0
            
            logger.info("=== Migration Verification ===")
            logger.info(f"ConversationTurn nodes remaining: {turn_count}")
            logger.info(f"PRECEDES relationships: {precedes_count}")
            logger.info(f"Orphaned memories: {orphan_count}")
            
            if turn_count == 0:
                logger.info("✅ Migration verified successful!")
            else:
                logger.warning("⚠️ Migration incomplete - ConversationTurn nodes still exist")
                
    finally:
        await driver.close()


async def main():
    """Run the migration."""
    logger.info("Starting ConversationTurn migration...")
    
    # Run migration
    await migrate_conversation_turns()
    
    # Verify results
    await verify_migration()
    
    logger.info("Migration complete!")


if __name__ == "__main__":
    asyncio.run(main())