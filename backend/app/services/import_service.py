from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from app.models.blood_pressure import (
    ENTRY_STATUS_EDITED,
    ENTRY_STATUS_MANUAL,
    ENTRY_STATUS_ORIGINAL,
    ENTRY_STATUS_RESTORED,
)
from app.schemas.hilo_import import (
    DirectImportRequest,
    HiloImportResponse,
    MeasurementCreateRequest,
    MeasurementUpdateRequest,
    PatientProfileResponse,
    ReportListItem,
    ReportListResponse,
    UnifiedMeasurementsResponse,
)
from app.services.aggregation_service import VALID_MEASUREMENT_TYPES, AggregationService
from app.services.classification_service import NightDayClassifier
from app.services.ml.feature_engineering import NightDayReference
from app.services.ml.sleep_phase import detect_sleep_episodes, expected_sleep_range
from app.services.parser_service import HiloParserService
from app.services.repository import InMemoryImportRepository


# Strukturelle Typannotation: Wir akzeptieren jeden Klassifikator mit
# kompatibler classify(...)-Signatur (NightDayClassifier oder
# KerasNightClassifier-Adapter).
class ImportService:
    def __init__(
        self,
        repository: InMemoryImportRepository,
        parser_service: HiloParserService,
        aggregation_service: AggregationService,
        classifier: object | None = None,
    ) -> None:
        self._repository = repository
        self._parser_service = parser_service
        self._aggregation_service = aggregation_service
        self._classifier = classifier or NightDayClassifier()

    # --- PDF import -----------------------------------------------------

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

    # --- dashboard reads ------------------------------------------------

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

    # --- multi-report management ---------------------------------------

    def list_reports(self) -> ReportListResponse:
        items = [ReportListItem.model_validate(item) for item in self._repository.list_reports()]
        return ReportListResponse(
            reports=items,
            current_report_id=self._repository.current_report_id,
        )

    def delete_report(self, report_id: str) -> ReportListResponse:
        try:
            self._repository.delete_report(report_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Report not found: {report_id}") from exc
        return self.list_reports()

    def activate_report(self, report_id: str) -> HiloImportResponse:
        try:
            payload = self._repository.set_current_report(report_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Report not found: {report_id}") from exc
        return HiloImportResponse.model_validate(payload)

    def get_unified_measurements(self) -> UnifiedMeasurementsResponse:
        from datetime import datetime as _dt

        payloads = list(self._repository.iter_all_reports())
        measurements: list[dict] = []
        report_items: list[ReportListItem] = []

        # Report items mirror list_reports() ordering (newest first).
        for item in self._repository.list_reports():
            report_items.append(ReportListItem.model_validate(item))

        methods_seen: set[str] = set()
        sleep_episodes: list[dict] = []
        latest_age: float | None = None

        # Cross-Report-Aggregation: Mittelwerte aus allen PDF-Übersichten
        # einsammeln, damit das Dashboard nicht nur den aktuellen Bericht,
        # sondern den gesamten importierten Datenbestand zeigen kann.
        cross_avg = _CrossReportAggregator()

        for payload in payloads:
            report = payload.get("report", {}) or {}
            report_id = report.get("id") or ""
            raw_measurements = list(payload.get("measurements") or [])
            summary = payload.get("summary", {}) or {}
            expected_night = summary.get("measurement_count_night")
            patient = payload.get("patient", {}) or {}
            age_years = patient.get("age_at_report_date")
            if age_years is not None:
                try:
                    latest_age = float(age_years)
                except (TypeError, ValueError):
                    pass

            reference = _build_reference_from_summary(summary)
            cross_avg.absorb(summary)

            classifications = self._classify(
                raw_measurements,
                expected_night_count=int(expected_night) if expected_night else None,
                age_years=float(age_years) if age_years is not None else None,
                reference=reference,
            )

            # Outlier-Maske aus dem Feature-Engineering ziehen, damit der
            # Sleep-Phase-Detektor Aufwach-Phasen erkennen kann.
            outlier_mask = _compute_outlier_mask(raw_measurements, reference)

            timestamps: list[_dt] = []
            is_night_arr: list[bool] = []
            ts_outliers: list[float] = []
            for measurement, result in zip(raw_measurements, classifications):
                methods_seen.add(result.method)
                enriched = dict(measurement)
                enriched["report_id"] = report_id
                enriched["report_month"] = report.get("report_month")
                enriched["report_year"] = report.get("report_year")
                enriched["is_night"] = result.is_night
                enriched["classification_score"] = result.score
                enriched["classification_method"] = result.method
                measurements.append(enriched)
                try:
                    parsed_ts = _dt.fromisoformat(measurement["datetime"])
                    # Doppelte Absicherung gegen vermischte Datentypen:
                    # Sleep-Phase-Detektor vergleicht Zeitstempel paarweise
                    # und scheitert an Mix aus naive und tz-aware.
                    if parsed_ts.tzinfo is not None:
                        parsed_ts = parsed_ts.astimezone().replace(tzinfo=None)
                    timestamps.append(parsed_ts)
                    is_night_arr.append(bool(result.is_night))
                except (KeyError, ValueError):
                    continue
                idx = len(timestamps) - 1
                ts_outliers.append(float(outlier_mask[idx]) if idx < len(outlier_mask) else 0.0)

            if timestamps:
                import numpy as _np

                episodes = detect_sleep_episodes(
                    timestamps,
                    _np.asarray(is_night_arr, dtype=bool),
                    age_years=float(age_years) if age_years is not None else None,
                    night_ref_outlier=_np.asarray(ts_outliers, dtype=_np.float64),
                )
                for ep in episodes:
                    payload_ep = ep.to_dict()
                    payload_ep["report_id"] = report_id
                    sleep_episodes.append(payload_ep)

        measurements.sort(key=lambda item: item.get("datetime") or "")

        method_label = (
            ", ".join(sorted(methods_seen)) if methods_seen else "heuristic"
        )

        expected_range = expected_sleep_range(latest_age) if measurements else None

        return UnifiedMeasurementsResponse.model_validate(
            {
                "measurements": measurements,
                "reports": [item.model_dump() for item in report_items],
                "classification_method": method_label,
                "warnings": [],
                "sleep_episodes": sleep_episodes,
                "expected_sleep_hours": expected_range,
                "cross_report_averages": cross_avg.as_dict(),
            }
        )

    def _classify(
        self,
        measurements: list[dict],
        *,
        expected_night_count: int | None,
        age_years: float | None,
        reference: NightDayReference | None = None,
    ):
        # Bevorzugt die volle Signatur (KerasNightClassifier mit reference),
        # fällt sonst auf age_years-only und schließlich die klassische
        # Signatur (NightDayClassifier) zurück. So bleibt der Service mit
        # allen drei Klassifikator-Varianten kompatibel.
        try:
            return self._classifier.classify(
                measurements,
                expected_night_count=expected_night_count,
                age_years=age_years,
                reference=reference,
            )
        except TypeError:
            pass
        try:
            return self._classifier.classify(
                measurements,
                expected_night_count=expected_night_count,
                age_years=age_years,
            )
        except TypeError:
            return self._classifier.classify(
                measurements,
                expected_night_count=expected_night_count,
            )

    # --- measurement CRUD ----------------------------------------------

    def create_measurement(self, request: MeasurementCreateRequest) -> HiloImportResponse:
        self._validate_measurement_type(request.measurement_type)
        payload = self._get_or_bootstrap_payload()
        measurements = payload.get("measurements") or []

        new_entry = {
            "entry_id": f"entry_{uuid4().hex[:12]}",
            "datetime": _validate_iso_datetime(request.datetime),
            "systolic": int(request.systolic),
            "diastolic": int(request.diastolic),
            "heart_rate": int(request.heart_rate),
            "measurement_type": request.measurement_type,
            "source": "manual",
            "source_file": None,
            "source_page": 0,
            "source_column": "manual",
            "row_index_on_page": 0,
            "import_batch_id": None,
            "entry_status": ENTRY_STATUS_MANUAL,
            "original_values": None,
            "edited_at": None,
        }
        measurements.append(new_entry)
        return self._persist_measurements(payload, measurements)

    def update_measurement(self, entry_id: str, request: MeasurementUpdateRequest) -> HiloImportResponse:
        if request.measurement_type is not None:
            self._validate_measurement_type(request.measurement_type)

        payload = self._require_payload()
        measurements = payload.get("measurements") or []
        entry = self._find_entry(measurements, entry_id)

        if request.datetime is not None:
            entry["datetime"] = _validate_iso_datetime(request.datetime)
        if request.systolic is not None:
            entry["systolic"] = int(request.systolic)
        if request.diastolic is not None:
            entry["diastolic"] = int(request.diastolic)
        if request.heart_rate is not None:
            entry["heart_rate"] = int(request.heart_rate)
        if request.measurement_type is not None:
            entry["measurement_type"] = request.measurement_type

        entry["entry_status"] = self._derive_status_after_edit(entry)
        entry["edited_at"] = datetime.utcnow().isoformat(timespec="seconds")

        return self._persist_measurements(payload, measurements)

    def delete_measurement(self, entry_id: str) -> HiloImportResponse:
        payload = self._require_payload()
        measurements = payload.get("measurements") or []
        remaining = [m for m in measurements if m.get("entry_id") != entry_id]
        if len(remaining) == len(measurements):
            raise HTTPException(status_code=404, detail=f"Measurement not found: {entry_id}")
        return self._persist_measurements(payload, remaining)

    # --- internal helpers ----------------------------------------------

    def _store_payload(self, payload: HiloImportResponse) -> HiloImportResponse:
        saved = self._repository.save_dashboard_payload(payload.model_dump())
        return HiloImportResponse.model_validate(saved)

    def _require_payload(self) -> dict:
        try:
            return self._repository.get_current_dashboard_payload()
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail="No imported report is available yet. Upload a PDF or create a manual entry first.",
            ) from exc

    def _get_or_bootstrap_payload(self) -> dict:
        try:
            return self._repository.get_current_dashboard_payload()
        except KeyError:
            bootstrap = self._aggregation_service.rebuild_payload(
                patient={
                    "id": f"patient_{uuid4().hex[:12]}",
                    "full_name": None,
                    "email": None,
                    "gender": None,
                    "birth_date": None,
                    "age_at_report_date": None,
                    "height_cm": None,
                    "weight_kg": None,
                },
                report={
                    "id": f"report_{uuid4().hex[:12]}",
                    "report_month": None,
                    "report_year": None,
                    "source": "manual_only",
                    "file_name": None,
                },
                measurements=[],
                raw_summary=None,
            )
            self._repository.save_dashboard_payload(bootstrap.model_dump())
            return self._repository.get_current_dashboard_payload()

    def _persist_measurements(self, payload: dict, measurements: list[dict]) -> HiloImportResponse:
        rebuilt = self._aggregation_service.rebuild_payload(
            patient=payload["patient"],
            report=payload["report"],
            measurements=measurements,
            raw_summary=None,
        )
        saved = self._repository.replace_current_payload(rebuilt.model_dump())
        return HiloImportResponse.model_validate(saved)

    def _find_entry(self, measurements: list[dict], entry_id: str) -> dict:
        for entry in measurements:
            if entry.get("entry_id") == entry_id:
                return entry
        raise HTTPException(status_code=404, detail=f"Measurement not found: {entry_id}")

    def _validate_measurement_type(self, measurement_type: str) -> None:
        if measurement_type not in VALID_MEASUREMENT_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid measurement_type: {measurement_type}. "
                f"Allowed: {', '.join(VALID_MEASUREMENT_TYPES)}",
            )

    def _derive_status_after_edit(self, entry: dict) -> str:
        original = entry.get("original_values")
        if not original:
            return ENTRY_STATUS_MANUAL

        current_values = {
            "datetime": entry["datetime"],
            "systolic": entry["systolic"],
            "diastolic": entry["diastolic"],
            "heart_rate": entry["heart_rate"],
            "measurement_type": entry["measurement_type"],
        }
        if _values_equal(original, current_values):
            # Edited back to the imported baseline.
            return (
                ENTRY_STATUS_ORIGINAL
                if entry.get("entry_status") == ENTRY_STATUS_ORIGINAL
                else ENTRY_STATUS_RESTORED
            )
        return ENTRY_STATUS_EDITED


