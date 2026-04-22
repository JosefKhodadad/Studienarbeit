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
    # Backwards-compatible: ``mean`` / ``sd`` / ``max`` / ``min`` keep referring
    # to systolic. The diastolic and heart-rate fields are new and may be
    # ``None`` in older payloads.
    mean: float | None = None
    sd: float | None = None
    max: float | None = None
    min: float | None = None
    measurements: int | None = None
    mean_diastolic: float | None = None
    mean_heart_rate: float | None = None
    min_diastolic: float | None = None
    min_heart_rate: float | None = None
    max_diastolic: float | None = None
    max_heart_rate: float | None = None


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


class ArousalEventSummary(BaseModel):
    start: str
    end: str
    measurement_indices: list[int] = Field(default_factory=list)


class SleepEpisodeSummary(BaseModel):
    start: str
    end: str
    sleep_onset_estimate: str
    wake_estimate: str
    measurement_indices: list[int] = Field(default_factory=list)
    confidence: float = 0.0
    report_id: str | None = None
    arousal_events: list[ArousalEventSummary] = Field(default_factory=list)


class CrossReportAverages(BaseModel):
    """Aggregated SBP/DBP/HR averages across all imported reports.

    Computed from the per-report PDF overview tables when available, with a
    fallback to the actually-classified measurements. Used by the dashboard
    to show a multi-document night/day picture instead of just the currently
    selected report.
    """

    night_systolic: float | None = None
    night_diastolic: float | None = None
    night_heart_rate: float | None = None
    day_rest_systolic: float | None = None
    day_rest_diastolic: float | None = None
    day_rest_heart_rate: float | None = None
    report_count: int = 0


class UnifiedMeasurementsResponse(BaseModel):
    measurements: list[UnifiedMeasurement] = Field(default_factory=list)
    reports: list[ReportListItem] = Field(default_factory=list)
    classification_method: str | None = None
    warnings: list[str] = Field(default_factory=list)
    sleep_episodes: list[SleepEpisodeSummary] = Field(default_factory=list)
    expected_sleep_hours: tuple[float, float] | None = None
    cross_report_averages: CrossReportAverages = Field(default_factory=CrossReportAverages)


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
