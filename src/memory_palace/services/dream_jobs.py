from datetime import datetime
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from memory_palace.core.base import ErrorLevel
from memory_palace.core.decorators import with_error_handling, with_session
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
        from memory_palace.core.config import settings

        self.driver = driver
        self.embeddings = embeddings
        self.clusterer = clusterer
        # Use configurable settings
        if decay_lambda is None:
            decay_lambda = settings.salience_decay_factor
        self.decay_factor = 1 - decay_lambda  # Convert to decay factor
        self.eviction_threshold = settings.salience_eviction_threshold
        self.decay_enabled = settings.salience_decay_enabled
        self.scheduler = AsyncIOScheduler()
        self._setup_jobs()

    async def _get_session(self):
        """Helper to get a new session for each job."""
        return self.driver.session()

    def _setup_jobs(self):
        """Configure the dream jobs with proper scheduling."""
        from memory_palace.core.config import settings
        from memory_palace.core.constants import (
            CLUSTER_RECENT_INTERVAL_HOURS,
            NIGHTLY_RECLUSTER_HOUR,
            NIGHTLY_RECLUSTER_MINUTE,
        )

        # Only add salience refresh job if decay is enabled
        if self.decay_enabled:
            self.scheduler.add_job(
                self.refresh_salience,
                "interval",
                minutes=settings.salience_refresh_interval_minutes,
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

    @with_session()
    @with_error_handling(error_level=ErrorLevel.WARNING, reraise=False)
    async def refresh_salience(self, session):
        """Update salience with exponential decay and evict low-salience memories.

        Only processes memories that are not marked as preserved.
        """
        # Use centralized query with configurable threshold
        query, _ = DreamJobQueries.refresh_salience()
        result = await session.run(
            query,
            decay_factor=self.decay_factor,
            eviction_threshold=self.eviction_threshold,
        )
        record = await result.single()
        updated = record["updated"] if record else 0
        logger.info(
            f"Applied salience decay to {updated} memories (preserved memories skipped)",
            extra={"decay_factor": self.decay_factor, "threshold": self.eviction_threshold},
        )

        # Use centralized query for eviction with audit trail
        query, _ = DreamJobQueries.evict_low_salience()
        result = await session.run(query, eviction_threshold=self.eviction_threshold)
        record = await result.single()
        evicted = record["evicted"] if record else 0
        if evicted > 0:
            evicted_memories = record.get("evicted_memories", [])
            logger.warning(
                f"Evicted {evicted} low-salience memories (preserved memories skipped)",
                extra={
                    "evicted_count": evicted,
                    "threshold": self.eviction_threshold,
                    "sample_evicted": evicted_memories[:5],  # Log first 5 for audit trail
                },
            )

    @with_session()
    @with_error_handling(error_level=ErrorLevel.WARNING, reraise=False)
    async def cluster_recent(self, session):
        """Assign clusters to recent unassigned memories."""
        cutoff = datetime.now().timestamp() - 86400
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

    @with_session()
    @with_error_handling(error_level=ErrorLevel.WARNING, reraise=False)
    async def nightly_recluster(self, session):
        """Perform full recluster of all memories to optimize topic boundaries."""
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
