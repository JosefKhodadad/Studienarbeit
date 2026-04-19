from __future__ import annotations

from pathlib import Path
from typing import Any

from .extractor import extract_measurement_rows, extract_page_images, extract_patient_data, extract_summary
from .icon_detection import IconDetector
from .mapper import measurement_datetime, to_patient_model, to_report_model


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
        first_page = doc[0]
        first_text = first_page.get_text("text")

        patient = to_patient_model(extract_patient_data(first_text), first_text)
        report = to_report_model(first_text)
        summary = extract_summary(first_text)

        self._train_icon_detector(doc)
        measurements = self._extract_measurements(doc)

        return {
            "patient": patient,
            "report": report,
            "summary": summary,
            "measurements": measurements,
        }

    def _train_icon_detector(self, doc: Any) -> None:
        if len(doc) < 18:
            return
        legend_page = doc[17]
        legend_images = extract_page_images(legend_page)
        # Heuristik: die ersten drei Legenden-Icons repräsentieren Kalibrierung, Manschette, Telefon.
        mapping = {}
        legend_keys = ["kalibrierung", "manschette", "telefon"]
        for key, image_meta in zip(legend_keys, legend_images):
            mapping[key] = image_meta
        self.icon_detector.learn_legend(mapping)

    def _extract_measurements(self, doc: Any) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        upper = min(len(doc), 17)
        for page_index in range(1, upper):  # Seiten 2-17
            page = doc[page_index]
            page_text = page.get_text("text")
            rows = extract_measurement_rows(page_text)
            row_icons = extract_page_images(page)

            for row in rows:
                image_meta = row_icons[row.row_index_on_page - 1] if len(row_icons) >= row.row_index_on_page else None
                results.append(
                    {
                        "datetime": measurement_datetime(row.date, row.time),
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
