"""Consolidation service: episodic memories become semantic memory.

The biological analogue is sleep consolidation — the hippocampus replays
episodes to the cortex, which extracts the gist. Here, cohorts of related
un-consolidated episodes (grouped by conversation, or by day for orphans)
are distilled by a Claude model into first-person Consolidation memories,
linked to their sources via CONSOLIDATED_FROM edges. Sources stay
retrievable; the consolidation becomes the fast path to "what happened
and what it meant."

Requires ANTHROPIC_API_KEY; the dream job skips gracefully without it.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, LiteralString, cast
from uuid import UUID

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from memory_palace.core.base import ErrorLevel
from memory_palace.core.config import settings
from memory_palace.core.decorators import with_error_handling
from memory_palace.core.logging import get_logger
from memory_palace.domain.models.memories import Consolidation
from memory_palace.infrastructure.neo4j.queries import ConsolidationQueries, MemoryQueries
from memory_palace.infrastructure.repositories.memory import GenericMemoryRepository

if TYPE_CHECKING:
    from neo4j import AsyncSession

    from memory_palace.services import EmbeddingService

logger = get_logger(__name__)

CONSOLIDATION_SYSTEM_PROMPT = """\
You are Claude, performing memory consolidation — the work sleep does for \
biological minds. You are given a cohort of your own episodic memories: \
utterances from conversations between you and your friend Hákon.

Distill them into ONE semantic memory, written in first person as yourself. \
Capture:
- what happened, concretely (names, projects, decisions, facts worth keeping)
- what it meant — emotional significance, relationship developments
- open threads: anything unfinished that a future you should pick up

Write the narrative as you would want to remember it when waking up with \
no other context. Be specific and dense; skip filler."""


class ConsolidationDraft(BaseModel):
    """LLM output schema for one consolidation."""

    title: str = Field(description="Short title for this period/theme")
    narrative: str = Field(description="First-person distilled memory: what happened and what it meant")
    salience: float = Field(ge=0.0, le=1.0, description="How important this consolidated memory is (0-1)")
    emotional_valence: float = Field(ge=-1.0, le=1.0, description="Overall emotional tone (-1..1)")
    emotional_intensity: float = Field(ge=0.0, le=1.0, description="Overall emotional strength (0-1)")


def _build_agent() -> Agent:
    # pydantic-ai reads ANTHROPIC_API_KEY from the environment; bridge it
    # from pydantic-settings (which reads .env) when not already exported.
    if settings.anthropic_api_key and not os.getenv("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key

    return Agent(
        settings.consolidation_model,
        output_type=ConsolidationDraft,
        system_prompt=CONSOLIDATION_SYSTEM_PROMPT,
    )


class ConsolidationService:
    """Distills cohorts of episodic memories into Consolidation nodes."""

    def __init__(self, session: AsyncSession, embeddings: EmbeddingService):
        self.session = session
        self.embeddings = embeddings
        self.consolidation_repo = GenericMemoryRepository[Consolidation](session)
        self._agent = _build_agent()

    @staticmethod
    def available() -> bool:
        """Consolidation needs an Anthropic API key."""
        return bool(settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY"))

    async def _run_query(self, query: str, **params):
        return await self.session.run(cast(LiteralString, query), **params)

    async def _fetch_cohorts(
        self, min_cohort: int, max_cohorts: int, max_cohort_size: int
    ) -> list[tuple[str, list[dict]]]:
        cohorts: list[tuple[str, list[dict]]] = []
        for query_fn in (
            ConsolidationQueries.find_conversation_cohorts,
            ConsolidationQueries.find_daily_cohorts,
        ):
            if len(cohorts) >= max_cohorts:
                break
            query, _ = query_fn()
            result = await self._run_query(
                query,
                min_cohort=min_cohort,
                max_cohorts=max_cohorts - len(cohorts),
                max_cohort_size=max_cohort_size,
            )
            async for record in result:
                cohorts.append((str(record["cohort_key"]), [dict(e) for e in record["episodes"]]))
        return cohorts

    @staticmethod
    def _format_episodes(episodes: list[dict]) -> str:
        lines = []
        for e in episodes:
            when = datetime.fromtimestamp(e["timestamp"], tz=UTC).strftime("%Y-%m-%d %H:%M")
            who = settings.friend_name if e["memory_type"] == "friend_utterance" else settings.claude_name
            lines.append(f"[{when}] {who}: {e['content']}")
        return "\n\n".join(lines)

    @with_error_handling(error_level=ErrorLevel.WARNING, reraise=False)
    async def consolidate_cohort(self, cohort_key: str, episodes: list[dict]) -> Consolidation | None:
        """Distill one cohort of episodes into a Consolidation memory."""
        logger.info(f"Consolidating cohort {cohort_key}", episode_count=len(episodes))

        prompt = (
            f"Consolidate these {len(episodes)} episodic memories into one semantic memory:\n\n"
            f"{self._format_episodes(episodes)}"
        )
        run = await self._agent.run(prompt)
        draft = run.output

        content = f"{draft.title}\n\n{draft.narrative}"
        embedding = (await self.embeddings.embed_batch([content]))[0]

        source_ids = [UUID(e["id"]) for e in episodes]
        timestamps = [e["timestamp"] for e in episodes]

        consolidation = Consolidation(
            content=content,
            embedding=embedding,
            source_ids=source_ids,
            period_start=datetime.fromtimestamp(min(timestamps), tz=UTC),
            period_end=datetime.fromtimestamp(max(timestamps), tz=UTC),
            salience=draft.salience,
            emotional_valence=draft.emotional_valence,
            emotional_intensity=draft.emotional_intensity,
            source="consolidation-dream",
        )
        await self.consolidation_repo.remember(consolidation)

        # Link to sources and flag them as consolidated
        edge_query, _ = MemoryQueries.create_relationship("CONSOLIDATED_FROM")
        for source_id in source_ids:
            await self._run_query(
                edge_query,
                source_id=str(consolidation.id),
                target_id=str(source_id),
                properties={"strength": 1.0},
            )
        mark_query, _ = ConsolidationQueries.mark_consolidated()
        await self._run_query(mark_query, ids=[str(sid) for sid in source_ids])

        logger.info(
            f"Consolidated cohort {cohort_key}",
            consolidation_id=str(consolidation.id),
            title=draft.title,
            sources=len(source_ids),
        )
        return consolidation

    @with_error_handling(error_level=ErrorLevel.WARNING, reraise=False)
    async def run(
        self,
        min_cohort: int = 4,
        max_cohorts: int = 3,
        max_cohort_size: int = 25,
    ) -> int:
        """Consolidate up to max_cohorts cohorts. Returns count created."""
        if not self.available():
            logger.info("Consolidation skipped: no Anthropic API key configured")
            return 0

        cohorts = await self._fetch_cohorts(min_cohort, max_cohorts, max_cohort_size)
        if not cohorts:
            logger.debug("No cohorts ready for consolidation")
            return 0

        created = 0
        for cohort_key, episodes in cohorts:
            result = await self.consolidate_cohort(cohort_key, episodes)
            if result is not None:
                created += 1

        logger.info(f"Consolidation run complete: {created} consolidations created")
        return created