def _build_reference_from_summary(summary: dict | None) -> NightDayReference | None:
    """Erzeuge eine ``NightDayReference`` aus dem ``summary``-Block.

    ``summary["night"]`` und ``summary["day_rest"]`` liefern Mittelwerte für
    SBP, DBP und HR (jeweils ``None``-fähig). Liegen die nötigen Felder vor,
    bauen wir die Referenz für das Modell — sonst ``None``, dann lernt das
    Modell ohne Referenz weiter (rein aus den klassischen Features).
    """

    if not summary:
        return None
    night = summary.get("night") or {}
    day = summary.get("day_rest") or {}

    def _f(value: object) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    night_sbp = _f(night.get("mean"))
    night_dbp = _f(night.get("mean_diastolic"))
    night_hr = _f(night.get("mean_heart_rate"))
    if night_sbp is None and night_dbp is None and night_hr is None:
        return None
    return NightDayReference(
        night_sbp_mean=night_sbp,
        night_dbp_mean=night_dbp,
        night_hr_mean=night_hr,
        night_sbp_min=_f(night.get("min")),
        night_sbp_max=_f(night.get("max")),
        night_dbp_min=_f(night.get("min_diastolic")),
        night_hr_min=_f(night.get("min_heart_rate")),
        day_sbp_mean=_f(day.get("mean")),
        day_dbp_mean=_f(day.get("mean_diastolic")),
        day_hr_mean=_f(day.get("mean_heart_rate")),
    )


