from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .extractor import extract_measurement_rows, extract_page_images, extract_patient_data, extract_summary
from .icon_detection import IconDetector
from .mapper import measurement_datetime, to_patient_model, to_report_model


logger = logging.getLogger(__name__)


class HiloPDFParser:
    def __init__(self) -> None:
        self.icon_detector = IconDetector()

    def parse_file(self, file_path: str | Path) -> dict[str, Any]:
        try:
            import fitz  # type: ignore
        except ModuleNotFoundError as exc:
            raise RuntimeError("PyMuPDF (fitz) ist nicht installiert") from exc

        with fitz.open(str(file_path)) as doc:
            return self._parse_document(doc)

    def parse_bytes(self, content: bytes) -> dict[str, Any]:
        try:
            import fitz  # type: ignore
        except ModuleNotFoundError as exc:
            raise RuntimeError("PyMuPDF (fitz) ist nicht installiert") from exc

        with fitz.open(stream=content, filetype="pdf") as doc:
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

        self._train_icon_detector(doc)
        measurements = self._extract_measurements(doc)

        logger.info(
            "Hilo parse complete: pages=%d, measurements=%d, patient=%s, period=%s-%s",
            len(doc),
            len(measurements),
            patient.get("full_name"),
            report.get("report_month"),
            report.get("report_year"),
        )

        return {
            "patient": patient,
            "report": report,
            "summary": summary,
            "measurements": measurements,
        }

    def _train_icon_detector(self, doc: Any) -> None:
        """Best-effort legend training.

        Hilo PDFs historically bundled a legend on the last page.  The current
        export reuses the same template icons on every page, so training from
        the last page produces bogus matches.  We keep the detector in a
        cleared state unless a dedicated legend page is identified later; this
        preserves the documented ``unknown`` fallback instead of misclassifying
        rows.
        """
        self.icon_detector = IconDetector()

    def _extract_measurements(self, doc: Any) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        total_pages = len(doc)
        # Hilo PDFs end with a marketing page; measurement pages are 2..N-1.
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
