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
#
# Die letzten drei Features kapseln den Vergleich mit den Nacht-/Tag-Referenz-
# werten aus der PDF-Übersichtstabelle (siehe ``NightDayReference``). Liegen
# keine Referenzen vor (z.B. beim CSV-Training ohne PDF-Kontext), bleiben sie
# auf 0 — das Modell verliert dann Signal, behält aber seine Eingabeform.
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
    "night_ref_match",      # Nähe zum Nacht-Mittel (einseitig nach oben)
    "night_min_match",      # Nähe zum Nacht-Minimum (Tiefschlaf-Indikator)
    "day_ref_match",        # Nähe zum Tag/Ruhe-Mittel (symmetrisch)
    "night_ref_outlier",    # 1 = deutlich über Nacht-Mittel (Aufwach-Kandidat)
)

# Indizes der Features, die NICHT pro Feature standardisiert werden, weil sie
# bereits in einem festen Wertebereich liegen ([0,1] oder normiert).
_NO_STANDARDIZE_INDICES: frozenset[int] = frozenset(
    {0, 1, 2, 3, 4, 5, 6, 16, 17, 18, 19, 20}
)

# 2,5 % Toleranz auf den Nacht-Mittelwert, einseitig nach oben: ein Messwert
# leicht *unter* dem Nachtmittel ist nachts völlig plausibel (Dipping), nur
# oberhalb der Grenze wird es zum Aufwach-Kandidaten.
NIGHT_REFERENCE_TOLERANCE = 0.025

# Außerhalb von ``OUTLIER_TOLERANCE_FACTOR × Toleranz`` markieren wir die
# Messung als "klar außerhalb" — diese Hilfsgröße wird zusätzlich an den
# Sleep-Phase-Detektor weitergereicht, um Aufwach-Episoden zu erkennen.
OUTLIER_TOLERANCE_FACTOR = 3.0

# Fenstergröße für lokale Trends (vor und nach der aktuellen Messung).
_LOCAL_WINDOW = 3

# Typischer Erwachsenen-Wertebereich für die Alter-Normierung.
_AGE_REF = 60.0
_AGE_SPAN = 40.0

# Weak-Label-Heuristik: Stunden, in denen Schlaf wahrscheinlich ist.
_WEAK_NIGHT_START = 22
_WEAK_NIGHT_END = 6  # exklusiv


@dataclass(frozen=True)
class NightDayReference:
    """Referenzwerte aus der PDF-Übersichtstabelle (Seite 1).

    Felder dürfen ``None`` sein, wenn die PDF die Spalte nicht enthielt; das
    Feature-Engineering prüft das und gibt dann einen neutralen 0-Wert aus.

    ``tolerance_fraction`` ist der relative Toleranzbereich um den Nacht-
    Mittelwert (Default 3 %, Vorgabe aus der Aufgabenstellung).
    """

    night_sbp_mean: float | None = None
    night_dbp_mean: float | None = None
    night_hr_mean: float | None = None
    night_sbp_min: float | None = None
    night_dbp_min: float | None = None
    night_hr_min: float | None = None
    night_sbp_max: float | None = None
    day_sbp_mean: float | None = None
    day_dbp_mean: float | None = None
    day_hr_mean: float | None = None
    tolerance_fraction: float = NIGHT_REFERENCE_TOLERANCE

    def has_night_means(self) -> bool:
        return all(
            value is not None and value > 0
            for value in (self.night_sbp_mean, self.night_dbp_mean, self.night_hr_mean)
        )

    def has_day_means(self) -> bool:
        return all(
            value is not None and value > 0
            for value in (self.day_sbp_mean, self.day_dbp_mean, self.day_hr_mean)
        )

    def has_night_mins(self) -> bool:
        # SBP-Min reicht als Basis; DBP/HR-Min werden in ``_night_min_match``
        # weggelassen, wenn sie fehlen.
        return self.night_sbp_min is not None and self.night_sbp_min > 0


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
    night_ref_outlier: np.ndarray   # shape (n_samples,), 0..1 — Sleep-Phase nutzt dies


# --- öffentliche Funktionen ----------------------------------------------

