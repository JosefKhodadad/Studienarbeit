from __future__ import annotations

from app.services.classification_service import NightDayClassifier


def _base_row(hour: int, minute: int = 0) -> dict:
    ts = f"2026-03-05T{hour:02d}:{minute:02d}:00"
    return {
        "datetime": ts,
        "systolic": 120 - (10 if 22 <= hour or hour < 6 else 0),
        "diastolic": 78 - (5 if 22 <= hour or hour < 6 else 0),
        "heart_rate": 62 if (22 <= hour or hour < 6) else 75,
    }


def test_classifier_returns_empty_for_empty_input() -> None:
    assert NightDayClassifier().classify([]) == []


def test_classifier_respects_expected_night_count() -> None:
    rows = [_base_row(h) for h in (1, 3, 9, 14, 19, 23)]
    result = NightDayClassifier().classify(rows, expected_night_count=3)

    assert sum(1 for r in result if r.is_night) == 3
    # The two well-inside-night rows (01:00, 03:00, 23:00) must be marked night.
    assert result[0].is_night  # 01:00
    assert result[1].is_night  # 03:00
    assert result[-1].is_night  # 23:00
    assert all(r.method.endswith("_constrained") for r in result)


def test_classifier_falls_back_to_heuristic_when_all_weak_labels_are_same() -> None:
    rows = [_base_row(h) for h in (8, 12, 15)]
    result = NightDayClassifier().classify(rows)
    # None of the rows have nighttime hours, so the weak labels collapse.
    assert all(not r.is_night for r in result)
    assert all(r.method == "heuristic" for r in result)


def test_classifier_uses_score_threshold_without_expected_count() -> None:
    rows = [_base_row(h) for h in (1, 14, 23)]
    result = NightDayClassifier().classify(rows)
    methods = {r.method for r in result}
    # Model ran, so the method reflects threshold-based assignment.
    assert methods == {"score_threshold"}
    assert result[0].is_night and result[2].is_night and not result[1].is_night
