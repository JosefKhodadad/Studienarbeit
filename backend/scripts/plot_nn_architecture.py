"""Standalone-Skript: visualisiert die Architektur des Nacht/Tag-MLPs.

Aufruf (vom Backend-Verzeichnis aus, venv aktiviert):

    python scripts/plot_nn_architecture.py

Optionale Argumente:

    --output PATH       Zielpfad für die PNG-Datei.
                        Default: ``backend/data/nn_architecture.png``
    --summary PATH      Zielpfad für die Text-Zusammenfassung
                        (``model.summary()``).
                        Default: gleicher Ordner wie ``--output``.
    --input-dim N       Anzahl Eingabe-Features. Default: aktuelle Länge
                        von ``FEATURE_NAMES`` aus dem Feature-Engineering.

Das Skript baut das Modell mit den im Code definierten Default-Hyper-
parametern (siehe ``TrainingConfig`` in ``app/services/ml/night_model.py``)
und erzeugt zwei Artefakte:

* ein PNG-Diagramm der gestapelten Layer,
* eine Textdatei mit der von Keras erzeugten ``model.summary()``-Tabelle.

Beides ist für die Studienarbeit gedacht, um die Architektur sauber
nachvollziehbar zu dokumentieren.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


# Backend-Auswahl muss VOR dem ersten Keras-Import stehen.
os.environ.setdefault("KERAS_BACKEND", "torch")

# Pfad-Setup: ``app`` muss importierbar sein, wenn das Skript direkt
# (nicht als Modul) ausgeführt wird.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.services.ml.feature_engineering import FEATURE_NAMES  # noqa: E402
from app.services.ml.night_model import (  # noqa: E402
    TrainingConfig,
    build_model,
    dump_architecture_summary,
    plot_architecture,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plotte die NN-Architektur.")
    default_dir = _BACKEND_ROOT / "data"
    parser.add_argument(
        "--output",
        default=str(default_dir / "nn_architecture.png"),
        help="Zielpfad für die PNG-Datei.",
    )
    parser.add_argument(
        "--summary",
        default=None,
        help=(
            "Zielpfad für model.summary(). Default: gleicher Ordner und "
            "Basisname wie --output, mit Endung .txt."
        ),
    )
    parser.add_argument(
        "--input-dim",
        type=int,
        default=len(FEATURE_NAMES),
        help="Anzahl Eingabe-Features (Default: aktuelle Feature-Liste).",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    config = TrainingConfig()
    model = build_model(input_dim=args.input_dim, config=config)

    png_path = Path(args.output)
    summary_path = (
        Path(args.summary)
        if args.summary
        else png_path.with_suffix(".txt")
    )

    plot_architecture(model, png_path)
    dump_architecture_summary(model, summary_path)

    print(f"NN-Architektur (PNG):     {png_path}")
    print(f"NN-Architektur (Summary): {summary_path}")
    print()
    print("Modell-Summary (Konsolen-Ausgabe):")
    model.summary()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
