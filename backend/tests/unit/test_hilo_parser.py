from app.services.hilo.extractor import extract_measurement_rows, extract_patient_data, extract_summary


PAGE1_TEXT = """
Name: Max Mustermann
E-Mail: max@example.org
Geschlecht: männlich
Geburtsdatum: 08.03.1985
Größe: 178 cm
Gewicht: 80,5 kg
Hilo Monatsbericht März 2026
Tag Ruhe 124 8 140 108 32
Nacht 112 7 126 99 28
Alle Messungen 118 9 140 99 60
"""

PAGE2_TEXT = """
01.03.2026 07:30 123 79 68
01.03.2026 21:30 118 75 64
"""


def test_extract_patient_data() -> None:
    patient = extract_patient_data(PAGE1_TEXT)
    assert patient["full_name"] == "Max Mustermann"
    assert patient["email"] == "max@example.org"


def test_extract_summary() -> None:
    summary = extract_summary(PAGE1_TEXT)
    assert summary["day_rest"]["mean"] == 124.0
    assert summary["night"]["measurements"] == 28


def test_extract_measurement_rows() -> None:
    rows = extract_measurement_rows(PAGE2_TEXT)
    assert len(rows) == 2
    assert rows[0].systolic == 123
    assert rows[1].diastolic == 75
