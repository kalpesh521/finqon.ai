"""Statement upload endpoints. The user id comes only from the verified token."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Response, UploadFile

from finqon.api.dependencies import CurrentUserDep, IngestionServiceDep
from finqon.schemas.files import FileRecord
from finqon.services.ingestion import MAX_FILE_BYTES, Incoming, UploadError

router = APIRouter(prefix="/files", tags=["files"])


@router.post("", response_model=list[FileRecord], status_code=202)
async def upload_files(
    files: Annotated[list[UploadFile], File(description="CSV, XLSX or PDF statements.")],
    user: CurrentUserDep,
    service: IngestionServiceDep,
) -> list[FileRecord]:
    incoming: list[Incoming] = []
    for upload in files:
        data = await upload.read(MAX_FILE_BYTES + 1)
        name = (upload.filename or "statement").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if len(data) > MAX_FILE_BYTES:
            raise UploadError(f"{name}: the file is larger than {MAX_FILE_BYTES // 1048576} MB.")
        incoming.append(Incoming(name, data))
    return await service.submit(user.id, incoming)


@router.get("", response_model=list[FileRecord])
async def list_files(user: CurrentUserDep, service: IngestionServiceDep) -> list[FileRecord]:
    return await service.list_files(user.id)


@router.get("/{file_id}", response_model=FileRecord)
async def get_file(
    file_id: uuid.UUID, user: CurrentUserDep, service: IngestionServiceDep
) -> FileRecord:
    return await service.get_file(user.id, file_id)


@router.delete("/{file_id}", status_code=204)
async def delete_file(
    file_id: uuid.UUID, user: CurrentUserDep, service: IngestionServiceDep
) -> Response:
    await service.delete_file(user.id, file_id)
    return Response(status_code=204)
