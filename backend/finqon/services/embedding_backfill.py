"""Embed transactions that have no embedding yet (the seed ledger, or after a model change)."""

from __future__ import annotations

import logging

from sqlalchemy import text

from finqon.llm.embeddings import to_pgvector
from finqon.privacy.pii import mask_narration
from finqon.repositories.statements import StatementRepository
from finqon.resources import Resources

logger = logging.getLogger(__name__)
_BATCH = 200


async def backfill_embeddings(resources: Resources) -> int:
    """Returns how many transactions were embedded. Safe to re-run."""
    async with resources.database.session() as session:
        user_ids = [r[0] for r in (await session.execute(text("SELECT id FROM identity.users")))]
    done = 0
    for user_id in user_ids:
        while True:
            async with resources.database.user_scope(user_id) as session:
                pending = await StatementRepository(session).unembedded(_BATCH)
            if not pending:
                break
            texts = sorted({mask_narration(desc) for _, desc in pending})
            vectors = await resources.embedder.embed(texts)
            by_text = {t: to_pgvector(v) for t, v in zip(texts, vectors, strict=True)}
            async with resources.database.user_scope(user_id) as session:
                await StatementRepository(session).set_embeddings(
                    [(i, by_text[mask_narration(desc)]) for i, desc in pending]
                )
            done += len(pending)
            logger.info("embedded", extra={"count": done})
    return done
