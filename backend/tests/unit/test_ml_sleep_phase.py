from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from app.services.ml.sleep_phase import (
    EPISODE_TYPE_NAP,
    EPISODE_TYPE_NIGHT,
    MIN_NAP_DURATION_MINUTES,
    apply_nap_recurrence_gate,
    classify_episode_type,
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


def test_classify_episode_type_recognises_nap_window() -> None:
    # Mittagsfenster, gleicher Tag -> Nickerchen.
    assert (
        classify_episode_type(
            datetime(2026, 4, 1, 13, 0), datetime(2026, 4, 1, 14, 0)
        )
        == EPISODE_TYPE_NAP
    )
    # Nachtfenster, über Mitternacht -> Nacht.
    assert (
        classify_episode_type(
            datetime(2026, 4, 1, 23, 0), datetime(2026, 4, 2, 6, 0)
        )
        == EPISODE_TYPE_NIGHT
    )
    # Abend-Randbereich -> weder Nickerchen noch Nacht.
    assert (
        classify_episode_type(
            datetime(2026, 4, 1, 19, 0), datetime(2026, 4, 1, 20, 0)
        )
        == "other"
    )


def _build_nap_episode(day: int, start_hour: int, duration_min: int):
    from app.services.ml.sleep_phase import SleepEpisode

    start = datetime(2026, 4, day, start_hour, 0)
    end = start + timedelta(minutes=duration_min)
    return SleepEpisode(
        start=start,
        end=end,
        sleep_onset_estimate=start,
        wake_estimate=end,
        measurement_indices=[0, 1, 2],
        confidence=0.8,
        arousal_events=[],
        episode_type=EPISODE_TYPE_NAP,
    )


def test_nap_gate_rejects_short_episodes() -> None:
    # Ein einzelnes Kurz-Nickerchen (30 min) fällt unter die 45-min-Grenze.
    short_nap = _build_nap_episode(day=1, start_hour=13, duration_min=30)
    decision = apply_nap_recurrence_gate([short_nap], observation_day_count=30)

    assert decision.accepted_episodes == []
    assert decision.rejected_episodes == [short_nap]
    assert decision.recurrent_pattern_detected is False


def test_nap_gate_rejects_single_long_nap_without_recurrence() -> None:
    # 60 min genügt der Dauer, aber nur an einem Tag — kein Muster.
    single_long = _build_nap_episode(day=5, start_hour=13, duration_min=60)
    decision = apply_nap_recurrence_gate([single_long], observation_day_count=30)

    assert decision.accepted_episodes == []
    assert decision.rejected_episodes == [single_long]
    assert decision.recurrent_pattern_detected is False
    assert decision.unique_nap_days == 0 or decision.unique_nap_days == 1


def test_nap_gate_accepts_recurrent_pattern_with_min_duration() -> None:
    # Drei Nickerchen an drei Tagen, jeweils 50 min -> Muster erkannt.
    naps = [
        _build_nap_episode(day=1, start_hour=13, duration_min=50),
        _build_nap_episode(day=3, start_hour=12, duration_min=55),
        _build_nap_episode(day=5, start_hour=14, duration_min=50),
    ]
    decision = apply_nap_recurrence_gate(naps, observation_day_count=30)

    assert len(decision.accepted_episodes) == 3
    assert decision.recurrent_pattern_detected is True
    assert decision.unique_nap_days == 3


def test_nap_gate_drops_too_short_candidates_even_when_pattern_exists() -> None:
    # Ein langer Nap und zwei Kurze — Muster zählt nur bei den langen, aber
    # das Gate akzeptiert hier, weil zwei unterschiedliche Tage mit >= 45 min
    # vorliegen.
    long_a = _build_nap_episode(day=1, start_hour=13, duration_min=50)
    long_b = _build_nap_episode(day=3, start_hour=13, duration_min=46)
    short_a = _build_nap_episode(day=5, start_hour=13, duration_min=20)
    short_b = _build_nap_episode(day=7, start_hour=13, duration_min=30)

    decision = apply_nap_recurrence_gate(
        [long_a, long_b, short_a, short_b], observation_day_count=30
    )

    accepted_ids = {id(ep) for ep in decision.accepted_episodes}
    rejected_ids = {id(ep) for ep in decision.rejected_episodes}
    assert id(long_a) in accepted_ids
    assert id(long_b) in accepted_ids
    assert id(short_a) in rejected_ids
    assert id(short_b) in rejected_ids
    assert decision.recurrent_pattern_detected is True


def test_nap_gate_uses_fraction_for_small_observation_windows() -> None:
    # Bei nur 4 beobachteten Tagen reicht 1 Nap (25 %) nicht — wir brauchen
    # mindestens 2 unterschiedliche Nap-Tage oder einen höheren Anteil.
    # Laut Default (recurrence_min_days=2, fraction=0.25) reicht hier ein
    # Nap nicht, da 1/4 = 0.25 genau am Schwellenwert, aber min_days=2.
    single = _build_nap_episode(day=1, start_hour=13, duration_min=60)
    decision = apply_nap_recurrence_gate([single], observation_day_count=4)
    # 1 Nap-Tag, 25 % der Beobachtungstage -> fraction_gate greift (>= 0.25),
    # count_gate nicht. Das Gate verknüpft über OR, also akzeptiert.
    assert decision.recurrent_pattern_detected is True

    # Bei nur 2 beobachteten Tagen reicht 1 Nap (50 %) klar.
    decision_b = apply_nap_recurrence_gate([single], observation_day_count=2)
    assert decision_b.recurrent_pattern_detected is True


def test_min_nap_duration_matches_requirement() -> None:
    # Regressions-Check: Die Vorgabe "mindestens 45 min" steht fest im Code.
    assert MIN_NAP_DURATION_MINUTES == 45.0
