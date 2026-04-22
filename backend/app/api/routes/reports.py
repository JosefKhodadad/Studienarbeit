from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import get_import_service
from app.schemas.hilo_import import HiloImportResponse, ReportListResponse
from app.services.import_service import ImportService

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("", response_model=ReportListResponse)
def list_reports(
    import_service: ImportService = Depends(get_import_service),
) -> ReportListResponse:
    """Return all stored reports (newest first) plus the currently active id."""
    return import_service.list_reports()


@router.delete("/{report_id}", response_model=ReportListResponse)
def delete_report(
    report_id: str,
    import_service: ImportService = Depends(get_import_service),
) -> ReportListResponse:
    """Remove an entire monthly report including its measurements."""
    return import_service.delete_report(report_id)


@router.post("/{report_id}/activate", response_model=HiloImportResponse)
def activate_report(
    report_id: str,
    import_service: ImportService = Depends(get_import_service),
) -> HiloImportResponse:
    """Make ``report_id`` the current report exposed via /dashboard endpoints."""
    return import_service.activate_report(report_id)
