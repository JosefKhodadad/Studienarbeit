from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass
class MeasurementRow:
    date: str
    time: str
    systolic: int
    diastolic: int
    heart_rate: int
    source_column: str
    row_index_on_page: int


def extract_patient_data(page_text: str) -> dict[str, str | None]:
    patterns = {
        "full_name": r"Name\s*:?\s*(.+)",
        "email": r"E-?Mail\s*:?\s*([\w.+\-]+@[\w.\-]+)",
        "gender": r"Geschlecht\s*:?\s*([A-Za-zäöüÄÖÜß]+)",
        "birth_date": r"Geburtsdatum\s*:?\s*(\d{1,2}\.\d{1,2}\.\d{2,4})",
        "height_cm": r"Gr(?:ö|oe)ße\s*:?\s*(\d{2,3})\s*cm",
        "weight_kg": r"Gewicht\s*:?\s*(\d{2,3}(?:[,.]\d+)?)\s*kg",
    }
    parsed: dict[str, str | None] = {}
    for field, pattern in patterns.items():
        match = re.search(pattern, page_text, re.IGNORECASE)
        parsed[field] = match.group(1).strip() if match else None
    return parsed


def extract_summary(page_text: str) -> dict[str, dict[str, float | int | None]]:
    summary: dict[str, dict[str, float | int | None]] = {
        "day_rest": _extract_summary_line(page_text, ["Tag", "Ruhe"]),
        "night": _extract_summary_line(page_text, ["Nacht"]),
        "all_measurements": _extract_summary_line(page_text, ["Alle", "Messungen"]),
    }
    return summary


def _extract_summary_line(page_text: str, labels: list[str]) -> dict[str, float | int | None]:
    numbers = {
        "mean": None,
        "sd": None,
        "max": None,
        "min": None,
        "measurements": None,
    }
    label_pattern = r".*".join(labels)
    line_match = re.search(rf"{label_pattern}[^\n]*", page_text, re.IGNORECASE)
    if not line_match:
        return numbers

    values = re.findall(r"\d+(?:[.,]\d+)?", line_match.group(0))
    if len(values) >= 5:
        numbers["mean"] = float(values[0].replace(",", "."))
        numbers["sd"] = float(values[1].replace(",", "."))
        numbers["max"] = float(values[2].replace(",", "."))
        numbers["min"] = float(values[3].replace(",", "."))
        numbers["measurements"] = int(float(values[4].replace(",", ".")))
    return numbers


def extract_measurement_rows(page_text: str) -> list[MeasurementRow]:
    rows: list[MeasurementRow] = []
    pattern = re.compile(
        r"(?P<date>\d{2}\.\d{2}\.\d{4})\s+"
        r"(?P<time>\d{2}:\d{2})\s+"
        r"(?P<sbp>\d{2,3})\s+"
        r"(?P<dbp>\d{2,3})\s+"
        r"(?P<hr>\d{2,3})"
    )
    for idx, match in enumerate(pattern.finditer(page_text), start=1):
        rows.append(
            MeasurementRow(
                date=match.group("date"),
                time=match.group("time"),
                systolic=int(match.group("sbp")),
                diastolic=int(match.group("dbp")),
                heart_rate=int(match.group("hr")),
                source_column=_guess_column(match.start()),
                row_index_on_page=idx,
            )
        )
    return rows


def _guess_column(char_offset: int) -> str:
    if char_offset < 2500:
        return "left"
    if char_offset < 5000:
        return "middle"
    return "right"


def extract_page_images(page: Any) -> list[dict[str, Any]]:
    images = []
    for image in page.get_images(full=True):
        xref = image[0]
        base = page.parent.extract_image(xref)
        images.append(base)
    return images