def _compute_outlier_mask(measurements: list[dict], reference: NightDayReference | None) -> list[float]:
    """Berechne pro Messung ein 0/1-Outlier-Flag gegen die Nacht-Referenz.

    Wir greifen dafür auf das vorhandene Feature-Engineering zurück, damit
    die Logik (3 % Toleranz, ``OUTLIER_TOLERANCE_FACTOR × Toleranz``)
    konsistent zwischen Modell-Input und Sleep-Phase-Detektor bleibt.
    """

    if not measurements or reference is None or not reference.has_night_means():
        return [0.0] * len(measurements)
    from app.services.ml.feature_engineering import build_features

    fm = build_features(measurements, reference=reference)
    return [float(value) for value in fm.night_ref_outlier.tolist()]


class _CrossReportAggregator:
    """Sammelt Nacht-/Tag-Mittelwerte aus PDF-Übersichten über alle Berichte.

    Die Aggregation gewichtet pro Bericht mit dessen Messanzahl, damit ein
    Report mit 50 Nachtmessungen mehr zählt als einer mit 5. Fehlende Felder
    werden konsequent ignoriert, statt mit 0 ein Mittel zu verfälschen.
    """

    def __init__(self) -> None:
        self._buckets: dict[str, list[tuple[float, int]]] = {
            "night_systolic": [],
            "night_diastolic": [],
            "night_heart_rate": [],
            "day_rest_systolic": [],
            "day_rest_diastolic": [],
            "day_rest_heart_rate": [],
        }
        self._reports = 0

    def absorb(self, summary: dict | None) -> None:
        if not summary:
            return
        self._reports += 1
        night = summary.get("night") or {}
        day = summary.get("day_rest") or {}
        n_night = int(night.get("measurements") or 0) or 1
        n_day = int(day.get("measurements") or 0) or 1
        self._add("night_systolic", night.get("mean"), n_night)
        self._add("night_diastolic", night.get("mean_diastolic"), n_night)
        self._add("night_heart_rate", night.get("mean_heart_rate"), n_night)
        self._add("day_rest_systolic", day.get("mean"), n_day)
        self._add("day_rest_diastolic", day.get("mean_diastolic"), n_day)
        self._add("day_rest_heart_rate", day.get("mean_heart_rate"), n_day)

    def _add(self, key: str, value: object, weight: int) -> None:
        if value is None or weight <= 0:
            return
        try:
            self._buckets[key].append((float(value), int(weight)))
        except (TypeError, ValueError):
            return

    def as_dict(self) -> dict:
        result: dict[str, float | int | None] = {"report_count": self._reports}
        for key, samples in self._buckets.items():
            if not samples:
                result[key] = None
                continue
            total_w = sum(w for _, w in samples)
            if total_w <= 0:
                result[key] = None
                continue
            weighted_sum = sum(v * w for v, w in samples)
            result[key] = round(weighted_sum / total_w, 2)
        return result


