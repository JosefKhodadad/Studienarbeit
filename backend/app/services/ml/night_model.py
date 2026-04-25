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


# --- Architektur-Visualisierung ------------------------------------------
#
# Wir bauen das Diagramm bewusst selbst mit matplotlib statt mit
# ``keras.utils.plot_model``, weil letzteres ``pydot`` und das System-Tool
# ``graphviz`` voraussetzt, das auf einem Windows-OneDrive-Pfad nicht
# zuverlässig installiert werden kann. matplotlib ist bereits Standard-Tool
# im Wissenschaftskontext und wird in ``requirements.txt`` gepflegt.

_LAYER_COLORS = {
    "InputLayer": "#cce5ff",
    "Dense": "#ffd9b3",
    "Dropout": "#dddddd",
    "BatchNormalization": "#d9f2d9",
    "Activation": "#fff2cc",
}


def _layer_summaries(model: keras.Model) -> list[dict]:
    """Sammle pro Layer die Felder, die das Diagramm anzeigt.

    ``output.shape`` wird best-effort gelesen — bei sequentiellen Modellen
    ohne fertigen Build kann das fehlschlagen; in dem Fall fallen wir auf
    einen Platzhalter zurück, anstatt zu crashen.
    """

    summaries: list[dict] = []
    for layer in model.layers:
        try:
            shape = tuple(layer.output.shape)
        except (AttributeError, ValueError):
            shape = None
        try:
            params = int(layer.count_params())
        except Exception:
            params = 0
        activation = getattr(layer, "activation", None)
        activation_name = getattr(activation, "__name__", None) if activation else None
        summaries.append(
            {
                "name": layer.name,
                "class_name": layer.__class__.__name__,
                "shape": shape,
                "params": params,
                "activation": activation_name,
            }
        )
    return summaries


def plot_architecture(
    model: keras.Model,
    output_path: str | Path,
    *,
    title: str | None = None,
    dpi: int = 150,
) -> Path:
    """Speichere ein Box-Diagramm der NN-Architektur als PNG.

    Pro Layer werden Klassenname, Layer-Name, Output-Shape, Aktivierung und
    Parameter-Anzahl gerendert. Das Diagramm liest von oben nach unten in
    Datenflussrichtung (Input oben, Output unten) — das entspricht der in
    Keras üblichen ``model.summary()``-Reihenfolge.

    ``matplotlib`` ist eine optionale Abhängigkeit (siehe
    ``backend/requirements.txt``); fehlt sie, wird ein verständlicher
    ImportError mit Installations-Hinweis geworfen.
    """

    try:
        import matplotlib

        matplotlib.use("Agg")  # headless-tauglich (kein GUI nötig)
        import matplotlib.patches as patches
        import matplotlib.pyplot as plt
    except ImportError as exc:  # noqa: BLE001
        raise ImportError(
            "matplotlib ist für plot_architecture() erforderlich. "
            "Bitte installieren mit:  pip install matplotlib"
        ) from exc

    summaries = _layer_summaries(model)
    if not summaries:
        raise ValueError("Modell enthält keine Layer — nichts zu plotten.")

    n = len(summaries)
    box_height = 0.9
    box_width = 7.0
    box_x = 1.5
    fig_width = 10.0
    fig_height = max(4.5, 1.4 * n + 1.2)

    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    ax.set_xlim(0, fig_width)
    ax.set_ylim(0, n * 1.4 + 1.0)
    ax.axis("off")

    total_params = sum(item["params"] for item in summaries)

    # Layer von oben (Input) nach unten (Output) zeichnen.
    for i, info in enumerate(summaries):
        y = (n - 1 - i) * 1.4 + 0.3
        color = _LAYER_COLORS.get(info["class_name"], "#f0e6ff")

        rect = patches.FancyBboxPatch(
            (box_x, y),
            box_width,
            box_height,
            boxstyle="round,pad=0.05",
            edgecolor="#333333",
            facecolor=color,
            linewidth=1.0,
        )
        ax.add_patch(rect)

        shape_text = "shape=" + (str(info["shape"]) if info["shape"] is not None else "?")
        activation_text = (
            f" | act={info['activation']}" if info["activation"] else ""
        )
        params_text = f"params={info['params']:,}".replace(",", ".")
        line1 = f"{info['class_name']}  ·  {info['name']}"
        line2 = f"{shape_text}{activation_text}  ·  {params_text}"

        ax.text(
            box_x + box_width / 2,
            y + box_height * 0.65,
            line1,
            ha="center",
            va="center",
            fontsize=10,
            weight="bold",
        )
        ax.text(
            box_x + box_width / 2,
            y + box_height * 0.30,
            line2,
            ha="center",
            va="center",
            fontsize=9,
            color="#333333",
        )

        if i < n - 1:
            arrow_top_y = y
            arrow_bottom_y = y - 0.4
            ax.annotate(
                "",
                xy=(box_x + box_width / 2, arrow_bottom_y),
                xytext=(box_x + box_width / 2, arrow_top_y),
                arrowprops=dict(arrowstyle="->", color="#555555", lw=1.4),
            )

    header = title or f"NN-Architektur · {model.name}"
    subtitle = (
        f"Eingabe: {summaries[0]['shape']} · "
        f"Ausgabe: {summaries[-1]['shape']} · "
        f"Trainierbare Parameter gesamt: {total_params:,}".replace(",", ".")
    )
    ax.set_title(f"{header}\n{subtitle}", fontsize=12, weight="bold", pad=14)

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi)
    plt.close(fig)
    return out_path


def dump_architecture_summary(
    model: keras.Model,
    output_path: str | Path,
) -> Path:
    """Schreibe ``model.summary()`` als Textdatei (Tabellenform).

    Ergänzt das PNG-Diagramm um die offizielle Keras-Übersicht — nützlich
    für die Studienarbeit, weil sie Parameteranzahl und Output-Shape je
    Layer in der von Keras kanonisch erzeugten Form dokumentiert.
    """

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    model.summary(print_fn=lambda line: lines.append(str(line)))
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path
