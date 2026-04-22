from __future__ import annotations

"""Light-weight night / day-rest classifier for Hilo measurements.

Approach
--------
Per Hilo monthly report we receive two pieces of information:

1. a list of measurements with their timestamp and the usual vital values
   (systolic, diastolic, heart rate), and
2. an aggregated count of how many of those measurements were classified as
   ``day_rest`` vs. ``night`` in the original PDF summary block.

The PDF does not label individual rows, so the per-row classification is
*weakly supervised*: we know the target class distribution for the month but
not the class of a specific row. The classifier combines two signals:

* a small logistic-regression model that learns from heuristic weak labels
  (nighttime hours and typical nocturnal BP/HR drop), and
* the monthly class distribution from the PDF which is used as a hard
  constraint during inference.

Inference is a two-step procedure:

* the model produces a scalar night-likelihood score per measurement, and
* measurements are then assigned to ``night`` or ``day_rest`` so that the
  total night count matches the PDF (when known). This guarantees that the
  per-row output is internally consistent with the PDF summary even for
  borderline cases where the heuristic alone would be wrong.

The classifier is intentionally shallow; it is documented as an **estimation**
rather than a clinical sleep detection.
"""

import math
from dataclasses import dataclass

import numpy as np


NIGHT_HOUR_START = 22
NIGHT_HOUR_END = 6  # exclusive

# Clamp training iterations — with the few dozen rows per month we converge
# well within this budget and want to stay cheap enough for every request.
_TRAIN_ITERATIONS = 250
_LEARNING_RATE = 0.3
_L2_REG = 0.02


@dataclass
class ClassificationResult:
    is_night: bool
    score: float
    method: str


def _hour_fraction(iso_datetime: str) -> float:
    from datetime import datetime as _dt

    dt = _dt.fromisoformat(iso_datetime)
    return dt.hour + dt.minute / 60.0


def _weak_night_label(hour: float) -> int:
    # The Hilo PDF's own heuristic treats 22:00-06:00 as "night".
    return 1 if (hour >= NIGHT_HOUR_START or hour < NIGHT_HOUR_END) else 0


def _cyclic_hour_features(hour: float) -> tuple[float, float]:
    angle = 2 * math.pi * (hour / 24.0)
    return math.sin(angle), math.cos(angle)


def _extract_features(measurements: list[dict]) -> np.ndarray:
    if not measurements:
        return np.zeros((0, 7), dtype=np.float64)

    rows: list[list[float]] = []
    for item in measurements:
        hour = _hour_fraction(item["datetime"])
        sin_h, cos_h = _cyclic_hour_features(hour)
        rows.append(
            [
                sin_h,
                cos_h,
                # Distance to 02:00 (nominal sleep center) on the 24h circle.
                1.0 - math.cos(2 * math.pi * ((hour - 2.0) / 24.0)),
                float(item.get("systolic") or 0),
                float(item.get("diastolic") or 0),
                float(item.get("heart_rate") or 0),
                1.0,  # bias / intercept term
            ]
        )
    return np.asarray(rows, dtype=np.float64)


def _standardize(features: np.ndarray) -> np.ndarray:
    if features.size == 0:
        return features
    scaled = features.copy()
    # Do not scale the bias column (index 6).
    for col in range(scaled.shape[1] - 1):
        column = scaled[:, col]
        mean = float(np.mean(column))
        std = float(np.std(column))
        if std < 1e-9:
            scaled[:, col] = 0.0
        else:
            scaled[:, col] = (column - mean) / std
    return scaled


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30.0, 30.0)))


def _train_logistic(
    features: np.ndarray,
    labels: np.ndarray,
    sample_weights: np.ndarray,
) -> np.ndarray:
    n_features = features.shape[1]
    weights = np.zeros(n_features, dtype=np.float64)
    for _ in range(_TRAIN_ITERATIONS):
        logits = features @ weights
        probs = _sigmoid(logits)
        error = (probs - labels) * sample_weights
        grad = features.T @ error / max(1, features.shape[0])
        # Skip regularization of the bias term.
        reg = _L2_REG * weights.copy()
        reg[-1] = 0.0
        weights -= _LEARNING_RATE * (grad + reg)
    return weights


def _balance_weights(labels: np.ndarray) -> np.ndarray:
    weights = np.ones_like(labels, dtype=np.float64)
    total = labels.size
    pos = float(labels.sum())
    neg = total - pos
    if pos > 0 and neg > 0:
        weights[labels == 1] = total / (2.0 * pos)
        weights[labels == 0] = total / (2.0 * neg)
    return weights


class NightDayClassifier:
    """Train and apply the night / day-rest classifier per report.

    ``classify`` returns one :class:`ClassificationResult` per measurement in
    the same order as the input list. The method used for the final assignment
    is reported alongside each result so the frontend can distinguish
    heuristic fallback from constraint-driven assignment.
    """

    def classify(
        self,
        measurements: list[dict],
        *,
        expected_night_count: int | None = None,
    ) -> list[ClassificationResult]:
        if not measurements:
            return []

        features = _extract_features(measurements)
        scaled = _standardize(features)

        hours = np.array([_hour_fraction(m["datetime"]) for m in measurements])
        weak_labels = np.array([_weak_night_label(h) for h in hours], dtype=np.float64)

        method_prefix = "constraint"
        if weak_labels.sum() in (0, weak_labels.size):
            # Weak labels collapse to a single class — skip training and fall
            # back to the heuristic directly.
            scores = weak_labels.copy()
            method_prefix = "heuristic"
        else:
            weights = _train_logistic(scaled, weak_labels, _balance_weights(weak_labels))
            scores = _sigmoid(scaled @ weights)

        if expected_night_count is not None and 0 < expected_night_count < len(measurements):
            # Rank by score and mark the top-K as night so the monthly total
            # matches the PDF summary exactly.
            ranked = np.argsort(-scores, kind="stable")
            assignment = np.zeros(len(measurements), dtype=bool)
            assignment[ranked[: int(expected_night_count)]] = True
            method = f"{method_prefix}_constrained"
        else:
            assignment = scores >= 0.5
            method = method_prefix if method_prefix == "heuristic" else "score_threshold"

        return [
            ClassificationResult(
                is_night=bool(assignment[i]),
                score=float(scores[i]),
                method=method,
            )
            for i in range(len(measurements))
        ]
