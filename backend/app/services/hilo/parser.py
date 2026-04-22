from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from .extractor import extract_measurement_rows, extract_page_images, extract_patient_data, extract_summary
from .icon_detection import IconDetector
from .mapper import measurement_datetime, to_patient_model, to_report_model
from .pdfplumber_extractor import DEFAULT_TYPE, ExtractedMeasurement, extract_measurements as plumber_extract


logger = logging.getLogger(__name__)


class HiloPDFParser:
    """Combines a fitz (PyMuPDF) text pass for patient/report/summary with a
    pdfplumber pass for measurement rows and icon-based type detection.

    pdfplumber gives us word/image bounding boxes, which lets us reproduce the
    reference script's positional logic instead of falling back to ``unknown``
    for every row.
    """

    def __init__(self) -> None:
        self.icon_detector = IconDetector()

    def parse_file(self, file_path: str | Path) -> dict[str, Any]:
        text_result = self._parse_text_layer(source=str(file_path))
        measurements = self._extract_measurements_positional(pdf_source=str(file_path))
        return self._merge_result(text_result, measurements)

    def parse_bytes(self, content: bytes) -> dict[str, Any]:
        text_result = self._parse_text_layer(content=content)
        measurements = self._extract_measurements_positional(pdf_source=content)
        return self._merge_result(text_result, measurements)

    # --- internal helpers ------------------------------------------------

    def _parse_text_layer(self, *, source: str | None = None, content: bytes | None = None) -> dict[str, Any]:
        """Extract patient/report/summary (and fallback measurements) via PyMuPDF."""
        try:
            import fitz  # type: ignore
        except ModuleNotFoundError as exc:
            raise RuntimeError("PyMuPDF (fitz) ist nicht installiert") from exc

        doc_ctx = fitz.open(source) if source else fitz.open(stream=content, filetype="pdf")
        with doc_ctx as doc:
            return self._parse_document(doc)

    def _parse_document(self, doc: Any) -> dict[str, Any]:
        if len(doc) == 0:
            logger.warning("Hilo PDF document is empty")
            return {"patient": {}, "report": {}, "summary": {}, "measurements": []}

        first_page = doc[0]
        first_text = first_page.get_text("text")

        patient = to_patient_model(extract_patient_data(first_text), first_text)
        report = to_report_model(first_text)
        summary = extract_summary(first_text)

        fallback_measurements = self._fallback_measurements_from_text(doc)

        logger.info(
            "Hilo text pass complete: pages=%d, fallback_measurements=%d, patient=%s, period=%s-%s",
            len(doc),
            len(fallback_measurements),
            patient.get("full_name"),
            report.get("report_month"),
            report.get("report_year"),
        )

        return {
            "patient": patient,
            "report": report,
            "summary": summary,
            "measurements": fallback_measurements,
        }

    def _fallback_measurements_from_text(self, doc: Any) -> list[dict[str, Any]]:
        """Text-only measurement extraction kept for the test mocks and as a
        safety net if the pdfplumber pass fails (measurement_type is then
        ``unknown`` as before).
        """
        results: list[dict[str, Any]] = []
        total_pages = len(doc)
        data_end = max(1, total_pages - 1)
        for page_index in range(1, data_end):
            page = doc[page_index]
            page_text = page.get_text("text")
            rows = extract_measurement_rows(page_text)
            row_icons = extract_page_images(page)

            for row in rows:
                image_meta = None
                if 0 < row.row_index_on_page <= len(row_icons):
                    image_meta = row_icons[row.row_index_on_page - 1]
                try:
                    iso_datetime = measurement_datetime(row.date, row.time)
                except ValueError:
                    logger.warning(
                        "Skipping unparseable measurement on page %d: date=%s time=%s",
                        page_index + 1,
                        row.date,
                        row.time,
                    )
                    continue

                results.append(
                    {
                        "datetime": iso_datetime,
                        "systolic": row.systolic,
                        "diastolic": row.diastolic,
                        "heart_rate": row.heart_rate,
                        "measurement_type": self.icon_detector.detect(image_meta),
                        "source_page": page_index + 1,
                        "source_column": row.source_column,
                        "row_index_on_page": row.row_index_on_page,
                    }
                )
        return results

    def _extract_measurements_positional(self, *, pdf_source: str | bytes) -> list[dict[str, Any]]:
        """Use pdfplumber to produce measurements with measurement_type filled.

        Returns an empty list on any failure so the caller can fall back to
        the text-layer measurements (``unknown`` type).
        """
        try:
            records = plumber_extract(pdf_source)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("pdfplumber measurement extraction failed: %s", exc)
            return []

        measurements: list[dict[str, Any]] = []
        for record in records:
            measurements.append(_record_to_dict(record))
        return measurements

    def _merge_result(self, text_result: dict[str, Any], plumber_measurements: list[dict[str, Any]]) -> dict[str, Any]:
        """Prefer pdfplumber rows when available; otherwise keep the text rows.

        Both sets come from the same PDF, so we don't try to mix them: the
        pdfplumber extractor is the authoritative source for measurement rows
        once it yielded data.
        """
        if plumber_measurements:
            logger.info(
                "Using pdfplumber measurements (%d rows)", len(plumber_measurements)
            )
            return {
                "patient": text_result.get("patient", {}),
                "report": text_result.get("report", {}),
                "summary": text_result.get("summary", {}),
                "measurements": plumber_measurements,
            }

        logger.info("Falling back to text-layer measurements (%d rows)", len(text_result.get("measurements", [])))
        return text_result


def _record_to_dict(record: ExtractedMeasurement) -> dict[str, Any]:
    try:
        iso_datetime = datetime.fromisoformat(f"{record.date_iso}T{record.time}:00").isoformat()
    except ValueError:
        iso_datetime = f"{record.date_iso}T{record.time}:00"
    return {
        "datetime": iso_datetime,
        "systolic": record.systolic,
        "diastolic": record.diastolic,
        "heart_rate": record.heart_rate,
        "measurement_type": record.measurement_type or DEFAULT_TYPE,
        "source_page": record.source_page,
        "source_column": record.source_column,
        "row_index_on_page": record.row_index_on_page,
    }
