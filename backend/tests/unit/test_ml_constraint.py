from __future__ import annotations

import numpy as np

from app.services.ml.constraint import assign_with_constraint


def test_assign_with_constraint_picks_top_k_by_score() -> None:
    scores = np.array([0.1, 0.9, 0.4, 0.8, 0.2])
    result = assign_with_constraint(scores, expected_night_count=2, method_prefix="keras")
    expected = np.array([False, True, False, True, False])
    assert np.array_equal(result.is_night, expected)
    assert result.method == "keras_constrained"


def test_assign_with_constraint_falls_back_to_threshold_when_count_missing() -> None:
    scores = np.array([0.4, 0.6, 0.49])
    result = assign_with_constraint(scores, expected_night_count=None, threshold=0.5)
    assert np.array_equal(result.is_night, np.array([False, True, False]))
    assert result.method == "score_threshold"


def test_assign_with_constraint_handles_edge_counts() -> None:
    scores = np.array([0.3, 0.7])
    none_result = assign_with_constraint(scores, expected_night_count=0)
    all_result = assign_with_constraint(scores, expected_night_count=5)
    assert not none_result.is_night.any()
    assert all_result.is_night.all()
    # Beide nutzen den constrained-Methodenpfad, nicht den Schwellenwert.
    assert none_result.method.endswith("_constrained")
    assert all_result.method.endswith("_constrained")


def test_assign_with_constraint_confidence_is_high_for_clear_winners() -> None:
    scores = np.array([0.05, 0.95, 0.5, 0.55])
    result = assign_with_constraint(scores, expected_night_count=2)
    # Die zwei eindeutigen Extreme sollen sicherer sein als die beiden
    # Werte um die Entscheidungsgrenze.
    assert result.confidence[1] > result.confidence[3]
    assert result.confidence[0] > result.confidence[2]


def test_assign_with_constraint_handles_empty() -> None:
    result = assign_with_constraint(np.array([]), expected_night_count=3)
    assert result.is_night.size == 0
    assert result.confidence.size == 0
