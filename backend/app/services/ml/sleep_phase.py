from __future__ import annotations

"""Schätzung von Einschlaf- und Aufwachzeitpunkten je Nacht.

Idee
----
Aus der Klassifikation pro Messung lassen sich zusammenhängende
Nacht-Episoden ableiten. Innerhalb jeder Episode markieren wir die erste
Messung als Aufwachfenster-Beginn und die letzte als Aufwachfenster-Ende.
Den Einschlaf-Zeitpunkt schätzen wir als Übergang vom letzten klar als Tag
klassifizierten Wert (oder einer Telefonmessung) zur ersten Nachtmessung.

Quellen
-------
* Sleep Onset Latency bei Erwachsenen typischerweise 10–20 Minuten
  (American Thoracic Society Patient Education, 2017).
* Schlafdauer-Empfehlung 7–9 h bei 26–64-Jährigen, 7–8 h ab 65 (National
  Sleep Foundation, Hirshkowitz et al., Sleep Health 1, 2015).
* Nachtphase im ambulanten BP-Monitoring üblicherweise 22:00–06:00 oder
  individuell nach Schlafprotokoll (ESH-Guideline 2023, Mancia et al.).

Diese Werte fließen nur als sanfte Plausibilitätsprüfung ein — die
eigentliche Detektion stützt sich auf die Modellausgabe.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np


# Erwartete Schlafdauer pro Altersgruppe (NSF / Hirshkowitz et al. 2015).
_SLEEP_HOURS_BY_AGE = (
    (0, 17, (8.0, 11.0)),
    (18, 25, (7.0, 9.0)),
    (26, 64, (7.0, 9.0)),
    (65, 200, (7.0, 8.0)),
)

_DEFAULT_SLEEP_RANGE = (7.0, 9.0)
_DEFAULT_ONSET_LATENCY_MIN = 15  # Minuten Sleep Onset Latency


@dataclass
class ArousalEvent:
    """Aufwach-Vermutung innerhalb einer Schlafepisode.

    Wird ausgelöst, wenn mindestens zwei Messungen in Folge klar außerhalb
    der Nacht-Referenz liegen (siehe ``feature_engineering.NightDayReference``)
    und die Werte danach wieder in den ruhigen Bereich zurückkehren. So
    bilden wir kurze Wachphasen ab, ohne die Episode zu zerschneiden — die
    Episode dauert weiter, bekommt aber den Aufwach-Punkt als Annotation.
    """

    start: datetime
    end: datetime
    measurement_indices: list[int]

    def to_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "measurement_indices": list(self.measurement_indices),
        }


@dataclass
class SleepEpisode:
    start: datetime
    end: datetime
    sleep_onset_estimate: datetime
    wake_estimate: datetime
    measurement_indices: list[int]
    confidence: float
    arousal_events: list[ArousalEvent] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.arousal_events is None:
            self.arousal_events = []

    def to_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "sleep_onset_estimate": self.sleep_onset_estimate.isoformat(),
            "wake_estimate": self.wake_estimate.isoformat(),
            "measurement_indices": list(self.measurement_indices),
            "confidence": float(self.confidence),
            "arousal_events": [ev.to_dict() for ev in self.arousal_events],
        }


def expected_sleep_range(age_years: float | None) -> tuple[float, float]:
    if age_years is None:
        return _DEFAULT_SLEEP_RANGE
    age = float(age_years)
    for lo, hi, span in _SLEEP_HOURS_BY_AGE:
        if lo <= age <= hi:
            return span
    return _DEFAULT_SLEEP_RANGE


def detect_sleep_episodes(
    timestamps: list[datetime],
    is_night: np.ndarray,
    *,
    age_years: float | None = None,
    onset_latency_minutes: int = _DEFAULT_ONSET_LATENCY_MIN,
    night_ref_outlier: np.ndarray | None = None,
    min_arousal_run: int = 2,
) -> list[SleepEpisode]:
    """Aggregiere zusammenhängende Nacht-Messungen zu Schlaf-Episoden.

    Episoden, die weniger als ``min_duration_minutes`` dauern, werden
    verworfen, weil sie wahrscheinlich Ausreißer sind. Der Standardwert
    leitet sich aus der ESH-Empfehlung ab, dass die Nachtphase mindestens
    ein paar zusammenhängende Stunden umfassen sollte.

    Wenn ``night_ref_outlier`` (eine 0/1-Maske pro Messung aus dem Feature-
    Engineering) übergeben wird, kennzeichnen wir innerhalb jeder Episode
    Läufe von mindestens ``min_arousal_run`` Outliern als Aufwach-Vermutung.
    Die Episode bleibt dabei eine Episode — wir setzen sie nur fort, wenn
    sich die Werte danach in den ruhigen Bereich zurück bewegen (genau die
    Anforderung aus der Aufgabenstellung).
    """

    if len(timestamps) == 0 or is_night.size == 0:
        return []
    if len(timestamps) != is_night.size:
        raise ValueError("timestamps und is_night müssen die gleiche Länge haben")
    if night_ref_outlier is not None and night_ref_outlier.size not in (0, len(timestamps)):
        raise ValueError("night_ref_outlier muss die gleiche Länge wie timestamps haben")

    sorted_idx = sorted(range(len(timestamps)), key=lambda i: timestamps[i])

    episodes: list[SleepEpisode] = []
    current: list[int] = []
    last_ts: datetime | None = None
    GAP_TOLERANCE = timedelta(hours=2)

    for i in sorted_idx:
        if not is_night[i]:
            if current:
                episodes.append(_build_episode(
                    current, timestamps, age_years, onset_latency_minutes,
                    night_ref_outlier, min_arousal_run,
                ))
                current = []
            last_ts = None
            continue
        ts = timestamps[i]
        if last_ts is not None and (ts - last_ts) > GAP_TOLERANCE:
            episodes.append(_build_episode(
                current, timestamps, age_years, onset_latency_minutes,
                night_ref_outlier, min_arousal_run,
            ))
            current = []
        current.append(i)
        last_ts = ts

    if current:
        episodes.append(_build_episode(
            current, timestamps, age_years, onset_latency_minutes,
            night_ref_outlier, min_arousal_run,
        ))

    return episodes


def _build_episode(
    indices: list[int],
    timestamps: list[datetime],
    age_years: float | None,
    onset_latency_minutes: int,
    night_ref_outlier: np.ndarray | None,
    min_arousal_run: int,
) -> SleepEpisode:
    start = timestamps[indices[0]]
    end = timestamps[indices[-1]]
    sleep_onset = start - timedelta(minutes=onset_latency_minutes)
    wake = end
    expected_lo, expected_hi = expected_sleep_range(age_years)
    duration_h = (end - start).total_seconds() / 3600.0
    if duration_h <= 0:
        confidence = 0.0
    elif expected_lo <= duration_h <= expected_hi:
        confidence = 1.0
    else:
        # Lineare Abstrafung außerhalb des Erwartungsbereichs.
        distance = min(abs(duration_h - expected_lo), abs(duration_h - expected_hi))
        confidence = max(0.0, 1.0 - distance / max(expected_hi, 1.0))

    arousals = _detect_arousals(indices, timestamps, night_ref_outlier, min_arousal_run)
    # Jede erkannte Aufwach-Phase senkt die Episoden-Konfidenz leicht — der
    # Schlaf gilt dann nicht mehr als "durchgehend".
    if arousals:
        confidence = max(0.0, confidence - 0.1 * min(3, len(arousals)))

    return SleepEpisode(
        start=start,
        end=end,
        sleep_onset_estimate=sleep_onset,
        wake_estimate=wake,
        measurement_indices=list(indices),
        confidence=float(confidence),
        arousal_events=arousals,
    )


def _detect_arousals(
    indices: list[int],
    timestamps: list[datetime],
    night_ref_outlier: np.ndarray | None,
    min_run: int,
) -> list[ArousalEvent]:
    """Suche zusammenhängende Outlier-Läufe (>= ``min_run``) in der Episode.

    Ein Lauf endet, sobald wieder ein "ruhiger" (Nicht-Outlier) Punkt folgt;
    so erkennen wir genau das in der Aufgabenstellung beschriebene Muster:
    "≥2 Ausreißer ⇒ wahrscheinlich aufgewacht; Werte beruhigen sich ⇒ wieder
    Schlaf".
    """

    if night_ref_outlier is None or night_ref_outlier.size == 0 or not indices:
        return []

    arousals: list[ArousalEvent] = []
    run: list[int] = []
    for i in indices:
        if i < 0 or i >= night_ref_outlier.size:
            continue
        if float(night_ref_outlier[i]) >= 1.0:
            run.append(i)
            continue
        if len(run) >= min_run:
            arousals.append(_arousal_from_run(run, timestamps))
        run = []
    if len(run) >= min_run:
        arousals.append(_arousal_from_run(run, timestamps))
    return arousals


def _arousal_from_run(run: list[int], timestamps: list[datetime]) -> ArousalEvent:
    return ArousalEvent(
        start=timestamps[run[0]],
        end=timestamps[run[-1]],
        measurement_indices=list(run),
    )
