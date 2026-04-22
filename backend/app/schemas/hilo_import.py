from pydantic import BaseModel, Field

from app.models.blood_pressure import BloodPressureMeasurement
from app.models.patient import Patient


class ReportInfo(BaseModel):
    id: str | None = None
    report_month: int | None = None
    report_year: int | None = None
    source: str = "hilo_pdf"
    file_name: str | None = None
    imported_at: str | None = None


class SummarySection(BaseModel):
    mean: float | None = None
    sd: float | None = None
    max: float | None = None
    min: float | None = None
    measurements: int | None = None


class MeasurementTypeBreakdown(BaseModel):
    cuff_calibration: int = 0
    cuff_measurement: int = 0
    phone_measurement: int = 0
    armband: int = 0
    unknown: int = 0


class MonthlySummary(BaseModel):
    measurement_count_total: int = 0
    measurement_count_day_rest: int = 0
    measurement_count_night: int = 0
    measurement_count_by_type: MeasurementTypeBreakdown = Field(default_factory=MeasurementTypeBreakdown)
    unknown_share: float = 0.0
    day_rest: SummarySection = Field(default_factory=SummarySection)
    night: SummarySection = Field(default_factory=SummarySection)
    all_measurements: SummarySection = Field(default_factory=SummarySection)


class HiloImportResponse(BaseModel):
    patient: Patient
    report: ReportInfo
    summary: MonthlySummary
    warnings: list[str] = Field(default_factory=list)
    measurements: list[BloodPressureMeasurement] = Field(default_factory=list)


class ReportListItem(BaseModel):
    """Lightweight metadata for the report-switcher in the frontend."""

    id: str
    patient_id: str | None = None
    patient_name: str | None = None
    report_month: int | None = None
    report_year: int | None = None
    source: str = "hilo_pdf"
    file_name: str | None = None
    measurement_count: int = 0
    imported_at: str | None = None
    is_current: bool = False


class ReportListResponse(BaseModel):
    reports: list[ReportListItem] = Field(default_factory=list)
    current_report_id: str | None = None


class UnifiedMeasurement(BloodPressureMeasurement):
    """Measurement enriched with the owning report's metadata.

    Used by the cross-report unified view so the frontend can display and
    filter data without reloading each individual report payload.
    """

    report_id: str
    report_month: int | None = None
    report_year: int | None = None
    is_night: bool | None = None
    classification_score: float | None = None
    classification_method: str | None = None


class SleepEpisodeSummary(BaseModel):
    start: str
    end: str
    sleep_onset_estimate: str
    wake_estimate: str
    measurement_indices: list[int] = Field(default_factory=list)
    confidence: float = 0.0
    report_id: str | None = None


class UnifiedMeasurementsResponse(BaseModel):
    measurements: list[UnifiedMeasurement] = Field(default_factory=list)
    reports: list[ReportListItem] = Field(default_factory=list)
    classification_method: str | None = None
    warnings: list[str] = Field(default_factory=list)
    sleep_episodes: list[SleepEpisodeSummary] = Field(default_factory=list)
    expected_sleep_hours: tuple[float, float] | None = None


class DirectImportPatient(BaseModel):
    full_name: str | None = None
    birth_date: str | None = None
    gender: str | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    email: str | None = None


class DirectImportReport(BaseModel):
    report_month: int | None = None
    report_year: int | None = None
    source: str = "mobile_api"
    file_name: str | None = None


class DirectImportMeasurement(BaseModel):
    datetime: str
    systolic: int
    diastolic: int
    heart_rate: int
    measurement_type: str = "unknown"


class DirectImportRequest(BaseModel):
    patient: DirectImportPatient
    report: DirectImportReport
    measurements: list[DirectImportMeasurement]


class PatientProfileResponse(BaseModel):
    patient: Patient
    report: ReportInfo


class MeasurementCreateRequest(BaseModel):
    datetime: str
    systolic: int
    diastolic: int
    heart_rate: int
    measurement_type: str = "armband"


class MeasurementUpdateRequest(BaseModel):
    datetime: str | None = None
    systolic: int | None = None
    diastolic: int | None = None
    heart_rate: int | None = None
    measurement_type: str | None = None
