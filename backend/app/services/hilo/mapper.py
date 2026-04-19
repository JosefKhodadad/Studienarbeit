from __future__ import annotations

from datetime import datetime

from .date_parser import calculate_age_at_report, extract_report_period, parse_german_date


def to_patient_model(patient_raw: dict[str, str | None], page_text: str) -> dict:
    period = extract_report_period(page_text)
    birth = parse_german_date(patient_raw.get("birth_date") or "")

    return {
        "full_name": patient_raw.get("full_name"),
        "email": patient_raw.get("email"),
        "gender": patient_raw.get("gender"),
        "birth_date": birth.isoformat() if birth else None,
        "age_at_report_date": calculate_age_at_report(birth, period),
        "height_cm": _to_float(patient_raw.get("height_cm")),
        "weight_kg": _to_float(patient_raw.get("weight_kg")),
    }


def to_report_model(page_text: str) -> dict:
    period = extract_report_period(page_text)
    return {
        "report_month": period.month if period else None,
        "report_year": period.year if period else None,
        "source": "hilo_pdf",
    }


def measurement_datetime(date_str: str, time_str: str) -> str:
    dt = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
    return dt.isoformat()


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None
