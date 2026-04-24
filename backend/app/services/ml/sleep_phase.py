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

from dataclasses import dataclass, field
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

# --- Hyperparameter der Episoden-Aggregation -----------------------------
#
# Diese Konstanten sind die zentralen Stellschrauben für das Verhalten des
# Sleep-Phase-Detektors. Sie werden von ``detect_sleep_episodes`` als
# Default-Parameter verwendet und können pro Aufruf überschrieben werden.

# Maximale Lücke zwischen zwei aufeinanderfolgenden Nacht-Messungen, bevor
# eine neue Episode begonnen wird. Größer = robuster gegen fehlende
# Stichproben, aber vermischt ggf. entfernte Schlafphasen.
NIGHT_GAP_TOLERANCE_MINUTES = 120.0

# Zwei benachbarte Nacht-Episoden werden zu einer zusammengezogen, wenn die
# Lücke zwischen ihrem Ende und dem Start der nächsten kürzer als dieser
# Wert ist. Dient dazu, 15–60-Minuten-Fragmente (einzelne Mess-Ausreißer
# mitten in der Nacht) nicht als getrennte Schlafphasen anzuzeigen.
# Setze den Wert auf 0, um das Merging zu deaktivieren.
NIGHT_MERGE_GAP_MINUTES = 60.0

# Mindestdauer einer echten Nacht-Episode in Minuten. Kürzere Episoden
# gelten als Fragmente und werden vom Post-Filter verworfen bzw. im Unified-
# View nicht mehr als Schlafbanner dargestellt. Setze den Wert auf 0, um den
# Filter zu deaktivieren.
NIGHT_MIN_EPISODE_MINUTES = 30.0

# Anzahl aufeinanderfolgender Outlier-Messungen, die als Aufwach-Phase
# (Arousal) innerhalb einer Schlafepisode gewertet werden. 2 ist der in der
# Aufgabenstellung geforderte Mindestwert; 3 macht das System gegenüber
# einzelnen Rauschmessungen robuster.
ARROUSAL_MIN_RUN = 2


# Episodentypen: werden vom Unified-View genutzt, um Nacht- und Mittagsschlaf
# gezielt unterschiedlich zu behandeln (z. B. Darstellung, Gate für Naps).
EPISODE_TYPE_NIGHT = "night"
EPISODE_TYPE_NAP = "nap"
EPISODE_TYPE_OTHER = "other"

# Mittagsfenster für die Nickerchen-Erkennung (inklusive Start, exklusive Ende).
# Die Spanne deckt typische Siesta-Zeiten ab (vgl. Milner & Cote, 2009,
# "Benefits of napping in healthy adults", Journal of Sleep Research 18).
NAP_WINDOW_START_HOUR = 11
NAP_WINDOW_END_HOUR = 17

# Mindestdauer für ein als Nickerchen gewertetes Muster. 45 min setzt eine
# erkennbare Schlafphase voraus (Brooks & Lack, 2006: Nickerchen ab ca. 30 min
# liefern messbare Erholung; unter 30 min sind es eher "power naps", ohne dass
# die Blutdruckkurve typische Dipping-Muster zeigt).
MIN_NAP_DURATION_MINUTES = 45.0

