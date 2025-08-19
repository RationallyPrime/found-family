from datetime import datetime
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from memory_palace.core.base import ErrorLevel
from memory_palace.core.decorators import with_error_handling
from memory_palace.core.logging import get_logger
from memory_palace.infrastructure.neo4j.queries import DreamJobQueries

if TYPE_CHECKING:
    from neo4j import AsyncDriver

    from memory_palace.services import ClusteringService, EmbeddingService

logger = get_logger(__name__)


class DreamJobOrchestrator:
    """Background jobs for ontology maintenance and memory lifecycle management."""

    def __init__(
        self,
        driver: "AsyncDriver",
        embeddings: "EmbeddingService",
        clusterer: "ClusteringService",
        decay_lambda: float | None = None,
    ):
        from memory_palace.core.constants import SALIENCE_DECAY_FACTOR_DEFAULT
        
        self.driver = driver
        self.embeddings = embeddings
        self.clusterer = clusterer
        if decay_lambda is None:
            decay_lambda = SALIENCE_DECAY_FACTOR_DEFAULT
        self.decay_factor = 1 - decay_lambda  # Convert to decay factor
        self.scheduler = AsyncIOScheduler()
        self._setup_jobs()

    async def _get_session(self):
        """Helper to get a new session for each job."""
        return self.driver.session()

    def _setup_jobs(self):
        """Configure the dream jobs with proper scheduling."""
        from memory_palace.core.constants import (
            SALIENCE_REFRESH_INTERVAL_MINUTES,
            CLUSTER_RECENT_INTERVAL_HOURS,
            NIGHTLY_RECLUSTER_HOUR,
            NIGHTLY_RECLUSTER_MINUTE,
        )
        
        self.scheduler.add_job(
            self.refresh_salience,
            "interval",
            minutes=SALIENCE_REFRESH_INTERVAL_MINUTES,
            id="salience_refresh",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self.cluster_recent,
            "interval",
            hours=CLUSTER_RECENT_INTERVAL_HOURS,
            id="cluster_recent",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self.nightly_recluster,
            "cron",
            hour=NIGHTLY_RECLUSTER_HOUR,
            minute=NIGHTLY_RECLUSTER_MINUTE,
            id="nightly_recluster",
            max_instances=1,
        )

    async def start(self):
        self.scheduler.start()
        logger.info("DreamJobOrchestrator started - background memory maintenance active")

    async def shutdown(self):
        self.scheduler.shutdown(wait=True)
        logger.info("DreamJobOrchestrator shutdown complete")

    @with_error_handling(error_level=ErrorLevel.WARNING, reraise=False)
    async def refresh_salience(self):
        """Update salience with exponential decay and evict low-salience memories."""
        async with self.driver.session() as session:
            # Use centralized query
            query, _ = DreamJobQueries.refresh_salience()
            result = await session.run(
                query,
                decay_factor=self.decay_factor,
            )
            record = await result.single()
            updated = record["updated"] if record else 0
            logger.info(f"Applied salience decay to {updated} memories")

            # Use centralized query for eviction
            query, _ = DreamJobQueries.evict_low_salience()
            result = await session.run(query)
            record = await result.single()
            evicted = record["evicted"] if record else 0
            if evicted > 0:
                logger.info(f"Evicted {evicted} low-salience memories")

    @with_error_handling(error_level=ErrorLevel.WARNING, reraise=False)
    async def cluster_recent(self):
        """Assign clusters to recent unassigned memories."""
        cutoff = datetime.now().timestamp() - 86400
        async with self.driver.session() as session:
            # Use centralized query
            query, _ = DreamJobQueries.find_unassigned_memories()
            result = await session.run(
                query,
                cutoff=cutoff,
            )
            records = await result.data()
            if not records:
                logger.debug("No unassigned memories found for clustering")
                return

            embeddings = [r["embedding"] for r in records]
            topic_ids = await self.clusterer.predict(embeddings)

            assigned = 0
            for record, topic_id in zip(records, topic_ids, strict=False):
                if topic_id != -1:
                    # Use centralized query
                    query, _ = DreamJobQueries.assign_topic()
                    await session.run(
                        query,
                        id=record["id"],
                        topic_id=topic_id,
                    )
                    assigned += 1
            logger.info(f"Assigned topic IDs to {assigned} memories")

    @with_error_handling(error_level=ErrorLevel.WARNING, reraise=False)
    async def nightly_recluster(self):
        """Perform full recluster of all memories to optimize topic boundaries."""
        async with self.driver.session() as session:
            # Use centralized query
            query, _ = DreamJobQueries.get_all_memories_for_clustering()
            result = await session.run(query)
            records = await result.data()
            if len(records) < 10:
                logger.info("Insufficient memories for full recluster")
                return

            embeddings = [r["embedding"] for r in records]
            await self.clusterer.fit(embeddings)
            new_topic_ids = await self.clusterer.predict(embeddings)

            updated = 0
            for record, new_id in zip(records, new_topic_ids, strict=False):
                if record["current_topic"] != new_id:
                    # Use centralized query
                    query, _ = DreamJobQueries.assign_topic()
                    await session.run(
                        query,
                        id=record["id"],
                        topic_id=new_id,
                    )
                    updated += 1
            # Note: save_model method doesn't exist on ClusteringService
            # await self.clusterer.save_model(session)
            logger.info(f"Full recluster complete - updated {updated} topic assignments")

    def get_job_status(self) -> dict:
        """Get status of all scheduled jobs."""
        jobs = self.scheduler.get_jobs()
        return {
            "scheduler_running": self.scheduler.running,
            "jobs": [
                {
                    "id": job.id,
                    "name": job.name,
                    "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                    "func": job.func.__name__,
                }
                for job in jobs
            ],
        }
