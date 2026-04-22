from __future__ import annotations

"""Persistenz für trainierte Keras-Modelle und ihre Metadaten.

Ein Modellverzeichnis enthält:

* ``night_model.keras`` — das komplette Keras-Modell (Architektur + Gewichte).
* ``feature_stats.npz`` — die Mittelwerte/Standardabweichungen, mit denen
  die Eingabefeatures standardisiert wurden.
* ``model_card.json`` — Trainingsmetadaten (Datum, Sample-Count, Konfiguration,
  Verlust-Verlauf, Feature-Reihenfolge).

Die Persistenz ist atomisch: Wir schreiben in temporäre Dateien und
verschieben sie erst nach erfolgreichem Schreiben. So kann ein
unterbrochenes Training keine halben Modelle hinterlassen.
"""

import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

# Keras vor dem Import via Backend-Hint vorbereiten.
os.environ.setdefault("KERAS_BACKEND", "torch")
import keras  # noqa: E402

from .feature_engineering import FEATURE_NAMES
from .night_model import TrainingConfig


MODEL_FILE_NAME = "night_model.keras"
STATS_FILE_NAME = "feature_stats.npz"
CARD_FILE_NAME = "model_card.json"


@dataclass
class ModelCard:
    created_at: str
    sample_count: int
    positive_count: int
    feature_names: list[str] = field(default_factory=lambda: list(FEATURE_NAMES))
    training_config: dict = field(default_factory=dict)
    history_summary: dict = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LoadedModel:
    model: keras.Model
    feature_stats: dict[str, np.ndarray]
    card: ModelCard
    directory: Path


def default_model_dir() -> Path:
    """Default-Speicherort: ``backend/data/models/night_classifier``.

    Wir leiten den Pfad relativ zum Projekt ab, damit Tests (die in einem
    temporären cwd laufen können) das Verzeichnis zuverlässig finden.
    """

    here = Path(__file__).resolve()
    # backend/app/services/ml/model_store.py -> backend/data/models/night_classifier
    backend_root = here.parents[3]
    return backend_root / "data" / "models" / "night_classifier"


def save_model(
    directory: Path,
    *,
    model: keras.Model,
    feature_stats: dict[str, np.ndarray],
    card: ModelCard,
) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    # In ein temp-Verzeichnis schreiben, dann atomar verschieben.
    with tempfile.TemporaryDirectory(prefix="night_model_", dir=directory.parent) as tmp_root:
        tmp_dir = Path(tmp_root)
        model.save(str(tmp_dir / MODEL_FILE_NAME))
        np.savez(
            tmp_dir / STATS_FILE_NAME,
            mean=np.asarray(feature_stats["mean"], dtype=np.float64),
            std=np.asarray(feature_stats["std"], dtype=np.float64),
        )
        (tmp_dir / CARD_FILE_NAME).write_text(
            json.dumps(card.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Inhalt von tmp_dir nach directory verschieben.
        for file_name in (MODEL_FILE_NAME, STATS_FILE_NAME, CARD_FILE_NAME):
            target = directory / file_name
            if target.exists():
                target.unlink()
            shutil.move(str(tmp_dir / file_name), str(target))

    return directory


def has_model(directory: Path) -> bool:
    directory = Path(directory)
    return (
        (directory / MODEL_FILE_NAME).exists()
        and (directory / STATS_FILE_NAME).exists()
        and (directory / CARD_FILE_NAME).exists()
    )


def load_model(directory: Path) -> LoadedModel:
    directory = Path(directory)
    if not has_model(directory):
        raise FileNotFoundError(
            f"Kein vollständiges Modell unter {directory}. "
            "Trainiere das Modell zuerst über /api/ml/training/start."
        )
    model = keras.models.load_model(str(directory / MODEL_FILE_NAME))
    with np.load(directory / STATS_FILE_NAME) as data:
        stats = {"mean": data["mean"].astype(np.float64), "std": data["std"].astype(np.float64)}
    card_dict = json.loads((directory / CARD_FILE_NAME).read_text(encoding="utf-8"))
    card = ModelCard(
        created_at=card_dict.get("created_at", ""),
        sample_count=int(card_dict.get("sample_count", 0)),
        positive_count=int(card_dict.get("positive_count", 0)),
        feature_names=list(card_dict.get("feature_names", FEATURE_NAMES)),
        training_config=dict(card_dict.get("training_config", {})),
        history_summary=dict(card_dict.get("history_summary", {})),
        notes=str(card_dict.get("notes", "")),
    )
    return LoadedModel(model=model, feature_stats=stats, card=card, directory=directory)


def build_model_card(
    *,
    sample_count: int,
    positive_count: int,
    config: TrainingConfig,
    history_summary: dict,
    notes: str = "",
) -> ModelCard:
    return ModelCard(
        created_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        sample_count=sample_count,
        positive_count=positive_count,
        feature_names=list(FEATURE_NAMES),
        training_config=asdict(config),
        history_summary=history_summary,
        notes=notes,
    )
