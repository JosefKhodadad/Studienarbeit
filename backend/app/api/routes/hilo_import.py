from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.dependencies import get_import_service
from app.schemas.hilo_import import HiloImportResponse
from app.services.import_service import ImportService

router = APIRouter(prefix="/api/hilo", tags=["hilo-import"])


@router.post("/import", response_model=HiloImportResponse)
async def import_hilo_report(
    file: UploadFile | None = File(default=None),
    file_path: str | None = Form(default=None),
    import_service: ImportService = Depends(get_import_service),
) -> HiloImportResponse:
    if not file and not file_path:
        raise HTTPException(status_code=400, detail="Provide either file or file_path.")

    if file:
        return import_service.import_hilo_from_bytes(await file.read(), file.filename)
    return import_service.import_hilo_from_path(file_path or "")


@router.post("/import-and-save", response_model=HiloImportResponse)
async def import_and_save_hilo_report(
    file: UploadFile | None = File(default=None),
    file_path: str | None = Form(default=None),
    import_service: ImportService = Depends(get_import_service),
) -> HiloImportResponse:
    if not file and not file_path:
        raise HTTPException(status_code=400, detail="Provide either file or file_path.")

    if file:
        return import_service.import_hilo_from_bytes(await file.read(), file.filename)
    return import_service.import_hilo_from_path(file_path or "")
