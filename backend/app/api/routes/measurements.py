from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import get_import_service
from app.schemas.hilo_import import (
    HiloImportResponse,
    MeasurementCreateRequest,
    MeasurementUpdateRequest,
)
from app.services.import_service import ImportService

router = APIRouter(prefix="/api/measurements", tags=["measurements"])


@router.post("", response_model=HiloImportResponse, status_code=201)
def create_measurement(
    payload: MeasurementCreateRequest,
    import_service: ImportService = Depends(get_import_service),
) -> HiloImportResponse:
    return import_service.create_measurement(payload)


@router.patch("/{entry_id}", response_model=HiloImportResponse)
def update_measurement(
    entry_id: str,
    payload: MeasurementUpdateRequest,
    import_service: ImportService = Depends(get_import_service),
) -> HiloImportResponse:
    return import_service.update_measurement(entry_id, payload)


@router.delete("/{entry_id}", response_model=HiloImportResponse)
def delete_measurement(
    entry_id: str,
    import_service: ImportService = Depends(get_import_service),
) -> HiloImportResponse:
    return import_service.delete_measurement(entry_id)
