from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from app.services.ml.sleep_phase import (
    detect_sleep_episodes,
    expected_sleep_range,
)


def test_expected_sleep_range_matches_age_buckets() -> None:
    # NSF / Hirshkowitz 2015.
    assert expected_sleep_range(10) == (8.0, 11.0)
    assert expected_sleep_range(22) == (7.0, 9.0)
    assert expected_sleep_range(45) == (7.0, 9.0)
    assert expected_sleep_range(70) == (7.0, 8.0)
    # Unbekanntes Alter -> Default-Erwachsenenbereich.
    assert expected_sleep_range(None) == (7.0, 9.0)


def test_detect_sleep_episodes_groups_consecutive_night_measurements() -> None:
    base = datetime(2026, 4, 1, 22, 0)
    timestamps = [base + timedelta(minutes=30 * i) for i in range(8)]
    # 5 Nacht-Messungen am Anfang, dann 3 Tag-Messungen.
    flags = np.array([True, True, True, True, True, False, False, False])

    episodes = detect_sleep_episodes(timestamps, flags, age_years=40)

    assert len(episodes) == 1
    ep = episodes[0]
    assert ep.start == timestamps[0]
    assert ep.end == timestamps[4]
    assert ep.measurement_indices == [0, 1, 2, 3, 4]
    # Sleep-Onset wird vor dem ersten Nacht-Sample geschaetzt.
    assert ep.sleep_onset_estimate < ep.start


def test_detect_sleep_episodes_splits_on_large_gap() -> None:
    base = datetime(2026, 4, 1, 22, 0)
    timestamps = [
        base,
        base + timedelta(minutes=30),
        base + timedelta(hours=5),  # > 2h Luecke -> neue Episode
        base + timedelta(hours=5, minutes=30),
    ]
    flags = np.array([True, True, True, True])

    episodes = detect_sleep_episodes(timestamps, flags, age_years=40)
    assert len(episodes) == 2
    assert episodes[0].measurement_indices == [0, 1]
    assert episodes[1].measurement_indices == [2, 3]


def test_detect_sleep_episodes_handles_unsorted_input() -> None:
    base = datetime(2026, 4, 1, 22, 0)
    timestamps = [
        base + timedelta(minutes=60),  # idx 0 -> spaeter
        base,                           # idx 1 -> frueher
    ]
    flags = np.array([True, True])
    episodes = detect_sleep_episodes(timestamps, flags)
    # Eine zusammenhaengende Episode in chronologischer Reihenfolge.
    assert len(episodes) == 1
    assert episodes[0].measurement_indices == [1, 0]


def test_detect_sleep_episodes_rejects_length_mismatch() -> None:
    timestamps = [datetime(2026, 4, 1, 22, 0)]
    flags = np.array([True, False])
    with pytest.raises(ValueError):
        detect_sleep_episodes(timestamps, flags)
