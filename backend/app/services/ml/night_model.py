from __future__ import annotations

"""Keras-MLP für die Nacht/Tag-Klassifikation.

Architekturbegründung
---------------------
Wir haben pro Bericht typischerweise 50–80 Messungen. Für so kleine Datasets
sind tiefe Architekturen riskant — wir verwenden bewusst ein flaches MLP mit
Dropout und L2-Regularisierung. Eingabe sind die Features aus
:mod:`feature_engineering`. Ausgabe ist die Wahrscheinlichkeit, dass eine
Messung in eine Nacht-Episode fällt.

Trainingsstrategie
------------------
* Schwache Labels (Stunde-basiert + Telefon-Hard-Rule) als initialer Lehrer.
* Sample-Weights mildern unsichere Übergangsstunden (siehe
  :func:`feature_engineering._label_confidence`).
* :class:`keras.callbacks.EarlyStopping` stoppt nach 12 Epochen ohne
  Verbesserung von ``val_loss`` und stellt die besten Gewichte wieder her.
* :class:`keras.callbacks.ModelCheckpoint` schreibt das beste Modell auf die
  Festplatte, sodass Trainings unterbrochen werden können.
* Trainings sind reproduzierbar: ``keras.utils.set_random_seed`` wird mit dem
  übergebenen Seed gesetzt.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

# Backend-Auswahl muss VOR dem Keras-Import stehen.
os.environ.setdefault("KERAS_BACKEND", "torch")

import numpy as np

import keras  # noqa: E402  (Reihenfolge ist wegen KERAS_BACKEND wichtig)
from keras import callbacks as keras_callbacks
from keras import layers, regularizers


@dataclass
class TrainingConfig:
    epochs: int = 120
    batch_size: int = 16
    validation_split: float = 0.2
    learning_rate: float = 1e-3
    l2_strength: float = 1e-3
    dropout: float = 0.25
    hidden_units: tuple[int, int] = (32, 16)
    early_stopping_patience: int = 12
    seed: int = 1337


@dataclass
class TrainingHistory:
    epochs_run: int
    final_loss: float
    final_val_loss: float | None
    best_val_loss: float | None
    history: dict[str, list[float]] = field(default_factory=dict)


def build_model(input_dim: int, config: TrainingConfig) -> keras.Model:
    """Sequenzielles MLP mit Dropout + L2.

    Output ist eine Sigmoid-Aktivierung — die Klassifikation passiert
    extern (Threshold oder Constraint).
    """

    reg = regularizers.l2(config.l2_strength)
    inputs = keras.Input(shape=(input_dim,), name="features")
    x = layers.Dense(config.hidden_units[0], activation="relu", kernel_regularizer=reg)(inputs)
    x = layers.Dropout(config.dropout)(x)
    x = layers.Dense(config.hidden_units[1], activation="relu", kernel_regularizer=reg)(x)
    x = layers.Dropout(config.dropout)(x)
    outputs = layers.Dense(1, activation="sigmoid", name="night_probability")(x)
    model = keras.Model(inputs=inputs, outputs=outputs, name="night_day_mlp")
    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=config.learning_rate),
        loss="binary_crossentropy",
        metrics=["accuracy"],
        weighted_metrics=["accuracy"],
    )
    return model


def fit_model(
    model: keras.Model,
    features: np.ndarray,
    labels: np.ndarray,
    sample_weights: np.ndarray,
    config: TrainingConfig,
    *,
    checkpoint_path: Path | None = None,
) -> TrainingHistory:
    """Trainiere das Modell mit EarlyStopping + Checkpoint.

    Bei sehr kleinen Datasets (<25 Samples) wird automatisch ohne
    Validation-Split trainiert, weil eine sinnvolle Aufteilung nicht möglich
    ist. EarlyStopping läuft dann auf ``loss`` statt ``val_loss``.
    """

    keras.utils.set_random_seed(config.seed)

    use_validation = labels.shape[0] >= 25 and config.validation_split > 0.0
    monitor = "val_loss" if use_validation else "loss"

    cbs: list[keras_callbacks.Callback] = [
        keras_callbacks.EarlyStopping(
            monitor=monitor,
            patience=config.early_stopping_patience,
            restore_best_weights=True,
            mode="min",
            min_delta=1e-4,
        )
    ]
    if checkpoint_path is not None:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        cbs.append(
            keras_callbacks.ModelCheckpoint(
                filepath=str(checkpoint_path),
                monitor=monitor,
                save_best_only=True,
                save_weights_only=False,
                mode="min",
            )
        )

    fit_kwargs: dict[str, object] = dict(
        x=features,
        y=labels,
        sample_weight=sample_weights,
        batch_size=min(config.batch_size, max(1, labels.shape[0])),
        epochs=config.epochs,
        verbose=0,
        callbacks=cbs,
        shuffle=True,
    )
    if use_validation:
        fit_kwargs["validation_split"] = config.validation_split

    history = model.fit(**fit_kwargs)
    hist = {k: [float(x) for x in v] for k, v in history.history.items()}
    val_losses = hist.get("val_loss") or []
    return TrainingHistory(
        epochs_run=len(hist.get("loss") or []),
        final_loss=float((hist.get("loss") or [float("nan")])[-1]),
        final_val_loss=float(val_losses[-1]) if val_losses else None,
        best_val_loss=float(min(val_losses)) if val_losses else None,
        history=hist,
    )


def predict_scores(model: keras.Model, features: np.ndarray) -> np.ndarray:
    """Inferenz, gibt Wahrscheinlichkeiten in [0,1] zurück."""

    if features.size == 0:
        return np.zeros(0, dtype=np.float64)
    raw = model.predict(features, verbose=0)
    return np.asarray(raw, dtype=np.float64).reshape(-1)