def _validate_iso_datetime(value: str) -> str:
    """Normalisiert eingehende Datetimes auf naive lokale Zeit.

    PDF-geparste Messungen sind naiv (lokale Uhrzeit ohne tzinfo), während
    das Frontend nach einem Edit ``new Date(...).toISOString()`` mit ``Z``
    sendet (tz-aware UTC). Würde der Server beides vermischt speichern,
    werfen ``feature_engineering.build_features`` und
    ``sleep_phase.detect_sleep_episodes`` beim späteren Sortieren bzw.
    Subtrahieren ``TypeError`` — die Sleep-Banden im Unified View
    verschwinden dann stillschweigend.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid ISO datetime: {value}") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed.isoformat()


def _values_equal(original: dict, current: dict) -> bool:
    # Compare on the absolute instant. The PDF parser stores naive local
    # datetimes while edits coming from the frontend arrive as tz-aware UTC
    # (Date.toISOString()). A plain string/datetime comparison would always
    # differ even when the values represent the same moment, so normalize
    # both sides to a Unix timestamp first (naive is treated as local time).
    original_ts = _to_timestamp(original.get("datetime"))
    current_ts = _to_timestamp(current.get("datetime"))
    if original_ts is None or current_ts is None or original_ts != current_ts:
        return False
    return (
        _as_int(original.get("systolic")) == _as_int(current.get("systolic"))
        and _as_int(original.get("diastolic")) == _as_int(current.get("diastolic"))
        and _as_int(original.get("heart_rate")) == _as_int(current.get("heart_rate"))
        and str(original.get("measurement_type")) == str(current.get("measurement_type"))
    )


def _to_timestamp(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # astimezone() on a naive datetime interprets it as local system time
        # and returns a tz-aware datetime in the local zone.
        try:
            dt = dt.astimezone()
        except (OSError, ValueError):
            dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
