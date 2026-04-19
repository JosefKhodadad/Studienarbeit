from pydantic import BaseModel, Field

from app.models.blood_pressure import BloodPressureMeasurement
from app.models.patient import Patient


class ReportInfo(BaseModel):
    report_month: int | None = None
    report_year: int | None = None
    source: str = "hilo_pdf"


class SummarySection(BaseModel):
    mean: float | None = None
    sd: float | None = None
    max: float | None = Field(default=None, alias="max")
    min: float | None = Field(default=None, alias="min")
    measurements: int | None = None


class HiloImportResponse(BaseModel):
    patient: Patient
    report: ReportInfo
    summary: dict[str, SummarySection]
    measurements: list[BloodPressureMeasurement]
