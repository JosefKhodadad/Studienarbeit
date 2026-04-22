from __future__ import annotations

"""Feature-Engineering für die Nacht/Tag-Klassifikation.

Die Features sind bewusst auf das beschränkt, was mit unseren Hilo-Daten
robust ableitbar ist und einen physiologischen Bezug hat:

* zyklische Zeitfeatures (Stunde, Wochentag) als ``sin``/``cos``-Paare,
* Distanz zur typischen Schlafmitte (~02:00) auf dem 24-h-Kreis,
* Vital-Werte (SBP, DBP, HR) und ihre Differenz zum patientenweisen Median
  (Dipping-Indikator: nachts fallen Blutdruck und Puls typischerweise um
  10–20 % gegenüber dem Tagesmittel — siehe ESH/ESC-Leitlinie 2023),
* lokale Trends in einem Fenster von ±N Messungen (geglätteter SBP/HR),
* Messtyp-Hinweis: Telefonmessungen sind in der Hilo-App nur tagsüber
  möglich, Armband-Messungen laufen 24/7. Ein Telefon-Flag ist daher ein
  starker Tag-Hinweis,
* optionale Patientenmerkmale (Alter, Geschlecht-Flag) als Kontext.

Alle Funktionen sind reine Numpy-Operationen ohne Seiteneffekte, sodass die
Feature-Berechnung in Tests deterministisch nachvollziehbar bleibt.
"""

from dataclasses import dataclass
from datetime import datetime
import math

import numpy as np


# --- Konstanten -----------------------------------------------------------

# Reihenfolge der Spalten in der Feature-Matrix. ``FEATURE_NAMES`` ist Teil der
# öffentlichen Schnittstelle: Persistierte Modelle speichern diese Liste in
# ihren Metadaten, damit beim Laden klar ist, mit welcher Eingabeform das
# Modell trainiert wurde.
FEATURE_NAMES: tuple[str, ...] = (
    "sin_hour",
    "cos_hour",
    "sin_weekday",
    "cos_weekday",
    "dist_to_sleep_center",
    "is_phone_measurement",
    "is_armband",
    "sbp",
    "dbp",
    "hr",
    "sbp_delta_to_day_median",
    "dbp_delta_to_day_median",
    "hr_delta_to_day_median",
    "sbp_local_trend",
    "hr_local_trend",
    "minutes_since_last",
    "age_norm",
)

# Indizes der Features, die NICHT pro Feature standardisiert werden, weil sie
# bereits in einem festen Wertebereich liegen.
_NO_STANDARDIZE_INDICES: frozenset[int] = frozenset(
    {0, 1, 2, 3, 4, 5, 6, 16}
)

# Fenstergröße für lokale Trends (vor und nach der aktuellen Messung).
_LOCAL_WINDOW = 3

# Typischer Erwachsenen-Wertebereich für die Alter-Normierung.
_AGE_REF = 60.0
_AGE_SPAN = 40.0

# Weak-Label-Heuristik: Stunden, in denen Schlaf wahrscheinlich ist.
_WEAK_NIGHT_START = 22
_WEAK_NIGHT_END = 6  # exklusiv


@dataclass(frozen=True)
class FeatureMatrix:
    """Container für Features + Hilfsdaten für Constraint/Sleep-Phase."""

    matrix: np.ndarray              # shape (n_samples, n_features)
    weak_labels: np.ndarray         # shape (n_samples,), Werte in {0,1}
    sample_weights: np.ndarray      # shape (n_samples,), positive Floats
    hours: np.ndarray               # shape (n_samples,), Stunde+Minute/60
    timestamps: list[datetime]      # für Sleep-Phase-Heuristik
    measurement_types: list[str]    # für Erklärbarkeit / Logging
    night_drop_score: np.ndarray    # shape (n_samples,), 0..1


# --- öffentliche Funktionen ----------------------------------------------

