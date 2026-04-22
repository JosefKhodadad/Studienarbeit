from __future__ import annotations

"""Adapter: nutzt das Keras-Modell hinter der bekannten Klassifier-API.

Der :class:`KerasNightClassifier` hat dieselbe Signatur wie der bisherige
:class:`app.services.classification_service.NightDayClassifier`. So bleibt die
Schnittstelle zum :class:`ImportService` stabil und der Klassifier ist
austauschbar.

Wenn kein trainiertes Modell vorhanden ist (oder Keras/Torch fehlt), fällt der
Adapter automatisch auf den klassischen logistischen Klassifier zurück. Damit
funktioniert das System auch in Umgebungen ohne ML-Stack — der Output ist
dann derselbe wie vor der Erweiterung.
"""

from dataclasses import dataclass
from pathlib import Path
import logging
import threading

import numpy as np

from app.services.classification_service import (
    ClassificationResult,
    NightDayClassifier,
)

from .constraint import assign_with_constraint
from .feature_engineering import (
    FEATURE_NAMES,
    NightDayReference,
    build_features,
    standardize,
)
from .model_store import LoadedModel, default_model_dir, has_model, load_model


_logger = logging.getLogger(__name__)


@dataclass
class _CachedModel:
    loaded: LoadedModel
    mtime: float


class KerasNightClassifier:
    """Frontend-stabile Klassifikation auf Basis des Keras-Modells.

    Die Schnittstelle ist deckungsgleich mit dem alten Klassifier — der
    :class:`ImportService` muss nicht angefasst werden, sobald wir hier
    instanzieren. Der Fallback nutzt den alten logistischen Klassifier.
    """

    def __init__(
        self,
        *,
        model_dir: Path | None = None,
        fallback: NightDayClassifier | None = None,
    ) -> None:
        self._model_dir = Path(model_dir) if model_dir else default_model_dir()
        self._fallback = fallback or NightDayClassifier()
        self._cache: _CachedModel | None = None
        self._lock = threading.Lock()

    # --- öffentliche API ------------------------------------------------

    def classify(
        self,
        measurements: list[dict],
        *,
        expected_night_count: int | None = None,
        age_years: float | None = None,
        reference: NightDayReference | None = None,
    ) -> list[ClassificationResult]:
        if not measurements:
            return []

        loaded = self._get_loaded_model()
        if loaded is None:
            return self._fallback.classify(
                measurements, expected_night_count=expected_night_count
            )

        try:
            scores = self._predict(
                loaded, measurements, age_years=age_years, reference=reference
            )
        except Exception as exc:
            _logger.exception("Keras-Inferenz fehlgeschlagen, falle zurück: %s", exc)
            return self._fallback.classify(
                measurements, expected_night_count=expected_night_count
            )

        assignment = assign_with_constraint(
            scores,
            expected_night_count=expected_night_count,
            method_prefix="keras",
        )
        return [
            ClassificationResult(
                is_night=bool(assignment.is_night[i]),
                score=float(assignment.score[i]),
                method=assignment.method,
            )
            for i in range(len(measurements))
        ]

    def model_available(self) -> bool:
        return has_model(self._model_dir)

    def get_model_card(self) -> dict | None:
        loaded = self._get_loaded_model()
        if loaded is None:
            return None
        return loaded.card.to_dict()

    def invalidate_cache(self) -> None:
        with self._lock:
            self._cache = None

    # --- intern --------------------------------------------------------

    def _get_loaded_model(self) -> LoadedModel | None:
        if not has_model(self._model_dir):
            return None
        with self._lock:
            mtime = (self._model_dir / "night_model.keras").stat().st_mtime
            if self._cache is None or self._cache.mtime != mtime:
                try:
                    loaded = load_model(self._model_dir)
                except Exception as exc:
                    _logger.exception("Modell konnte nicht geladen werden: %s", exc)
                    self._cache = None
                    return None
                # Sanity-Check: Wurde das gespeicherte Modell mit den aktuellen
                # Features trainiert? Wenn nicht (z. B. nach einer Erweiterung
                # der Feature-Liste), ignorieren wir es bis zum Re-Training und
                # nutzen den klassischen Fallback. So bleibt das System nach
                # Code-Updates immer noch antwortfähig.
                stored_features = tuple(loaded.card.feature_names or ())
                if stored_features != FEATURE_NAMES:
                    _logger.warning(
                        "Modell hat veraltete Feature-Liste (%d statt %d Spalten)"
                        " — verwende Heuristik bis zum nächsten Training.",
                        len(stored_features), len(FEATURE_NAMES),
                    )
                    self._cache = None
                    return None
                self._cache = _CachedModel(loaded=loaded, mtime=mtime)
            return self._cache.loaded if self._cache else None

    def _predict(
        self,
        loaded: LoadedModel,
        measurements: list[dict],
        *,
        age_years: float | None,
        reference: NightDayReference | None,
    ) -> np.ndarray:
        from .night_model import predict_scores  # lazy import

        fm = build_features(measurements, age_years=age_years, reference=reference)
        if fm.matrix.size == 0:
            return np.zeros(0, dtype=np.float64)
        scaled, _ = standardize(fm.matrix, stats=loaded.feature_stats)
        return predict_scores(loaded.model, scaled.astype(np.float32))
