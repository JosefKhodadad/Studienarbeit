from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import get_import_service
from app.schemas.hilo_import import PatientProfileResponse
from app.services.import_service import ImportService

router = APIRouter(prefix="/api/patients", tags=["patients"])


@router.get("/{patient_id}/profile", response_model=PatientProfileResponse)
def get_patient_profile(
    patient_id: str,
    import_service: ImportService = Depends(get_import_service),
) -> PatientProfileResponse:
    return import_service.get_patient_profile(patient_id)