def build_features(
    measurements: list[dict],
    *,
    age_years: float | None = None,
    weak_label_method: str = "rule",
) -> FeatureMatrix:
    """Erzeuge Features + schwache Labels für eine Liste von Messungen.

    Die Reihenfolge der Zeilen entspricht der Reihenfolge in
    ``measurements``. Eingabe-Listen werden nicht mutiert.
    """

    if not measurements:
        empty = np.zeros((0, len(FEATURE_NAMES)), dtype=np.float64)
        return FeatureMatrix(
            matrix=empty,
            weak_labels=np.zeros(0, dtype=np.float64),
            sample_weights=np.zeros(0, dtype=np.float64),
            hours=np.zeros(0, dtype=np.float64),
            timestamps=[],
            measurement_types=[],
            night_drop_score=np.zeros(0, dtype=np.float64),
        )

    sorted_indices = _sorted_index_by_time(measurements)
    timestamps_sorted = [_parse_dt(measurements[i]["datetime"]) for i in sorted_indices]
    sbp_sorted = np.array([_safe_float(measurements[i].get("systolic")) for i in sorted_indices])
    dbp_sorted = np.array([_safe_float(measurements[i].get("diastolic")) for i in sorted_indices])
    hr_sorted = np.array([_safe_float(measurements[i].get("heart_rate")) for i in sorted_indices])
    types_sorted = [str(measurements[i].get("measurement_type") or "unknown").lower() for i in sorted_indices]

    sbp_trend_sorted = _local_trend(sbp_sorted, _LOCAL_WINDOW)
    hr_trend_sorted = _local_trend(hr_sorted, _LOCAL_WINDOW)
    minutes_since_sorted = _minutes_since_previous(timestamps_sorted)

    # Tagesmediane für Dipping: pro Datum (lokales Datum), berechnet aus
    # Messungen mit Stunde 9..21, also dem typischen Wachfenster. Fällt das
    # Fenster für einen Tag leer aus, fallen wir auf den Median aller
    # Messungen dieses Tages zurück.
    day_median_sbp = _per_day_median(timestamps_sorted, sbp_sorted)
    day_median_dbp = _per_day_median(timestamps_sorted, dbp_sorted)
    day_median_hr = _per_day_median(timestamps_sorted, hr_sorted)

    # Map zurück in die ursprüngliche Reihenfolge.
    n = len(measurements)
    matrix = np.zeros((n, len(FEATURE_NAMES)), dtype=np.float64)
    weak_labels = np.zeros(n, dtype=np.float64)
    sample_weights = np.ones(n, dtype=np.float64)
    hours = np.zeros(n, dtype=np.float64)
    night_drop = np.zeros(n, dtype=np.float64)
    timestamps_orig: list[datetime] = [datetime.min] * n
    types_orig: list[str] = [""] * n

    age_norm = _normalize_age(age_years)

    for sorted_pos, original_pos in enumerate(sorted_indices):
        ts = timestamps_sorted[sorted_pos]
        hour = ts.hour + ts.minute / 60.0
        hours[original_pos] = hour
        timestamps_orig[original_pos] = ts
        types_orig[original_pos] = types_sorted[sorted_pos]

        sin_h, cos_h = _cyclic(hour, period=24.0)
        sin_w, cos_w = _cyclic(float(ts.weekday()), period=7.0)
        dist_sleep_center = 1.0 - math.cos(2 * math.pi * ((hour - 2.0) / 24.0))

        is_phone = 1.0 if "telefon" in types_sorted[sorted_pos] or "phone" in types_sorted[sorted_pos] else 0.0
        is_armband = 1.0 if "armband" in types_sorted[sorted_pos] else 0.0

        sbp = sbp_sorted[sorted_pos]
        dbp = dbp_sorted[sorted_pos]
        hr = hr_sorted[sorted_pos]

        date_key = ts.date().isoformat()
        med_sbp = day_median_sbp.get(date_key, float(np.median(sbp_sorted)) if sbp_sorted.size else 0.0)
        med_dbp = day_median_dbp.get(date_key, float(np.median(dbp_sorted)) if dbp_sorted.size else 0.0)
        med_hr = day_median_hr.get(date_key, float(np.median(hr_sorted)) if hr_sorted.size else 0.0)

        sbp_delta = sbp - med_sbp
        dbp_delta = dbp - med_dbp
        hr_delta = hr - med_hr

        matrix[original_pos] = (
            sin_h, cos_h, sin_w, cos_w, dist_sleep_center,
            is_phone, is_armband,
            sbp, dbp, hr,
            sbp_delta, dbp_delta, hr_delta,
            sbp_trend_sorted[sorted_pos], hr_trend_sorted[sorted_pos],
            minutes_since_sorted[sorted_pos],
            age_norm,
        )

        # Schwaches Label: Standard-Regel (22..06) ist die Basis. Telefonmessungen
        # erzwingen Tag-Label, weil die Hilo-App diese nur im Wachzustand
        # erlaubt — eine harte Domäne-Regel.
        weak = _weak_night_label(hour, weak_label_method)
        if is_phone == 1.0:
            weak = 0.0
        weak_labels[original_pos] = weak

        # Sample-Gewichte: nahe der Mitte der Nachtschiene bzw. weit
        # tagsüber sind die Labels sicherer; an den Rändern (06–08, 20–22)
        # weniger sicher. Diese weiche Gewichtung wirkt regularisierend.
        sample_weights[original_pos] = _label_confidence(hour)

        # Night-Drop-Score: relative Senkung von SBP+HR vs. Tagesmedian,
        # auf 0..1 abgebildet. Nur als Hilfs-Feature für Sleep-Phase /
        # Constraint-Logik exportiert, nicht als Modell-Input — das Modell
        # bekommt die Roh-Deltas und kann selbst lernen.
        rel = 0.0
        if med_sbp > 1.0 and med_hr > 1.0:
            rel_sbp = max(0.0, (med_sbp - sbp) / med_sbp)
            rel_hr = max(0.0, (med_hr - hr) / med_hr)
            rel = min(1.0, 0.5 * rel_sbp / 0.15 + 0.5 * rel_hr / 0.20)
        night_drop[original_pos] = rel

    return FeatureMatrix(
        matrix=matrix,
        weak_labels=weak_labels,
        sample_weights=sample_weights,
        hours=hours,
        timestamps=timestamps_orig,
        measurement_types=types_orig,
        night_drop_score=night_drop,
    )


