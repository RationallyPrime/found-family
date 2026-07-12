from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pydantic import BaseModel

from memory_palace.core.base import ErrorLevel
from memory_palace.core.decorators import with_error_handling, with_session
from memory_palace.core.logging import get_logger
from memory_palace.infrastructure.neo4j.queries import DreamJobQueries

if TYPE_CHECKING:
    from neo4j import AsyncDriver, AsyncSession

    from memory_palace.services import ClusteringService, EmbeddingService

logger = get_logger(__name__)
MAX_CLUSTERING_MEMORIES = 500


class DreamJobDescriptor(BaseModel):
    """Serializable scheduler job state."""

    id: str
    name: str
    next_run: datetime | None
    function: str


class DreamJobStatus(BaseModel):
    """Typed orchestrator status consumed by the admin API."""

    scheduler_running: bool
    jobs: list[DreamJobDescriptor]


class DreamJobOrchestrator:
    """Background jobs for ontology maintenance and memory lifecycle management."""

    def __init__(
        self,
        driver: AsyncDriver,
        embeddings: EmbeddingService,
        clusterer: ClusteringService,
        decay_lambda: float | None = None,
    ) -> None:
        from memory_palace.core.constants import SALIENCE_DECAY_LAMBDA_PER_DAY

        self.driver = driver
        self.embeddings = embeddings
        self.clusterer = clusterer
        self.decay_lambda = decay_lambda if decay_lambda is not None else SALIENCE_DECAY_LAMBDA_PER_DAY
        self.scheduler = AsyncIOScheduler()
        self._setup_jobs()

    def _setup_jobs(self) -> None:
        """Configure the dream jobs with proper scheduling."""
        from memory_palace.core.constants import (
            CLUSTER_RECENT_INTERVAL_HOURS,
            CONSOLIDATION_HOUR,
            CONSOLIDATION_MINUTE,
            DECAY_JOB_INTERVAL_HOURS,
            NIGHTLY_RECLUSTER_HOUR,
            NIGHTLY_RECLUSTER_MINUTE,
        )

        self.scheduler.add_job(
            self.decay_and_archive,
            "interval",
            hours=DECAY_JOB_INTERVAL_HOURS,
            id="salience_decay",
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

        from memory_palace.services.consolidation import ConsolidationService

        if ConsolidationService.available():
            self.scheduler.add_job(
                self.consolidate,
                "cron",
                hour=CONSOLIDATION_HOUR,
                minute=CONSOLIDATION_MINUTE,
                id="consolidation",
                max_instances=1,
            )
        else:
            logger.info("Consolidation job not scheduled: selected provider has no API key configured")

    async def start(self) -> None:
        await self.nightly_recluster()
        self.scheduler.start()
        logger.info("DreamJobOrchestrator started - background memory maintenance active")

    async def shutdown(self) -> None:
        self.scheduler.shutdown(wait=True)
        logger.info("DreamJobOrchestrator shutdown complete")

    @with_session()
    @with_error_handling(error_level=ErrorLevel.WARNING, reraise=False)
    async def decay_and_archive(self, session: AsyncSession) -> None:
        """Decay salience by elapsed time, then archive stale memories.

        Decay is anchored on each memory's salience_updated_at, so this job
        is idempotent with respect to wall-clock time. Archival adds the
        :Archived label — nothing is ever deleted.
        """
        from memory_palace.core.constants import (
            ARCHIVE_SALIENCE_THRESHOLD,
            ARCHIVE_UNACCESSED_DAYS,
            SALIENCE_FLOOR,
        )

        now = datetime.now(UTC).timestamp()

        query, _ = DreamJobQueries.decay_salience()
        result = await session.run(
            query,
            {"now": now, "decay_lambda": self.decay_lambda, "floor": SALIENCE_FLOOR},
        )
        record = await result.single()
        updated = record["updated"] if record else 0
        logger.info(f"Applied elapsed-time salience decay to {updated} memories")

        query, _ = DreamJobQueries.archive_stale_memories()
        result = await session.run(
            query,
            {
                "threshold": ARCHIVE_SALIENCE_THRESHOLD,
                "cutoff": now - ARCHIVE_UNACCESSED_DAYS * 86400,
            },
        )
        record = await result.single()
        archived = record["archived"] if record else 0
        if archived > 0:
            logger.info(f"Archived {archived} stale memories (reversible - :Archived label)")

    @with_session()
    @with_error_handling(error_level=ErrorLevel.WARNING, reraise=False)
    async def consolidate(self, session: AsyncSession) -> None:
        """Distill un-consolidated episodic cohorts into semantic memories."""
        from memory_palace.services.consolidation import ConsolidationService

        service = ConsolidationService(session, self.embeddings)
        created = await service.run()
        if created:
            logger.info(f"Dream consolidation created {created} semantic memories")

    @with_session()
    @with_error_handling(error_level=ErrorLevel.WARNING, reraise=False)
    async def cluster_recent(self, session: AsyncSession) -> None:
        """Assign clusters to recent unassigned memories."""
        cutoff = datetime.now(UTC).timestamp() - 86400
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

        updates = [
            {"id": record["id"], "topic_id": topic_id}
            for record, topic_id in zip(records, topic_ids, strict=True)
            if topic_id != -1
        ]
        assigned = 0
        if updates:
            query, _ = DreamJobQueries.assign_topics_batch()
            assignment_result = await session.run(query, updates=updates)
            assignment_record = await assignment_result.single()
            assigned = assignment_record["updated"] if assignment_record else 0
            if assigned != len(updates):
                raise RuntimeError("Recent topic assignment snapshot changed before commit")
        logger.info(f"Assigned topic IDs to {assigned} memories")

    @with_session()
    @with_error_handling(error_level=ErrorLevel.WARNING, reraise=False)
    async def nightly_recluster(self, session: AsyncSession) -> None:
        """Perform full recluster of all memories to optimize topic boundaries."""
        # Use centralized query
        query, _ = DreamJobQueries.get_all_memories_for_clustering()
        result = await session.run(query, limit=MAX_CLUSTERING_MEMORIES)
        records = await result.data()
        if len(records) < 10:
            await self.clusterer.reset()
            logger.info("Insufficient memories for full recluster")
            return

        embeddings = [r["embedding"] for r in records]
        await self.clusterer.fit(embeddings)
        new_topic_ids = await self.clusterer.predict(embeddings)

        updates = [
            {"id": record["id"], "topic_id": new_id}
            for record, new_id in zip(records, new_topic_ids, strict=True)
            if record["current_topic"] != new_id
        ]
        updated = 0
        if updates:
            query, _ = DreamJobQueries.assign_topics_batch()
            assignment_result = await session.run(query, updates=updates)
            assignment_record = await assignment_result.single()
            updated = assignment_record["updated"] if assignment_record else 0
            if updated != len(updates):
                raise RuntimeError("Nightly topic assignment snapshot changed before commit")
        logger.info(f"Full recluster complete - updated {updated} topic assignments")

    def get_job_status(self) -> DreamJobStatus:
        """Get status of all scheduled jobs."""
        jobs = self.scheduler.get_jobs()
        return DreamJobStatus(
            scheduler_running=self.scheduler.running,
            jobs=[
                DreamJobDescriptor(
                    id=job.id,
                    name=job.name,
                    next_run=job.next_run_time,
                    function=getattr(job.func, "__name__", type(job.func).__name__),
                )
                for job in jobs
            ],
        )
