from __future__ import annotations

from collections import Counter
from datetime import datetime
from statistics import mean, pstdev
from uuid import uuid4

from app.models.blood_pressure import (
    ENTRY_STATUS_MANUAL,
    ENTRY_STATUS_ORIGINAL,
)
from app.schemas.hilo_import import (
    BloodPressureCategoryBreakdown,
    DirectImportRequest,
    HiloImportResponse,
    MeasurementTypeBreakdown,
    MonthlySummary,
    PatientProfileResponse,
    ReportInfo,
    SummarySection,
)
from app.services.hilo.date_parser import calculate_age_at_report, extract_report_period, parse_german_date


VALID_MEASUREMENT_TYPES = (
    "cuff_calibration",
    "cuff_measurement",
    "phone_measurement",
    "armband",
    "unknown",
)


class AggregationService:
    TYPE_ALIASES = {
        "calibration_with_cuff": "cuff_calibration",
        "cuff_calibration": "cuff_calibration",
        "cuff_measurement": "cuff_measurement",
        "phone_measurement": "phone_measurement",
        "armband": "armband",
        "unknown": "unknown",
    }

    def build_from_parser_result(
        self,
        parsed: dict,
        *,
        file_name: str | None = None,
    ) -> HiloImportResponse:
        patient = parsed.get("patient", {})
        report = parsed.get("report", {})
        measurements = parsed.get("measurements", [])
        summary = parsed.get("summary", {})
        return self._build_payload(
            patient_data=patient,
            report_data={**report, "file_name": file_name or report.get("file_name")},
            measurements_data=measurements,
            raw_summary=summary,
            default_source="hilo_import",
            default_status=ENTRY_STATUS_ORIGINAL,
        )

    def build_from_direct_import(self, payload: DirectImportRequest) -> HiloImportResponse:
        return self._build_payload(
            patient_data=payload.patient.model_dump(),
            report_data=payload.report.model_dump(),
            measurements_data=[item.model_dump() for item in payload.measurements],
            raw_summary=None,
            default_source="mobile_api",
            default_status=ENTRY_STATUS_MANUAL,
        )

    def rebuild_payload(
        self,
        *,
        patient: dict,
        report: dict,
        measurements: list[dict],
        raw_summary: dict | None = None,
    ) -> HiloImportResponse:
        """Rebuild a dashboard payload from already-normalized parts.

        Used when measurements change (edit/delete/create) and the derived
        summary needs to be recomputed without re-parsing the PDF.
        """
        normalized_measurements = self._normalize_measurements(measurements)
        summary = self._build_summary(normalized_measurements, raw_summary)
        warnings = self._build_warnings(normalized_measurements, report, raw_summary)
        return HiloImportResponse.model_validate(
            {
                "patient": patient,
                "report": report,
                "summary": summary.model_dump(),
                "warnings": warnings,
                "measurements": normalized_measurements,
            }
        )

    def build_profile_response(self, dashboard_payload: dict) -> PatientProfileResponse:
        return PatientProfileResponse.model_validate(
            {
                "patient": dashboard_payload["patient"],
                "report": dashboard_payload["report"],
            }
        )

    def _build_payload(
        self,
        *,
        patient_data: dict,
        report_data: dict,
        measurements_data: list[dict],
        raw_summary: dict | None,
        default_source: str,
        default_status: str,
    ) -> HiloImportResponse:
        import_batch_id = f"batch_{uuid4().hex[:12]}"
        normalized_measurements = self._normalize_measurements(
            measurements_data,
            default_source=default_source,
            default_status=default_status,
            default_source_file=report_data.get("file_name"),
            default_import_batch_id=import_batch_id,
            snapshot_original=(default_status == ENTRY_STATUS_ORIGINAL),
        )
        normalized_report = self._normalize_report(report_data, normalized_measurements)
        normalized_patient = self._normalize_patient(patient_data, normalized_report)
        summary = self._build_summary(normalized_measurements, raw_summary)
        warnings = self._build_warnings(normalized_measurements, normalized_report, raw_summary)

        return HiloImportResponse.model_validate(
            {
                "patient": normalized_patient,
                "report": normalized_report,
                "summary": summary.model_dump(),
                "warnings": warnings,
                "measurements": normalized_measurements,
            }
        )

    def _normalize_patient(self, patient_data: dict, report: dict) -> dict:
        birth_date = patient_data.get("birth_date")
        parsed_birth = parse_german_date(str(birth_date)) if birth_date else None
        report_text = self._report_text_for_age(report)
        report_period = extract_report_period(report_text) if report_text else None
        age = patient_data.get("age_at_report_date")
        if age is None:
            age = calculate_age_at_report(parsed_birth, report_period)

        return {
            "id": patient_data.get("id") or self._generate_id("patient"),
            "full_name": patient_data.get("full_name"),
            "email": patient_data.get("email"),
            "gender": patient_data.get("gender"),
            "birth_date": parsed_birth.isoformat() if parsed_birth else birth_date,
            "age_at_report_date": age,
            "height_cm": self._to_float(patient_data.get("height_cm")),
            "weight_kg": self._to_float(patient_data.get("weight_kg")),
        }

    def _normalize_report(self, report_data: dict, measurements: list[dict]) -> dict:
        report_month = report_data.get("report_month")
        report_year = report_data.get("report_year")

        if (report_month is None or report_year is None) and measurements:
            first_dt = datetime.fromisoformat(measurements[0]["datetime"])
            report_month = report_month or first_dt.month
            report_year = report_year or first_dt.year

        return ReportInfo.model_validate(
            {
                "id": report_data.get("id") or self._generate_id("report"),
                "report_month": report_month,
                "report_year": report_year,
                "source": report_data.get("source") or "unknown_source",
                "file_name": report_data.get("file_name"),
            }
        ).model_dump()

    def _normalize_measurements(
        self,
        measurements_data: list[dict],
        *,
        default_source: str | None = None,
        default_status: str | None = None,
        default_source_file: str | None = None,
        default_import_batch_id: str | None = None,
        snapshot_original: bool = False,
    ) -> list[dict]:
        normalized: list[dict] = []
        for idx, item in enumerate(measurements_data, start=1):
            dt_value = datetime.fromisoformat(str(item["datetime"])).isoformat()
            measurement_type = self._normalize_type(item.get("measurement_type"))
            entry = {
                "entry_id": item.get("entry_id") or self._generate_id("entry"),
                "datetime": dt_value,
                "systolic": int(item["systolic"]),
                "diastolic": int(item["diastolic"]),
                "heart_rate": int(item["heart_rate"]),
                "measurement_type": measurement_type,
                "source_page": int(item.get("source_page", 0) or 0),
                "source_column": item.get("source_column", "import") or "import",
                "row_index_on_page": int(item.get("row_index_on_page", idx) or idx),
                "source": item.get("source") or default_source or "import",
                "source_file": item.get("source_file") or default_source_file,
                "import_batch_id": item.get("import_batch_id") or default_import_batch_id,
                "entry_status": item.get("entry_status") or default_status or ENTRY_STATUS_ORIGINAL,
                "original_values": item.get("original_values"),
                "edited_at": item.get("edited_at"),
            }
            if snapshot_original and entry["original_values"] is None:
                entry["original_values"] = {
                    "datetime": entry["datetime"],
                    "systolic": entry["systolic"],
                    "diastolic": entry["diastolic"],
                    "heart_rate": entry["heart_rate"],
                    "measurement_type": entry["measurement_type"],
                }
            normalized.append(entry)
        normalized.sort(key=lambda entry: entry["datetime"])
        return normalized

    def _normalize_type(self, raw: str | None) -> str:
        if raw is None:
            return "unknown"
        return self.TYPE_ALIASES.get(str(raw), "unknown") if str(raw) not in VALID_MEASUREMENT_TYPES else str(raw)

    def _build_summary(self, measurements: list[dict], raw_summary: dict | None) -> MonthlySummary:
        type_counter = Counter(item["measurement_type"] for item in measurements)
        auto_day_measurements = [item for item in measurements if self._is_day_measurement(item["datetime"])]
        auto_night_measurements = [item for item in measurements if not self._is_day_measurement(item["datetime"])]

        day_rest_section = self._build_section(raw_summary, "day_rest", auto_day_measurements)
        night_section = self._build_section(raw_summary, "night", auto_night_measurements)
        all_section = self._build_section(raw_summary, "all_measurements", measurements)

        total_count = len(measurements)
        day_count = day_rest_section.measurements or len(auto_day_measurements)
        night_count = night_section.measurements or len(auto_night_measurements)
        unknown_count = type_counter.get("unknown", 0)

        return MonthlySummary(
            measurement_count_total=total_count,
            measurement_count_day_rest=day_count,
            measurement_count_night=night_count,
            measurement_count_by_type=MeasurementTypeBreakdown(
                cuff_calibration=type_counter.get("cuff_calibration", 0),
                cuff_measurement=type_counter.get("cuff_measurement", 0),
                phone_measurement=type_counter.get("phone_measurement", 0),
                armband=type_counter.get("armband", 0),
                unknown=unknown_count,
            ),
            measurement_count_by_bp_category=self._build_bp_categories(measurements),
            unknown_share=round((unknown_count / total_count), 4) if total_count else 0.0,
            day_rest=day_rest_section,
            night=night_section,
            all_measurements=all_section,
        )

    def _build_bp_categories(self, measurements: list[dict]) -> BloodPressureCategoryBreakdown:
        counter: Counter[str] = Counter()
        for item in measurements:
            category = self._classify_bp(item["systolic"], item["diastolic"])
            counter[category] += 1
        return BloodPressureCategoryBreakdown(
            hypotension=counter.get("hypotension", 0),
            optimal=counter.get("optimal", 0),
            normal=counter.get("normal", 0),
            high_normal=counter.get("high_normal", 0),
            hypertension_grade_1=counter.get("hypertension_grade_1", 0),
            hypertension_grade_2=counter.get("hypertension_grade_2", 0),
            hypertension_grade_3=counter.get("hypertension_grade_3", 0),
        )

    def _classify_bp(self, sbp: int, dbp: int) -> str:
        """Ordnet eine Einzelmessung einer ESH/ESC-Kategorie zu.

        Grenzen nach Mancia et al., „2023 ESH Guidelines for the Management
        of Arterial Hypertension". Bei unterschiedlichen Kategorien für
        SBP und DBP gilt die höhere. Hypotonie ist in der ESH-Leitlinie
        nicht ausdrücklich definiert; die Schwelle SBP < 90 mmHg bzw.
        DBP < 60 mmHg folgt der verbreiteten WHO-Konvention.
        """
        if sbp < 90 or dbp < 60:
            return "hypotension"
        if sbp >= 180 or dbp >= 110:
            return "hypertension_grade_3"
        if sbp >= 160 or dbp >= 100:
            return "hypertension_grade_2"
        if sbp >= 140 or dbp >= 90:
            return "hypertension_grade_1"
        if sbp >= 130 or dbp >= 85:
            return "high_normal"
        if sbp >= 120 or dbp >= 80:
            return "normal"
        return "optimal"

    def _build_section(self, raw_summary: dict | None, key: str, measurements: list[dict]) -> SummarySection:
        source = (raw_summary or {}).get(key) or {}
        # If the PDF table provided at least the systolic mean, trust it for
        # all reference values that exist there. Missing DBP/HR fields are
        # backfilled from the actual measurements so the dashboard never has
        # to deal with partial sections.
        from_pdf = SummarySection.model_validate(source) if source else SummarySection()
        has_any_pdf_value = any(
            getattr(from_pdf, metric) is not None
            for metric in ("mean", "sd", "max", "min", "measurements")
        )

        sbp_values = [item["systolic"] for item in measurements]
        dbp_values = [item["diastolic"] for item in measurements]
        hr_values = [item["heart_rate"] for item in measurements]

        derived = SummarySection()
        if sbp_values:
            derived = SummarySection(
                mean=round(mean(sbp_values), 2),
                sd=round(pstdev(sbp_values), 2) if len(sbp_values) > 1 else 0.0,
                max=max(sbp_values),
                min=min(sbp_values),
                measurements=len(sbp_values),
                mean_diastolic=round(mean(dbp_values), 2) if dbp_values else None,
                mean_heart_rate=round(mean(hr_values), 2) if hr_values else None,
                min_diastolic=min(dbp_values) if dbp_values else None,
                min_heart_rate=min(hr_values) if hr_values else None,
                max_diastolic=max(dbp_values) if dbp_values else None,
                max_heart_rate=max(hr_values) if hr_values else None,
            )

        if not has_any_pdf_value:
            return derived

        # Merge: PDF wins when it has a value, derived fills the gaps.
        merged = derived.model_dump()
        pdf_dump = from_pdf.model_dump()
        for field_name, value in pdf_dump.items():
            if value is not None:
                merged[field_name] = value
        return SummarySection.model_validate(merged)

    def _build_warnings(self, measurements: list[dict], report: dict, raw_summary: dict | None) -> list[str]:
        warnings: list[str] = []
        if not measurements:
            warnings.append("No measurements were imported.")

        unknown_count = sum(1 for item in measurements if item["measurement_type"] == "unknown")
        if unknown_count:
            warnings.append("measurement_type could not be reliably determined for all rows")

        if report.get("report_month") is None or report.get("report_year") is None:
            warnings.append("Report month/year could not be derived and were inferred only partially.")

        if raw_summary is not None and not any((raw_summary.get(section) or {}).get("measurements") for section in ("day_rest", "night", "all_measurements")):
            warnings.append("Summary blocks were missing in the source and were recalculated from measurements.")

        return warnings

    def _report_text_for_age(self, report: dict) -> str | None:
        month = report.get("report_month")
        year = report.get("report_year")
        if month is None or year is None:
            return None
        month_names = {
            1: "Januar",
            2: "Februar",
            3: "Maerz",
            4: "April",
            5: "Mai",
            6: "Juni",
            7: "Juli",
            8: "August",
            9: "September",
            10: "Oktober",
            11: "November",
            12: "Dezember",
        }
        return f"Monatsbericht {month_names.get(month, month)} {year}"

    def _is_day_measurement(self, iso_datetime: str) -> bool:
        hour = datetime.fromisoformat(iso_datetime).hour
        return 6 <= hour < 22

    def _generate_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid4().hex[:12]}"

    def _to_float(self, value: object) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
