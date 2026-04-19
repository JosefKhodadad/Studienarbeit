from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from app.schemas.hilo_import import DirectImportRequest, HiloImportResponse, PatientProfileResponse
from app.services.aggregation_service import AggregationService
from app.services.parser_service import HiloParserService
from app.services.repository import InMemoryImportRepository


class ImportService:
    def __init__(
        self,
        repository: InMemoryImportRepository,
        parser_service: HiloParserService,
        aggregation_service: AggregationService,
    ) -> None:
        self._repository = repository
        self._parser_service = parser_service
        self._aggregation_service = aggregation_service

    def import_hilo_from_path(self, file_path: str) -> HiloImportResponse:
        path = Path(file_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {path}")
        try:
            parsed = self._parser_service.parse_file(path)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Hilo PDF import failed: {exc}") from exc
        return self._store_payload(
            self._aggregation_service.build_from_parser_result(parsed, file_name=path.name)
        )

    def import_hilo_from_bytes(self, content: bytes, file_name: str | None = None) -> HiloImportResponse:
        try:
            parsed = self._parser_service.parse_bytes(content)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Hilo PDF import failed: {exc}") from exc
        return self._store_payload(
            self._aggregation_service.build_from_parser_result(parsed, file_name=file_name)
        )

    def import_measurements(self, payload: DirectImportRequest) -> HiloImportResponse:
        return self._store_payload(self._aggregation_service.build_from_direct_import(payload))

    def get_monthly_summary(self, report_id: str | None = None) -> HiloImportResponse:
        try:
            payload = (
                self._repository.get_dashboard_payload(report_id)
                if report_id
                else self._repository.get_current_dashboard_payload()
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="No imported report is available yet.") from exc
        return HiloImportResponse.model_validate(payload)

    def get_patient_profile(self, patient_id: str) -> PatientProfileResponse:
        try:
            payload = self._repository.get_patient_profile(patient_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Patient not found: {patient_id}") from exc
        return PatientProfileResponse.model_validate(payload)

    def _store_payload(self, payload: HiloImportResponse) -> HiloImportResponse:
        saved = self._repository.save_dashboard_payload(payload.model_dump())
        return HiloImportResponse.model_validate(saved)
