from app.services.hilo.date_parser import calculate_age_at_report, extract_report_period, parse_german_date


def test_parse_german_date_supports_common_formats() -> None:
    assert parse_german_date("08.03.1985").isoformat() == "1985-03-08"
    assert parse_german_date("2026-03-01").isoformat() == "2026-03-01"


def test_extract_report_period_handles_umlaut_month() -> None:
    period = extract_report_period("Hilo Blutdruckbericht März 2026")
    assert period is not None
    assert period.month == 3
    assert period.year == 2026


def test_calculate_age_at_report() -> None:
    birth = parse_german_date("08.03.1985")
    period = extract_report_period("Monatsbericht März 2026")
    assert calculate_age_at_report(birth, period) == 41
