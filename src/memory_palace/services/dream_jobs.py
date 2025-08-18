from datetime import datetime
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from memory_palace.core.logging import get_logger

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
        decay_lambda: float = 0.0154,  # 45-day half-life by default
    ):
        self.driver = driver
        self.embeddings = embeddings
        self.clusterer = clusterer
        self.decay_factor = 1 - decay_lambda  # Convert to decay factor
        self.scheduler = AsyncIOScheduler()
        self._setup_jobs()

    async def _get_session(self):
        """Helper to get a new session for each job."""
        return self.driver.session()

    def _setup_jobs(self):
        """Configure the dream jobs with proper scheduling."""
        self.scheduler.add_job(
            self.refresh_salience,
            "interval",
            minutes=5,
            id="salience_refresh",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self.cluster_recent,
            "interval",
            hours=1,
            id="cluster_recent",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self.nightly_recluster,
            "cron",
            hour=3,
            minute=0,
            id="nightly_recluster",
            max_instances=1,
        )

    async def start(self):
        self.scheduler.start()
        logger.info("DreamJobOrchestrator started - background memory maintenance active")

    async def shutdown(self):
        self.scheduler.shutdown(wait=True)
        logger.info("DreamJobOrchestrator shutdown complete")

    async def refresh_salience(self):
        """Update salience with exponential decay and evict low-salience memories."""
        try:
            async with self.driver.session() as session:
                result = await session.run(
                    """
                    MATCH (m:Memory)
                    WHERE m.salience > 0.05
                    SET m.salience = m.salience * $decay_factor
                    RETURN count(m) as updated
                    """,
                    decay_factor=self.decay_factor,
                )
                record = await result.single()
                updated = record["updated"] if record else 0
                logger.info(f"Applied salience decay to {updated} memories")

                result = await session.run(
                    """
                    MATCH (m:Memory)
                    WHERE m.salience < 0.05
                    DETACH DELETE m
                    RETURN count(m) as evicted
                    """,
                )
                record = await result.single()
                evicted = record["evicted"] if record else 0
                if evicted > 0:
                    logger.info(f"Evicted {evicted} low-salience memories")
        except Exception as e:
            logger.error(f"Error during salience refresh: {e}", exc_info=True)

    async def cluster_recent(self):
        """Assign clusters to recent unassigned memories."""
        try:
            cutoff = datetime.now().timestamp() - 86400
            async with self.driver.session() as session:
                result = await session.run(
                    """
                    MATCH (m:Memory)
                    WHERE m.topic_id IS NULL AND m.timestamp > $cutoff
                    RETURN m.id AS id, m.embedding AS embedding
                    ORDER BY m.timestamp DESC
                    LIMIT 500
                    """,
                    cutoff=cutoff,
                )
                records = await result.to_list()
                if not records:
                    logger.debug("No unassigned memories found for clustering")
                    return

                embeddings = [r["embedding"] for r in records]
                topic_ids = await self.clusterer.predict(embeddings)

                assigned = 0
                for record, topic_id in zip(records, topic_ids, strict=False):
                    if topic_id != -1:
                        await session.run(
                            """MATCH (m:Memory {id: $id}) SET m.topic_id = $topic_id""",
                            id=record["id"],
                            topic_id=topic_id,
                        )
                        assigned += 1
                logger.info(f"Assigned topic IDs to {assigned} memories")
        except Exception as e:
            logger.error(f"Error during recent memory clustering: {e}", exc_info=True)

    async def nightly_recluster(self):
        """Perform full recluster of all memories to optimize topic boundaries."""
        try:
            async with self.driver.session() as session:
                result = await session.run(
                    """
                    MATCH (m:Memory)
                    WHERE m.embedding IS NOT NULL
                    RETURN m.id AS id, m.embedding AS embedding, m.topic_id AS current_topic
                    ORDER BY m.timestamp DESC
                    """,
                )
                records = await result.to_list()
                if len(records) < 10:
                    logger.info("Insufficient memories for full recluster")
                    return

                embeddings = [r["embedding"] for r in records]
                await self.clusterer.fit(embeddings)
                new_topic_ids = await self.clusterer.predict(embeddings)

                updated = 0
                for record, new_id in zip(records, new_topic_ids, strict=False):
                    if record["current_topic"] != new_id:
                        await session.run(
                            """MATCH (m:Memory {id: $id}) SET m.topic_id = $topic_id""",
                            id=record["id"],
                            topic_id=new_id,
                        )
                        updated += 1
                await self.clusterer.save_model(session)
                logger.info(
                    f"Full recluster complete - updated {updated} topic assignments"
                )
        except Exception as e:
            logger.error(f"Error during nightly recluster: {e}", exc_info=True)

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