# Gate gegen sporadische Mittags-Ausreißer: ein Nickerchen-Muster wird erst
# akzeptiert, wenn es an mindestens ``NAP_RECURRENCE_MIN_DAYS`` einzelnen Tagen
# auftritt ODER auf mindestens ``NAP_RECURRENCE_FRACTION`` der beobachteten
# Tage. Ziel: keine Nap-Klassifikation für einzelne Zufallstreffer.
NAP_RECURRENCE_MIN_DAYS = 2
NAP_RECURRENCE_FRACTION = 0.25


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
    episode_type: str = EPISODE_TYPE_NIGHT

    def __post_init__(self) -> None:
        if self.arousal_events is None:
            self.arousal_events = []

    @property
    def duration_minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0

    def to_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "sleep_onset_estimate": self.sleep_onset_estimate.isoformat(),
            "wake_estimate": self.wake_estimate.isoformat(),
            "measurement_indices": list(self.measurement_indices),
            "confidence": float(self.confidence),
            "arousal_events": [ev.to_dict() for ev in self.arousal_events],
            "episode_type": self.episode_type,
            "duration_minutes": float(self.duration_minutes),
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
    min_arousal_run: int = ARROUSAL_MIN_RUN,
    gap_tolerance_minutes: float = NIGHT_GAP_TOLERANCE_MINUTES,
    merge_gap_minutes: float = NIGHT_MERGE_GAP_MINUTES,
) -> list[SleepEpisode]:
    """Aggregiere zusammenhängende Nacht-Messungen zu Schlaf-Episoden.

    Hyperparameter (siehe Modul-Head für Details und Default-Werte):

    * ``gap_tolerance_minutes`` — Maximale Lücke zwischen zwei Nacht-Messungen
      innerhalb einer Episode. Wird sie überschritten, beginnt eine neue
      Episode.
    * ``merge_gap_minutes`` — Zwei direkt benachbarte Nacht-Episoden werden
      im Post-Processing zusammengezogen, wenn die Pause zwischen ihnen
      kürzer als dieser Wert ist. Das verhindert 15–60-Minuten-Fragmente
      durch einzelne abweichende Messungen mitten in der Nacht.
    * ``min_arousal_run`` — Mindestanzahl aufeinanderfolgender Outlier, die
      als Aufwach-Phase erkannt werden (die Episode wird dabei nicht
      geteilt, nur annotiert).

    Liegt ``night_ref_outlier`` (0/1-Maske pro Messung aus dem Feature-
    Engineering) vor, werden Outlier-Läufe innerhalb einer Episode als
    Aufwach-Vermutung annotiert.
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
    gap_tolerance = timedelta(minutes=max(0.0, float(gap_tolerance_minutes)))

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
        if last_ts is not None and (ts - last_ts) > gap_tolerance:
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

    if merge_gap_minutes > 0.0:
        episodes = _merge_close_night_episodes(
            episodes,
            merge_gap_minutes=merge_gap_minutes,
            age_years=age_years,
        )

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

    episode_type = classify_episode_type(start, end)

    return SleepEpisode(
        start=start,
        end=end,
        sleep_onset_estimate=sleep_onset,
        wake_estimate=wake,
        measurement_indices=list(indices),
        confidence=float(confidence),
        arousal_events=arousals,
        episode_type=episode_type,
    )


def _episode_confidence(
    start: datetime,
    end: datetime,
    arousals: list[ArousalEvent],
    age_years: float | None,
) -> float:
    """Einheitliche Konfidenz-Berechnung (wird beim Mergen wiederverwendet)."""

    duration_h = (end - start).total_seconds() / 3600.0
    if duration_h <= 0:
        return 0.0
    expected_lo, expected_hi = expected_sleep_range(age_years)
    if expected_lo <= duration_h <= expected_hi:
        base = 1.0
    else:
        distance = min(abs(duration_h - expected_lo), abs(duration_h - expected_hi))
        base = max(0.0, 1.0 - distance / max(expected_hi, 1.0))
    if arousals:
        base = max(0.0, base - 0.1 * min(3, len(arousals)))
    return float(base)


def _merge_close_night_episodes(
    episodes: list[SleepEpisode],
    *,
    merge_gap_minutes: float,
    age_years: float | None,
) -> list[SleepEpisode]:
    """Verschmilz zeitlich eng benachbarte Nacht-Episoden zu einer Episode.

    Zwei Episoden vom Typ ``night`` oder ``other`` werden zusammengezogen,
    wenn die Lücke zwischen ``prev.end`` und ``next.start`` kürzer als
    ``merge_gap_minutes`` ist. Nickerchen (``nap``) bleiben davon unberührt,
    weil das :func:`apply_nap_recurrence_gate` sie eigenständig filtert.

    Die Merging-Logik ist bewusst sequenziell: wir laufen einmal durch die
    sortierten Episoden und bauen das Ergebnis inkrementell auf. So entsteht
    ein stabiles, vorhersagbares Verhalten auch bei mehreren benachbarten
    Fragmenten.
    """

    if not episodes:
        return episodes

    sorted_eps = sorted(episodes, key=lambda ep: ep.start)
    merged: list[SleepEpisode] = []
    for ep in sorted_eps:
        if not merged:
            merged.append(ep)
            continue
        prev = merged[-1]
        # Naps werden separat behandelt und nicht mit Nacht-Episoden vermischt.
        if prev.episode_type == EPISODE_TYPE_NAP or ep.episode_type == EPISODE_TYPE_NAP:
            merged.append(ep)
            continue
        gap_minutes = (ep.start - prev.end).total_seconds() / 60.0
        if gap_minutes < 0.0 or gap_minutes > merge_gap_minutes:
            merged.append(ep)
            continue
        combined_indices = list(prev.measurement_indices) + list(ep.measurement_indices)
        combined_arousals = list(prev.arousal_events) + list(ep.arousal_events)
        merged[-1] = SleepEpisode(
            start=prev.start,
            end=ep.end,
            sleep_onset_estimate=prev.sleep_onset_estimate,
            wake_estimate=ep.end,
            measurement_indices=combined_indices,
            confidence=_episode_confidence(prev.start, ep.end, combined_arousals, age_years),
            arousal_events=combined_arousals,
            episode_type=classify_episode_type(prev.start, ep.end),
        )
    return merged


@dataclass
class ShortEpisodeFilterResult:
    """Ergebnis des Mindestdauer-Filters für Nicht-Nap-Episoden."""

    accepted_episodes: list[SleepEpisode] = field(default_factory=list)
    rejected_episodes: list[SleepEpisode] = field(default_factory=list)
    min_duration_minutes: float = 0.0


def filter_short_night_episodes(
    episodes: list[SleepEpisode],
    *,
    min_duration_minutes: float = NIGHT_MIN_EPISODE_MINUTES,
) -> ShortEpisodeFilterResult:
    """Trenne zu kurze Nicht-Nap-Episoden (Fragmente) von echten Schlafphasen.

    Der Filter greift nur bei Episoden vom Typ ``night`` oder ``other`` —
    Nap-Kandidaten werden vom :func:`apply_nap_recurrence_gate` über eine
    eigene Mindestdauer gesteuert (siehe :data:`MIN_NAP_DURATION_MINUTES`).

    ``min_duration_minutes`` <= 0 deaktiviert den Filter und gibt alle
    Episoden unverändert zurück.
    """

    threshold = float(min_duration_minutes)
    if threshold <= 0.0:
        return ShortEpisodeFilterResult(
            accepted_episodes=list(episodes),
            rejected_episodes=[],
            min_duration_minutes=0.0,
        )

    accepted: list[SleepEpisode] = []
    rejected: list[SleepEpisode] = []
    for ep in episodes:
        if ep.episode_type == EPISODE_TYPE_NAP:
            accepted.append(ep)
            continue
        if ep.duration_minutes >= threshold:
            accepted.append(ep)
        else:
            rejected.append(ep)
    return ShortEpisodeFilterResult(
        accepted_episodes=accepted,
        rejected_episodes=rejected,
        min_duration_minutes=threshold,
    )


def classify_episode_type(start: datetime, end: datetime) -> str:
    """Ordne eine Episode einem Typ zu: Nacht, Nickerchen oder "other".

    Eine Episode gilt als ``night``, wenn sie das typische Nachtfenster
    (22..07 Uhr) berührt oder über Mitternacht reicht. Ein ``nap`` liegt
    vollständig im Mittagsfenster (:data:`NAP_WINDOW_START_HOUR` bis
    :data:`NAP_WINDOW_END_HOUR`) und beginnt und endet am gleichen Tag.
    Alles andere — z. B. früher Abend oder später Vormittag — bleibt
    ``other`` und wird vom Frontend nur als generisches Ruhe-Fenster
    gezeigt.
    """

    if start.date() != end.date():
        return EPISODE_TYPE_NIGHT
    start_hour = start.hour
    end_hour = end.hour
    if start_hour >= 22 or end_hour < 7:
        return EPISODE_TYPE_NIGHT
    if (
        NAP_WINDOW_START_HOUR <= start_hour < NAP_WINDOW_END_HOUR
        and NAP_WINDOW_START_HOUR <= end_hour < NAP_WINDOW_END_HOUR
    ):
        return EPISODE_TYPE_NAP
    return EPISODE_TYPE_OTHER


@dataclass
class NapGateDecision:
    """Ergebnis des Nickerchen-Gates über alle Reports hinweg.

    ``accepted_episodes`` enthält die Episoden, die als echte Nickerchen
    übernommen werden. ``rejected_episodes`` sind Kandidaten, die entweder
    zu kurz waren oder mangels wiederkehrendem Muster verworfen wurden —
    das aufrufende Modul kann für diese Messungen das Nacht-Flag
    zurücksetzen.
    """

    accepted_episodes: list[SleepEpisode] = field(default_factory=list)
    rejected_episodes: list[SleepEpisode] = field(default_factory=list)
    recurrent_pattern_detected: bool = False
    unique_nap_days: int = 0
    observation_day_count: int = 0


def apply_nap_recurrence_gate(
    episodes: list[SleepEpisode],
    *,
    observation_day_count: int,
    min_duration_minutes: float = MIN_NAP_DURATION_MINUTES,
    recurrence_min_days: int = NAP_RECURRENCE_MIN_DAYS,
    recurrence_fraction: float = NAP_RECURRENCE_FRACTION,
) -> NapGateDecision:
    """Akzeptiere Nickerchen nur bei ausreichender Dauer und Wiederholung.

    Das Gate arbeitet auf bereits zu Episoden aggregierten Daten:

    1. **Dauer-Filter** — Episoden vom Typ ``nap`` mit einer Dauer unter
       ``min_duration_minutes`` werden verworfen. Damit wird ein einzelnes
       mittägliches Ausreißer-Paar nicht sofort zum Nickerchen.
    2. **Wiederholungs-Gate** — Treten nach Filter 1 weniger Nap-Tage als
       ``recurrence_min_days`` auf *und* ist der Anteil nap-positiver Tage
       kleiner als ``recurrence_fraction`` der beobachteten Tage, werden
       alle Nap-Kandidaten verworfen. So bleibt die Klassifikation "kein
       Nickerchen" der Normalfall, solange kein regelmäßiges Muster sichtbar
       ist.

    ``observation_day_count`` ist die Anzahl unterschiedlicher Kalendertage
    mit Messdaten im gesamten betrachteten Zeitraum (alle Reports). Für 0
    wird der Bruchteil-Test automatisch übersprungen.
    """

    candidates = [ep for ep in episodes if ep.episode_type == EPISODE_TYPE_NAP]
    if not candidates:
        return NapGateDecision(
            accepted_episodes=[],
            rejected_episodes=[],
            recurrent_pattern_detected=False,
            unique_nap_days=0,
            observation_day_count=max(0, observation_day_count),
        )

    long_enough = [ep for ep in candidates if ep.duration_minutes >= min_duration_minutes]
    too_short = [ep for ep in candidates if ep.duration_minutes < min_duration_minutes]

    unique_nap_days = {ep.start.date() for ep in long_enough}
    n_days = len(unique_nap_days)

    fraction_gate = (
        observation_day_count > 0
        and n_days >= recurrence_fraction * observation_day_count
    )
    count_gate = n_days >= recurrence_min_days
    recurrent = bool(long_enough) and (count_gate or fraction_gate)

    if recurrent:
        accepted = list(long_enough)
        rejected = list(too_short)
    else:
        # Kein wiederkehrendes Muster — alle Nap-Kandidaten zurücknehmen.
        accepted = []
        rejected = list(candidates)

    return NapGateDecision(
        accepted_episodes=accepted,
        rejected_episodes=rejected,
        recurrent_pattern_detected=recurrent,
        unique_nap_days=n_days,
        observation_day_count=max(0, observation_day_count),
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
