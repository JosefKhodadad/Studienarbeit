from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import get_import_service
from app.schemas.hilo_import import HiloImportResponse, UnifiedMeasurementsResponse
from app.services.import_service import ImportService

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/monthly-summary", response_model=HiloImportResponse)
def get_monthly_summary(
    report_id: str | None = None,
    import_service: ImportService = Depends(get_import_service),
) -> HiloImportResponse:
    return import_service.get_monthly_summary(report_id=report_id)


@router.get("/unified-measurements", response_model=UnifiedMeasurementsResponse)
def get_unified_measurements(
    import_service: ImportService = Depends(get_import_service),
) -> UnifiedMeasurementsResponse:
    """Return all stored measurements across reports, enriched with the
    night / day-rest classification.
    """
    return import_service.get_unified_measurements()
