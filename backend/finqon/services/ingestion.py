"""Upload intake and the background ingestion job.

``submit`` validates and hashes each file, records a queued job, and starts a bounded
background task. The task parses, reconciles, classifies and imports in stages, writing
progress that the UI polls. Re-uploading identical bytes returns the existing job, so
imports are idempotent. A file whose balances do not reconcile is quarantined and
nothing is imported.

The bytes are held in memory for the job and not stored. That keeps this a single
process design; swapping ``_spawn`` for a durable queue plus an object store is the
scale-out path (see docs/architecture.md).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass

from finqon.core.errors import AppError, NotFoundError
from finqon.ingest.classify import Alias, Classifier
from finqon.ingest.parser import SUPPORTED_SUFFIXES, ParseError, parse_statement
from finqon.repositories.statements import NewTransaction, StatementRepository
from finqon.resources import Resources
from finqon.schemas.files import FileRecord

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES_PER_REQUEST = 5
_MAGIC = {".pdf": b"%PDF", ".xlsx": b"PK\x03\x04"}


class UploadError(AppError):
    status_code = 422
    code = "invalid_upload"


@dataclass(frozen=True, slots=True)
class Incoming:
    filename: str
    data: bytes


def validate_incoming(item: Incoming) -> str | None:
    """Return a user-facing problem with this file, or ``None`` when it is acceptable."""
    name = item.filename.lower()
    suffix = next((s for s in SUPPORTED_SUFFIXES if name.endswith(s)), None)
    if suffix is None:
        return "Unsupported file type. Upload CSV, XLSX or PDF."
    if not item.data:
        return "The file is empty."
    if len(item.data) > MAX_FILE_BYTES:
        return f"The file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB."
    magic = _MAGIC.get(suffix)
    if magic and not item.data.startswith(magic):
        return "The file content does not match its extension."
    return None


class IngestionService:
    def __init__(self, resources: Resources) -> None:
        self._r = resources
        self._tasks: set[asyncio.Task[None]] = set()
        self._slots = asyncio.Semaphore(2)

    # -- intake -----------------------------------------------------------------------

    async def submit(self, user_id: uuid.UUID, files: list[Incoming]) -> list[FileRecord]:
        if not files:
            raise UploadError("Attach at least one file.")
        if len(files) > MAX_FILES_PER_REQUEST:
            raise UploadError(f"Upload at most {MAX_FILES_PER_REQUEST} files at a time.")
        for item in files:
            problem = validate_incoming(item)
            if problem:
                raise UploadError(f"{item.filename}: {problem}")

        records: list[FileRecord] = []
        for item in files:
            digest = hashlib.sha256(item.data).hexdigest()
            async with self._r.database.user_scope(user_id) as session:
                repo = StatementRepository(session)
                record = await repo.create(user_id, item.filename, digest, len(item.data))
                is_new = record is not None
                if record is None:
                    record = await repo.get_by_hash(digest)
            if record is None:  # pragma: no cover - lost a race with a delete
                raise UploadError(f"{item.filename}: please try again.")
            records.append(record)
            if is_new:
                self._spawn(user_id, record, item)
        return records

    def _spawn(self, user_id: uuid.UUID, record: FileRecord, item: Incoming) -> None:
        task = asyncio.create_task(self._run(user_id, record.id, item))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # -- reads ------------------------------------------------------------------------

    async def list_files(self, user_id: uuid.UUID) -> list[FileRecord]:
        async with self._r.database.user_scope(user_id) as session:
            repo = StatementRepository(session)
            await repo.fail_stale()
            return await repo.list_all()

    async def get_file(self, user_id: uuid.UUID, statement_id: uuid.UUID) -> FileRecord:
        async with self._r.database.user_scope(user_id) as session:
            record = await StatementRepository(session).get(statement_id)
        if record is None:
            raise NotFoundError("File not found.")
        return record

    async def delete_file(self, user_id: uuid.UUID, statement_id: uuid.UUID) -> None:
        async with self._r.database.user_scope(user_id) as session:
            deleted = await StatementRepository(session).delete(statement_id)
        if not deleted:
            raise NotFoundError("File not found.")

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # -- the job ----------------------------------------------------------------------

    async def _stage(
        self, user_id: uuid.UUID, statement_id: uuid.UUID, stage: str, pct: int
    ) -> None:
        async with self._r.database.user_scope(user_id) as session:
            await StatementRepository(session).progress(statement_id, stage, pct)

    async def _finish(self, user_id: uuid.UUID, statement_id: uuid.UUID, **fields: object) -> None:
        async with self._r.database.user_scope(user_id) as session:
            await StatementRepository(session).finish(statement_id, **fields)  # type: ignore[arg-type]

    async def _run(self, user_id: uuid.UUID, statement_id: uuid.UUID, item: Incoming) -> None:
        async with self._slots:
            try:
                await self._pipeline(user_id, statement_id, item)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ingestion_failed")
                await self._safe_fail(
                    user_id, statement_id, "Something went wrong while reading this file."
                )

    async def _safe_fail(self, user_id: uuid.UUID, statement_id: uuid.UUID, message: str) -> None:
        try:
            await self._finish(
                user_id, statement_id, status="failed", stage="Failed", error=message
            )
        except Exception:
            logger.exception("ingestion_fail_write_failed")

    async def _pipeline(self, user_id: uuid.UUID, statement_id: uuid.UUID, item: Incoming) -> None:
        await self._stage(user_id, statement_id, "Reading file", 15)
        try:
            parsed = await asyncio.to_thread(parse_statement, item.filename, item.data)
        except ParseError as exc:
            await self._finish(
                user_id, statement_id, status="failed", stage="Failed", error=str(exc)
            )
            return

        await self._stage(user_id, statement_id, "Checking balances", 40)
        if parsed.reconciled is False:
            await self._finish(
                user_id,
                statement_id,
                status="quarantined",
                stage="Needs review",
                skipped=parsed.skipped,
                warnings=parsed.warnings,
                error="Running balances do not match the transactions, so nothing was imported.",
            )
            return

        await self._stage(user_id, statement_id, "Categorising", 65)
        async with self._r.database.user_scope(user_id) as session:
            raw_aliases, defaults, categories, unknown = await StatementRepository(
                session
            ).lookups()
        classifier = Classifier([Alias(*a) for a in raw_aliases], defaults, categories, unknown)
        rows: list[NewTransaction] = []
        for r in parsed.rows:
            c = classifier.classify(r.description, r.direction)
            rows.append(
                NewTransaction(
                    txn_date=r.txn_date,
                    raw_description=r.description,
                    merchant_id=c.merchant_id,
                    category_id=c.category_id,
                    amount=str(r.amount),
                    direction=r.direction,
                    txn_type=c.txn_type,
                    payment_mode=c.payment_mode,
                    is_recurring=c.is_recurring,
                    balance_after=str(r.balance) if r.balance is not None else None,
                )
            )

        await self._stage(user_id, statement_id, "Saving transactions", 85)
        async with self._r.database.user_scope(user_id) as session:
            repo = StatementRepository(session)
            await repo.import_rows(user_id, statement_id, item.filename, rows)
            await repo.finish(
                statement_id,
                status="ready",
                stage="Ready",
                imported=len(rows),
                skipped=parsed.skipped,
                date_from=min(r.txn_date for r in rows),
                date_to=max(r.txn_date for r in rows),
                warnings=parsed.warnings,
            )
