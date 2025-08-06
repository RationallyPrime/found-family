import logging
from datetime import datetime
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler

if TYPE_CHECKING:
    from memory_palace.services.memory_service import MemoryService

logger = logging.getLogger(__name__)


class DreamJobOrchestrator:
    """Background jobs for ontology maintenance and memory lifecycle management."""
    
    def __init__(self, memory_service: "MemoryService"):
        self.memory = memory_service
        self.scheduler = AsyncIOScheduler()
        self._setup_jobs()
    
    def _setup_jobs(self):
        """Configure the dream jobs with proper scheduling."""
        # Every 5 minutes: salience refresh and memory eviction
        self.scheduler.add_job(
            self.refresh_salience,
            'interval',
            minutes=5,
            id='salience_refresh',
            max_instances=1,  # Prevent overlapping executions
            coalesce=True
        )
        
        # Hourly: cluster recent memories without topic assignments
        self.scheduler.add_job(
            self.cluster_recent,
            'interval',
            hours=1,
            id='cluster_recent',
            max_instances=1,
            coalesce=True
        )
        
        # Nightly: full recluster at 3 AM
        self.scheduler.add_job(
            self.nightly_recluster,
            'cron',
            hour=3,
            minute=0,
            id='nightly_recluster',
            max_instances=1
        )
    
    async def start(self):
        """Start the scheduler."""
        self.scheduler.start()
        logger.info("DreamJobOrchestrator started - background memory maintenance active")
    
    async def shutdown(self):
        """Gracefully shutdown the scheduler."""
        self.scheduler.shutdown(wait=True)
        logger.info("DreamJobOrchestrator shutdown complete")
    
    async def refresh_salience(self):
        """Update salience with exponential decay and evict low-salience memories."""
        try:
            # 45-day half-life: λ = ln(2)/45 ≈ 0.0154
            decay_lambda = 0.0154
            decay_factor = 1 - decay_lambda
            
            from memory_palace.infrastructure.neo4j.query_builder import CypherQueryBuilder
            
            # Apply decay to all memories with salience > eviction threshold
            builder = CypherQueryBuilder()
            decay_query = (
                builder
                .match(lambda p: p.node("Memory", "m"))
                .where("m.salience > 0.05")
                .set_property("m", {
                    "salience": f"m.salience * {decay_factor}"
                })
                .return_clause("count(m) as updated")
            )
            
            result = await self.memory.neo4j.execute_builder(decay_query)
            updated_count = result[0]['updated'] if result else 0
            logger.info(f"Applied salience decay to {updated_count} memories")
            
            # Evict memories below threshold
            eviction_builder = CypherQueryBuilder()
            eviction_query = (
                eviction_builder
                .match(lambda p: p.node("Memory", "m"))
                .where("m.salience < 0.05")
                .detach_delete("m")
                .return_clause("count(m) as evicted")
            )
            
            eviction_result = await self.memory.neo4j.execute_builder(eviction_query)
            evicted_count = eviction_result[0]['evicted'] if eviction_result else 0
            
            if evicted_count > 0:
                logger.info(f"Evicted {evicted_count} low-salience memories")
                
        except Exception as e:
            logger.error(f"Error during salience refresh: {e}", exc_info=True)
    
    async def cluster_recent(self):
        """Assign clusters to recent unassigned memories."""
        try:
            from memory_palace.infrastructure.neo4j.query_builder import CypherQueryBuilder
            
            # Get recent memories without topic_id (created in last 24 hours)
            query_builder = CypherQueryBuilder()
            query = (
                query_builder
                .match(lambda p: p.node("Memory", "m"))
                .where("m.topic_id IS NULL")
                .where(f"m.timestamp > {datetime.now().timestamp() - 86400}")  # Last 24 hours
                .return_clause("m")
                .order_by("m.timestamp DESC")
                .limit(500)
            )
            
            unassigned = await self.memory.neo4j.execute_builder(query)
            if not unassigned:
                logger.debug("No unassigned memories found for clustering")
                return
            
            logger.info(f"Found {len(unassigned)} unassigned memories for clustering")
            
            # Extract embeddings
            embeddings = [m['embedding'] for m in unassigned]
            
            # Predict clusters using the clusterer
            if hasattr(self.memory, 'clusterer'):
                topic_ids = await self.memory.clusterer.predict(embeddings)
                
                # Update memories with assignments
                assigned_count = 0
                for memory, topic_id in zip(unassigned, topic_ids, strict=False):
                    if topic_id != -1:  # Not noise
                        update_builder = CypherQueryBuilder()
                        update_query = (
                            update_builder
                            .match(lambda p: p.node("Memory", "m"))
                            .where(f"m.id = '{memory['id']}'")
                            .set_property("m", {"topic_id": topic_id})
                        )
                        await self.memory.neo4j.execute_builder(update_query)
                        assigned_count += 1
                
                logger.info(f"Assigned topic IDs to {assigned_count} memories")
            else:
                logger.warning("Clusterer not available - skipping topic assignment")
                
        except Exception as e:
            logger.error(f"Error during recent memory clustering: {e}", exc_info=True)
    
    async def nightly_recluster(self):
        """Perform full recluster of all memories to optimize topic boundaries."""
        try:
            logger.info("Starting nightly full recluster")
            
            from memory_palace.infrastructure.neo4j.query_builder import CypherQueryBuilder
            
            # Get all memories with embeddings
            query_builder = CypherQueryBuilder()
            query = (
                query_builder
                .match(lambda p: p.node("Memory", "m"))
                .where("m.embedding IS NOT NULL")
                .return_clause("m.id as id", "m.embedding as embedding", "m.topic_id as current_topic")
                .order_by("m.timestamp DESC")
            )
            
            all_memories = await self.memory.neo4j.execute_builder(query)
            if len(all_memories) < 10:  # Need minimum memories for clustering
                logger.info("Insufficient memories for full recluster")
                return
            
            logger.info(f"Reclustering {len(all_memories)} memories")
            
            # Extract embeddings for full recluster
            embeddings = [m['embedding'] for m in all_memories]
            
            if hasattr(self.memory, 'clusterer'):
                # Perform full refit and predict
                await self.memory.clusterer.fit(embeddings)
                new_topic_ids = await self.memory.clusterer.predict(embeddings)
                
                # Update all memories with new topic assignments
                updated_count = 0
                for memory, new_topic_id in zip(all_memories, new_topic_ids, strict=False):
                    if memory['current_topic'] != new_topic_id:
                        update_builder = CypherQueryBuilder()
                        update_query = (
                            update_builder
                            .match(lambda p: p.node("Memory", "m"))
                            .where(f"m.id = '{memory['id']}'")
                            .set_property("m", {"topic_id": new_topic_id})
                        )
                        await self.memory.neo4j.execute_builder(update_query)
                        updated_count += 1
                
                logger.info(f"Full recluster complete - updated {updated_count} topic assignments")
            else:
                logger.warning("Clusterer not available - skipping full recluster")
                
        except Exception as e:
            logger.error(f"Error during nightly recluster: {e}", exc_info=True)
    
    def get_job_status(self) -> dict:
        """Get status of all scheduled jobs."""
        jobs = self.scheduler.get_jobs()
        return {
            'scheduler_running': self.scheduler.running,
            'jobs': [
                {
                    'id': job.id,
                    'name': job.name,
                    'next_run': job.next_run_time.isoformat() if job.next_run_time else None,
                    'func': job.func.__name__
                }
                for job in jobs
            ]
        }
