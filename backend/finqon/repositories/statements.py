"""Uploaded statement jobs and their imported rows. Use inside ``Database.user_scope``."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from finqon.schemas.files import FileRecord

_COLUMNS = (
    "id, filename, size_bytes, status, stage, progress, rows_imported, rows_skipped, "
    "date_from, date_to, warnings, error, created_at, completed_at"
)


@dataclass(frozen=True, slots=True)
class NewTransaction:
    txn_date: dt.date
    raw_description: str
    merchant_id: int | None
    category_id: int | None
    amount: str
    direction: str
    txn_type: str
    payment_mode: str
    is_recurring: bool
    balance_after: str | None
    embedding: str | None = None  # pgvector text literal


class StatementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self, user_id: uuid.UUID, filename: str, sha256: str, size: int
    ) -> FileRecord | None:
        """Insert a queued job, or return ``None`` when this user already uploaded the file."""
        try:
            async with self._s.begin_nested():
                result = await self._s.execute(
                    text(
                        "INSERT INTO ledger.statements (user_id, filename, sha256, size_bytes) "
                        f"VALUES (:u, :f, :h, :n) RETURNING {_COLUMNS}"
                    ),
                    {"u": user_id, "f": filename[:255], "h": sha256, "n": size},
                )
        except IntegrityError:
            return None
        return FileRecord.model_validate(dict(result.mappings().one()))

    async def list_all(self) -> list[FileRecord]:
        result = await self._s.execute(
            text(f"SELECT {_COLUMNS} FROM ledger.statements ORDER BY created_at DESC LIMIT 100")
        )
        return [FileRecord.model_validate(dict(r)) for r in result.mappings()]

    async def get(self, statement_id: uuid.UUID) -> FileRecord | None:
        result = await self._s.execute(
            text(f"SELECT {_COLUMNS} FROM ledger.statements WHERE id = :i"), {"i": statement_id}
        )
        row = result.mappings().first()
        return FileRecord.model_validate(dict(row)) if row else None

    async def progress(self, statement_id: uuid.UUID, stage: str, percent: int) -> None:
        await self._s.execute(
            text(
                "UPDATE ledger.statements SET status = 'processing', stage = :s, progress = :p "
                "WHERE id = :i"
            ),
            {"i": statement_id, "s": stage, "p": percent},
        )

    async def finish(
        self,
        statement_id: uuid.UUID,
        *,
        status: str,
        stage: str,
        imported: int = 0,
        skipped: int = 0,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
        warnings: Sequence[str] = (),
        error: str | None = None,
    ) -> None:
        await self._s.execute(
            text(
                "UPDATE ledger.statements SET status = :st, stage = :sg, progress = :pg, "
                "rows_imported = :im, rows_skipped = :sk, date_from = :df, date_to = :dt, "
                "warnings = CAST(:w AS jsonb), error = :e, completed_at = now() WHERE id = :i"
            ),
            {
                "i": statement_id,
                "st": status,
                "sg": stage,
                "pg": 100 if status == "ready" else 0,
                "im": imported,
                "sk": skipped,
                "df": date_from,
                "dt": date_to,
                "w": json.dumps(list(warnings)),
                "e": error,
            },
        )

    async def delete(self, statement_id: uuid.UUID) -> bool:
        result = await self._s.execute(
            text("DELETE FROM ledger.statements WHERE id = :i RETURNING id"), {"i": statement_id}
        )
        return result.first() is not None

    async def fail_stale(self) -> int:
        """Jobs left unfinished by a restart cannot resume (bytes are not kept)."""
        result = await self._s.execute(
            text(
                "UPDATE ledger.statements SET status = 'failed', stage = 'failed', "
                "error = 'Processing was interrupted. Upload the file again.', "
                "completed_at = now() WHERE status IN ('queued', 'processing') "
                "AND created_at < now() - interval '10 minutes' RETURNING id"
            )
        )
        return len(result.all())

    # -- lookups used by the classifier (shared, not user-owned) ---------------------

    async def lookups(
        self,
    ) -> tuple[list[tuple[str, str, int]], dict[int, int | None], dict[str, int], int | None]:
        aliases = (
            await self._s.execute(
                text("SELECT pattern, match_type, merchant_id FROM ledger.merchant_aliases")
            )
        ).all()
        merchants = (
            await self._s.execute(
                text("SELECT id, name, default_category_id FROM ledger.merchants")
            )
        ).all()
        categories = (await self._s.execute(text("SELECT id, name FROM ledger.categories"))).all()
        unknown = next((m.id for m in merchants if m.name == "Unknown Merchant"), None)
        return (
            [(a.pattern, a.match_type, a.merchant_id) for a in aliases],
            {m.id: m.default_category_id for m in merchants},
            {c.name: c.id for c in categories},
            unknown,
        )

    # -- the import ------------------------------------------------------------------

    async def import_rows(
        self, user_id: uuid.UUID, statement_id: uuid.UUID, name: str, rows: Sequence[NewTransaction]
    ) -> None:
        account_id = (
            await self._s.execute(
                text(
                    "INSERT INTO ledger.accounts (id, user_id, name, account_type, statement_id) "
                    "VALUES (nextval('ledger.upload_account_id_seq'), :u, :n, 'savings', :s) "
                    "RETURNING id"
                ),
                {"u": user_id, "n": name[:120], "s": statement_id},
            )
        ).scalar_one()
        insert = text(
            "INSERT INTO ledger.transactions (id, account_id, txn_date, raw_description, "
            "merchant_id, category_id, amount, direction, txn_type, payment_mode, "
            "is_recurring, balance_after, embedding) "
            "VALUES (nextval('ledger.upload_txn_id_seq'), :a, :d, :r, :m, :c, :amt, :dir, "
            ":t, :pm, :rec, :bal, CAST(:emb AS vector))"
        )
        for start in range(0, len(rows), 500):
            await self._s.execute(
                insert,
                [
                    {
                        "a": account_id,
                        "d": r.txn_date,
                        "r": r.raw_description,
                        "m": r.merchant_id,
                        "c": r.category_id,
                        "amt": r.amount,
                        "dir": r.direction,
                        "t": r.txn_type,
                        "pm": r.payment_mode,
                        "rec": r.is_recurring,
                        "bal": r.balance_after,
                    }
                    for r in rows[start : start + 500]
                ],
            )

    async def get_by_hash(self, sha256: str) -> FileRecord | None:
        result = await self._s.execute(
            text(f"SELECT {_COLUMNS} FROM ledger.statements WHERE sha256 = :h"), {"h": sha256}
        )
        row = result.mappings().first()
        return FileRecord.model_validate(dict(row)) if row else None

    async def account_names(self) -> list[str]:
        """Names of the accounts created from uploads (RLS limits them to the caller)."""
        result = await self._s.execute(
            text(
                "SELECT name FROM ledger.accounts WHERE statement_id IS NOT NULL "
                "ORDER BY id DESC LIMIT 10"
            )
        )
        return [r[0] for r in result.all()]

    # -- cache validity -------------------------------------------------------------

    async def data_version(self) -> str:
        """A short fingerprint of the caller's ledger, for the semantic cache.

        Row-level security limits every count to the caller. Uploads add an account and its
        transactions with ids from sequences that only grow; deleting a statement removes
        them. Any of those moves at least one of the four numbers, so an entry written for one
        ledger is never offered for another.
        """
        # A savepoint: if this read ever fails, the caller's transaction is still usable.
        async with self._s.begin_nested():
            row = (
                await self._s.execute(
                    text(
                        "SELECT (SELECT count(*) FROM ledger.accounts) AS accounts, "
                        "(SELECT coalesce(max(id), 0) FROM ledger.accounts) AS max_account, "
                        "(SELECT count(*) FROM ledger.transactions) AS txns, "
                        "(SELECT coalesce(max(id), 0) FROM ledger.transactions) AS max_txn"
                    )
                )
            ).one()
        material = f"{row.accounts}:{row.max_account}:{row.txns}:{row.max_txn}"
        return hashlib.sha256(material.encode()).hexdigest()[:16]

    # -- embedding backfill ---------------------------------------------------------

    async def unembedded(self, limit: int) -> list[tuple[int, str]]:
        result = await self._s.execute(
            text(
                "SELECT id, raw_description FROM ledger.transactions "
                "WHERE embedding IS NULL ORDER BY id LIMIT :n"
            ),
            {"n": limit},
        )
        return [(r.id, r.raw_description) for r in result.all()]

    async def set_embeddings(self, rows: Sequence[tuple[int, str]]) -> None:
        await self._s.execute(
            text("UPDATE ledger.transactions SET embedding = CAST(:e AS vector) WHERE id = :i"),
            [{"i": txn_id, "e": vector} for txn_id, vector in rows],
        )
