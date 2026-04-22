from __future__ import annotations

"""Constraint-Logik: Roh-Scores gegen das PDF-Monatssummary kalibrieren.

Die Hilo-PDF nennt pro Monat die Anzahl der Messungen, die als ``night``
gezählt werden. Wir interpretieren diese Zahl als harten Constraint: das
Modell darf seine Score-Reihenfolge frei lernen, aber die Endzuordnung
muss in Summe diesen Wert treffen.

Vorgehen
--------
1. Sortiere alle Messungen absteigend nach Score.
2. Markiere die ersten ``expected_night_count`` als Nacht.
3. Falls ``expected_night_count`` ``None`` ist, fällt die Zuweisung auf
   einen Schwellenwert (Default 0,5) zurück.

Zusätzlich liefert :func:`assign_with_constraint` eine Konfidenzangabe pro
Messung — das ist nützlich für die Visualisierung im Frontend
(z. B. transparente Markierung an Übergangsstunden).
"""

from dataclasses import dataclass

import numpy as np


@dataclass
class Assignment:
    is_night: np.ndarray   # bool
    score: np.ndarray      # float in [0, 1]
    confidence: np.ndarray  # float in [0, 1]
    method: str            # "constraint_constrained" | "score_threshold"


def assign_with_constraint(
    scores: np.ndarray,
    *,
    expected_night_count: int | None,
    threshold: float = 0.5,
    method_prefix: str = "constraint",
) -> Assignment:
    """Setze das Monats-Constraint durch.

    Wenn ``expected_night_count`` <= 0 oder >= ``len(scores)`` ist, ist die
    Zuordnung trivial — wir markieren alles bzw. nichts als Nacht. Das
    vermeidet Edge-Cases im Sortier-Code.
    """

    n = scores.size
    if n == 0:
        return Assignment(
            is_night=np.zeros(0, dtype=bool),
            score=scores,
            confidence=np.zeros(0, dtype=np.float64),
            method=f"{method_prefix}_constrained",
        )

    if expected_night_count is None:
        is_night = scores >= threshold
        # Konfidenz: Abstand zum Schwellenwert, normalisiert auf [0, 1].
        confidence = np.minimum(1.0, np.abs(scores - threshold) / threshold)
        return Assignment(
            is_night=is_night,
            score=scores,
            confidence=confidence,
            method="score_threshold",
        )

    expected = int(expected_night_count)
    if expected <= 0:
        return Assignment(
            is_night=np.zeros(n, dtype=bool),
            score=scores,
            confidence=np.ones(n, dtype=np.float64),
            method=f"{method_prefix}_constrained",
        )
    if expected >= n:
        return Assignment(
            is_night=np.ones(n, dtype=bool),
            score=scores,
            confidence=np.ones(n, dtype=np.float64),
            method=f"{method_prefix}_constrained",
        )

    ranked = np.argsort(-scores, kind="stable")
    is_night = np.zeros(n, dtype=bool)
    is_night[ranked[:expected]] = True

    # Konfidenz pro Messung = Abstand zur Entscheidungsgrenze (= Score des
    # zuletzt als Nacht akzeptierten / des ersten als Tag eingestuften).
    boundary_high = float(scores[ranked[expected - 1]])
    boundary_low = float(scores[ranked[expected]])
    boundary = 0.5 * (boundary_high + boundary_low)
    confidence = np.minimum(1.0, np.abs(scores - boundary) / max(1e-6, abs(boundary - 0.5) + 0.25))

    return Assignment(
        is_night=is_night,
        score=scores,
        confidence=confidence,
        method=f"{method_prefix}_constrained",
    )
