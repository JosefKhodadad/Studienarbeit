from __future__ import annotations

import math

import numpy as np

from app.services.ml.feature_engineering import (
    FEATURE_NAMES,
    build_features,
    standardize,
)


def _measurement(dt: str, sbp: int, dbp: int, hr: int, m_type: str = "armband") -> dict:
    return {
        "datetime": dt,
        "systolic": sbp,
        "diastolic": dbp,
        "heart_rate": hr,
        "measurement_type": m_type,
    }


def test_build_features_returns_zero_size_for_empty_input() -> None:
    fm = build_features([])
    assert fm.matrix.shape == (0, len(FEATURE_NAMES))
    assert fm.weak_labels.size == 0
    assert fm.timestamps == []


def test_build_features_weak_label_rule_marks_night_hours() -> None:
    measurements = [
        _measurement("2026-04-01T03:00:00", 110, 70, 60),  # Nacht
        _measurement("2026-04-01T14:00:00", 130, 85, 75),  # Tag
    ]
    fm = build_features(measurements)
    assert fm.weak_labels[0] == 1.0
    assert fm.weak_labels[1] == 0.0


def test_build_features_phone_forces_day_label() -> None:
    # 03:00 Telefonmessung: trotz Nachtzeit als Tag-Label, da Hilo-App
    # Telefonmessungen nur tagsueber zulaesst.
    measurements = [_measurement("2026-04-01T03:00:00", 130, 85, 80, m_type="phone_measurement")]
    fm = build_features(measurements)
    assert fm.weak_labels[0] == 0.0
    phone_idx = FEATURE_NAMES.index("is_phone_measurement")
    assert fm.matrix[0, phone_idx] == 1.0


def test_build_features_keeps_input_order_after_internal_sort() -> None:
    # Eingabe ist absichtlich UN-sortiert; das Ergebnis muss in der
    # gegebenen Reihenfolge bleiben.
    measurements = [
        _measurement("2026-04-02T10:00:00", 130, 80, 70),
        _measurement("2026-04-01T22:00:00", 110, 70, 60),
    ]
    fm = build_features(measurements)
    # Erste Zeile entspricht der ersten Eingabemessung (Tag), zweite Nacht.
    assert fm.weak_labels[0] == 0.0
    assert fm.weak_labels[1] == 1.0
    assert fm.timestamps[0].hour == 10


def test_standardize_zscores_only_non_protected_columns() -> None:
    measurements = [
        _measurement(f"2026-04-{day:02d}T12:00:00", 120 + day, 80, 70 + day)
        for day in range(1, 6)
    ]
    fm = build_features(measurements)
    scaled, stats = standardize(fm.matrix)

    sin_hour_idx = FEATURE_NAMES.index("sin_hour")
    sbp_idx = FEATURE_NAMES.index("sbp")

    # Geschuetzte Spalte: identisch zur Eingabe.
    np.testing.assert_allclose(scaled[:, sin_hour_idx], fm.matrix[:, sin_hour_idx])
    # Standardisierte Spalte: Mittelwert ~0, Standardabweichung ~1.
    assert abs(float(scaled[:, sbp_idx].mean())) < 1e-9
    assert math.isclose(float(scaled[:, sbp_idx].std()), 1.0, rel_tol=1e-6)

    # Re-Standardisieren mit den vorhandenen Stats reproduziert das Ergebnis.
    scaled_again, _ = standardize(fm.matrix, stats=stats)
    np.testing.assert_allclose(scaled_again, scaled)
