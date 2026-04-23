from app.services.hilo.extractor import extract_measurement_rows, extract_patient_data, extract_summary


PAGE1_TEXT = """
Name: Max Mustermann
E-Mail: max@example.org
Geschlecht: mÃ¤nnlich
Geburtsdatum: 08.03.1985
GrÃ¶ÃŸe: 178 cm
Gewicht: 80,5 kg
Hilo Monatsbericht MÃ¤rz 2026
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


def test_extract_patient_data_with_native_umlauts() -> None:
    text = """
    Name: Erika Beispiel
    Geschlecht: weiblich
    Geburtsdatum: 01.02.1990
    Größe: 170 cm
    Gewicht: 65,5 kg
    """
    patient = extract_patient_data(text)
    assert patient["gender"] == "weiblich"
    assert patient["height_cm"] == "170"
    assert patient["weight_kg"] == "65,5"


def test_extract_summary() -> None:
    summary = extract_summary(PAGE1_TEXT)
    assert summary["day_rest"]["mean"] == 124.0
    assert summary["night"]["measurements"] == 28


def test_extract_measurement_rows() -> None:
    rows = extract_measurement_rows(PAGE2_TEXT)
    assert len(rows) == 2
    assert rows[0].systolic == 123
    assert rows[1].diastolic == 75


def test_extract_summary_tabular_measurements_with_triplets() -> None:
    text = """
    Übersichtstabelle
    Mittelwert
    129
    74
    68
    117
    64
    58
    127
    73
    66
    Messungen
    438
    438
    438
    92
    92
    92
    530
    530
    530
    """
    summary = extract_summary(text)
    assert summary["day_rest"]["measurements"] == 438
    assert summary["night"]["measurements"] == 92
    assert summary["all_measurements"]["measurements"] == 530


def test_extract_summary_tabular_includes_sys_dia_min_max_mean() -> None:
    text = """
    Übersichtstabelle
    Mittelwert
    129
    74
    68
    117
    64
    58
    127
    73
    66
    Max
    143
    86
    97
    136
    77
    78
    143
    86
    97
    Mindest
    106
    56
    46
    103
    54
    48
    103
    54
    46
    """
    summary = extract_summary(text)
    assert summary["day_rest"]["mean"] == 129.0
    assert summary["day_rest"]["mean_diastolic"] == 74.0
    assert summary["night"]["max"] == 136.0
    assert summary["night"]["max_diastolic"] == 77.0
    assert summary["all_measurements"]["min"] == 103.0
    assert summary["all_measurements"]["min_diastolic"] == 54.0


def test_extract_summary_tabular_accepts_minimalwert_label() -> None:
    text = """
    Übersichtstabelle
    Minimalwert
    106
    56
    46
    103
    54
    48
    103
    54
    46
    """
    summary = extract_summary(text)
    assert summary["day_rest"]["min"] == 106.0
    assert summary["night"]["min_diastolic"] == 54.0