def build_features(
    measurements: list[dict],
    *,
    age_years: float | None = None,
    weak_label_method: str = "rule",
    reference: NightDayReference | None = None,
) -> FeatureMatrix:
    """Erzeuge Features + schwache Labels für eine Liste von Messungen.

    Die Reihenfolge der Zeilen entspricht der Reihenfolge in
    ``measurements``. Eingabe-Listen werden nicht mutiert.

    ``reference`` enthält die aus der PDF-Übersichtstabelle geparsten Nacht-/
    Tag-Mittelwerte. Sind sie vorhanden, fließen sie als zusätzliche Features
    (``night_ref_match``, ``day_ref_match``, ``night_ref_outlier``) ein und
    verstärken/abschwächen das schwache Label entsprechend.
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
            night_ref_outlier=np.zeros(0, dtype=np.float64),
        )

    ref = reference or NightDayReference()
    has_night_ref = ref.has_night_means()
    has_day_ref = ref.has_day_means()
    has_night_min = ref.has_night_mins()

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
    night_outlier = np.zeros(n, dtype=np.float64)
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

        # Vergleich mit den PDF-Referenzwerten: Wir bilden die relative
        # Abweichung zu Nacht- bzw. Tag-Mittel (in Vielfachen der Toleranz)
        # und mappen sie auf einen Match-Score in [0,1]. Score 1 = innerhalb
        # der Toleranz, fällt mit wachsender Abweichung exponentiell ab.
        night_match = 0.0
        night_min_score = 0.0
        day_match = 0.0
        outlier_flag = 0.0
        if has_night_ref:
            night_match, outlier_flag = _reference_match(
                sbp, dbp, hr,
                ref.night_sbp_mean or 0.0,
                ref.night_dbp_mean or 0.0,
                ref.night_hr_mean or 0.0,
                ref.tolerance_fraction,
                upward_only=True,
            )
        if has_night_min:
            night_min_score = _night_min_match(sbp, dbp, hr, ref)
        if has_day_ref:
            day_match, _ = _reference_match(
                sbp, dbp, hr,
                ref.day_sbp_mean or 0.0,
                ref.day_dbp_mean or 0.0,
                ref.day_hr_mean or 0.0,
                ref.tolerance_fraction,
                upward_only=False,
            )

        matrix[original_pos] = (
            sin_h, cos_h, sin_w, cos_w, dist_sleep_center,
            is_phone, is_armband,
            sbp, dbp, hr,
            sbp_delta, dbp_delta, hr_delta,
            sbp_trend_sorted[sorted_pos], hr_trend_sorted[sorted_pos],
            minutes_since_sorted[sorted_pos],
            age_norm,
            night_match, night_min_score, day_match, outlier_flag,
        )

        # Schwaches Label: Standard-Regel (22..06) ist die Basis. Telefonmessungen
        # erzwingen Tag-Label, weil die Hilo-App diese nur im Wachzustand
        # erlaubt — eine harte Domäne-Regel.
        weak = _weak_night_label(hour, weak_label_method)
        if is_phone == 1.0:
            weak = 0.0

        # Referenzwerte schärfen das schwache Label: Innerhalb der Nacht-Bande
        # gewinnt das Nacht-Label an Sicherheit; ein klarer Outlier zur
        # Nacht-Referenz im typischen Schlaffenster wird tendenziell als
        # Aufwach-Punkt (also Tag) interpretiert. Telefonmessungen bleiben
        # weiterhin Tag (siehe oben).
        confidence = _label_confidence(hour)
        if has_night_ref and is_phone != 1.0:
            in_night_band = night_match > 0.7 and outlier_flag < 0.5
            in_day_band = day_match > 0.7 and has_day_ref
            if in_night_band:
                weak = 1.0
                confidence = max(confidence, 0.9)
            elif outlier_flag >= 1.0 and (hour >= _WEAK_NIGHT_START or hour < _WEAK_NIGHT_END):
                # Klar außerhalb der Nachtreferenz, obwohl die Uhrzeit im
                # üblichen Nachtfenster liegt: behandeln wir als
                # Aufwach-Kandidat (schwächeres Label, geringere Konfidenz).
                weak = 0.0
                confidence = max(confidence, 0.5)
            elif in_day_band and 7.0 <= hour < 22.0:
                weak = 0.0
                confidence = max(confidence, 0.85)
        weak_labels[original_pos] = weak

        # Sample-Gewichte: nahe der Mitte der Nachtschiene bzw. weit
        # tagsüber sind die Labels sicherer; an den Rändern (06–08, 20–22)
        # weniger sicher. Diese weiche Gewichtung wirkt regularisierend.
        sample_weights[original_pos] = confidence

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
        night_outlier[original_pos] = outlier_flag

    return FeatureMatrix(
        matrix=matrix,
        weak_labels=weak_labels,
        sample_weights=sample_weights,
        hours=hours,
        timestamps=timestamps_orig,
        measurement_types=types_orig,
        night_drop_score=night_drop,
        night_ref_outlier=night_outlier,
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


def _reference_match(
    sbp: float,
    dbp: float,
    hr: float,
    ref_sbp: float,
    ref_dbp: float,
    ref_hr: float,
    tolerance_fraction: float,
    *,
    upward_only: bool = True,
) -> tuple[float, float]:
    """Bewerte Nähe zu einer Referenz (Nacht- oder Tag-Mittelwerte).

    ``upward_only=True`` (Default): nur Überschreitung zählt. Werte *unter*
    dem Nachtmittel sind nachts erwünscht (Dipping) und dürfen den Score
    nicht drücken. Für Tag-Ruhe rufen wir mit ``upward_only=False`` auf.

    Rückgabe: ``(match_score, outlier_flag)``. Outlier wird ebenfalls nur
    ausgelöst, wenn der Wert oberhalb der Toleranz liegt — das ist der
    Aufwach-Kandidat.
    """

    def _rel_over(value: float, ref: float) -> float:
        if ref <= 0.0:
            return 0.0
        diff = value - ref if upward_only else abs(value - ref)
        return max(0.0, diff) / ref

    distances = (
        _rel_over(sbp, ref_sbp),
        _rel_over(dbp, ref_dbp),
        _rel_over(hr, ref_hr),
    )

    tol = max(1e-6, float(tolerance_fraction))
    excess = max(d / tol for d in distances)
    match_score = float(math.exp(-max(0.0, excess - 1.0)))
    outlier_flag = 1.0 if excess >= OUTLIER_TOLERANCE_FACTOR else 0.0
    return match_score, outlier_flag


def _night_min_match(
    sbp: float,
    dbp: float,
    hr: float,
    ref: NightDayReference,
) -> float:
    """Näheschatzung zu den Nacht-Minima (Tiefschlaf-Indikator).

    Ein Messwert nahe oder unter dem patientenspezifischen Nacht-Minimum ist
    ein starkes Schlaf-Signal. Wir gewichten SBP am höchsten (stabilstes PDF-
    Signal), gefolgt von DBP und HR. Fehlende Min-Werte werden übersprungen.
    """

    tol = max(1e-6, float(ref.tolerance_fraction))

    def _closeness(value: float, ref_min: float | None) -> float | None:
        if ref_min is None or ref_min <= 0.0:
            return None
        band = ref_min * tol
        upper = ref_min + 2.0 * band  # "Tiefschlaf"-Band: bis ~2x Toleranz über Min
        if value <= upper:
            return 1.0
        return float(math.exp(-(value - upper) / max(band, 1e-6)))

    parts: list[tuple[float, float]] = []
    sbp_close = _closeness(sbp, ref.night_sbp_min)
    if sbp_close is not None:
        parts.append((sbp_close, 0.5))
    dbp_close = _closeness(dbp, ref.night_dbp_min)
    if dbp_close is not None:
        parts.append((dbp_close, 0.3))
    hr_close = _closeness(hr, ref.night_hr_min)
    if hr_close is not None:
        parts.append((hr_close, 0.2))
    if not parts:
        return 0.0
    total_weight = sum(w for _, w in parts)
    return float(sum(v * w for v, w in parts) / total_weight)