def standardize(
    matrix: np.ndarray,
    *,
    stats: dict[str, np.ndarray] | None = None,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Standardisiere die Feature-Matrix spaltenweise (z-Score).

    Spalten in :data:`_NO_STANDARDIZE_INDICES` bleiben unverändert. Wird
    ``stats`` übergeben (``mean``/``std`` aus dem Training), nutzen wir diese
    Werte; andernfalls werden sie aus ``matrix`` geschätzt und zurückgegeben.
    """

    if matrix.size == 0:
        return matrix, stats or {"mean": np.zeros(matrix.shape[1] if matrix.ndim == 2 else 0),
                                  "std": np.ones(matrix.shape[1] if matrix.ndim == 2 else 0)}

    n_features = matrix.shape[1]
    if stats is None:
        mean = np.zeros(n_features, dtype=np.float64)
        std = np.ones(n_features, dtype=np.float64)
        for col in range(n_features):
            if col in _NO_STANDARDIZE_INDICES:
                continue
            mean[col] = float(np.mean(matrix[:, col]))
            col_std = float(np.std(matrix[:, col]))
            std[col] = col_std if col_std > 1e-9 else 1.0
        stats = {"mean": mean, "std": std}

    scaled = matrix.copy()
    mean = stats["mean"]
    std = stats["std"]
    for col in range(n_features):
        if col in _NO_STANDARDIZE_INDICES:
            continue
        scaled[:, col] = (matrix[:, col] - mean[col]) / std[col]
    return scaled, stats


# --- Hilfsfunktionen -----------------------------------------------------

def _safe_float(value: object) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _cyclic(value: float, *, period: float) -> tuple[float, float]:
    angle = 2 * math.pi * (value / period)
    return math.sin(angle), math.cos(angle)


def _weak_night_label(hour: float, method: str) -> float:
    if method != "rule":
        raise ValueError(f"Unbekannte Weak-Label-Methode: {method!r}")
    return 1.0 if (hour >= _WEAK_NIGHT_START or hour < _WEAK_NIGHT_END) else 0.0


def _label_confidence(hour: float) -> float:
    """Höher in der Nachtmitte / am Tagshöhepunkt, niedriger am Übergang."""

    if 0.0 <= hour < 5.0:
        return 1.0
    if 5.0 <= hour < 7.0:
        return 0.4
    if 7.0 <= hour < 10.0:
        return 0.7
    if 10.0 <= hour < 19.0:
        return 1.0
    if 19.0 <= hour < 22.0:
        return 0.5
    return 0.9  # 22..24


def _sorted_index_by_time(measurements: list[dict]) -> list[int]:
    return sorted(range(len(measurements)), key=lambda i: measurements[i]["datetime"])


def _local_trend(values: np.ndarray, window: int) -> np.ndarray:
    """Differenz zum gleitenden Mittel über ein zentriertes Fenster."""

    if values.size == 0:
        return values
    n = values.size
    out = np.zeros_like(values, dtype=np.float64)
    for i in range(n):
        lo = max(0, i - window)
        hi = min(n, i + window + 1)
        local_mean = float(np.mean(values[lo:hi]))
        out[i] = float(values[i]) - local_mean
    return out


def _minutes_since_previous(timestamps: list[datetime]) -> np.ndarray:
    if not timestamps:
        return np.zeros(0, dtype=np.float64)
    out = np.zeros(len(timestamps), dtype=np.float64)
    for i in range(1, len(timestamps)):
        delta = timestamps[i] - timestamps[i - 1]
        # Auf 240 Minuten kappen — größere Lücken sind selten und sollen das
        # Feature nicht dominieren.
        out[i] = min(240.0, max(0.0, delta.total_seconds() / 60.0))
    return out


def _per_day_median(timestamps: list[datetime], values: np.ndarray) -> dict[str, float]:
    by_day: dict[str, list[float]] = {}
    for ts, v in zip(timestamps, values):
        # Nur Wachfenster nutzen, damit der Median nicht selbst dippt.
        if 9 <= ts.hour < 21:
            by_day.setdefault(ts.date().isoformat(), []).append(float(v))
    if not by_day:
        # Fallback: gleicher Median aus allen Werten — wir verwenden hier
        # bewusst keinen leeren dict, damit der Aufrufer einen sinnvollen
        # Default findet.
        for ts, v in zip(timestamps, values):
            by_day.setdefault(ts.date().isoformat(), []).append(float(v))
    return {day: float(np.median(np.array(values_per_day))) for day, values_per_day in by_day.items()}


def _normalize_age(age_years: float | None) -> float:
    if age_years is None:
        return 0.0
    return (float(age_years) - _AGE_REF) / _AGE_SPAN
