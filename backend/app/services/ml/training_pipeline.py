from __future__ import annotations

"""Lädt CSV-Trainingsdaten und führt das Modelltraining aus.

CSV-Format (siehe ``hilo_rohdaten_*.csv``):

    Datum,Uhrzeit,SBP,DBP,HR,Messtyp
    "1 April, 26",00:20,136,75,69,Armband

Datumswerte sind deutsch lokalisiert (``"1 April, 26"`` = 2026-04-01). Wir
parsen sie eigenständig, damit wir nicht von der ``locale``-Einstellung des
ausführenden Systems abhängen.
"""

import csv
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

# Backend vor Keras-Import setzen.
os.environ.setdefault("KERAS_BACKEND", "torch")

from .feature_engineering import FEATURE_NAMES, build_features, standardize
from .model_store import (
    LoadedModel,
    ModelCard,
    build_model_card,
    default_model_dir,
    save_model,
)
from .night_model import TrainingConfig, build_model, fit_model


GERMAN_MONTHS = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "maerz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}

_DATE_PATTERN = re.compile(r"^(?P<day>\d{1,2})\s+(?P<month>[A-Za-zÄÖÜäöüß]+),?\s*(?P<year>\d{2,4})$")


@dataclass
class TrainingResult:
    sample_count: int
    positive_count: int
    epochs_run: int
    best_val_loss: float | None
    final_loss: float
    model_dir: Path
    notes: str = ""


def load_measurements_from_csv(csv_paths: list[Path]) -> list[dict]:
    """Lese eine oder mehrere CSV-Dateien und gib eine Mess-Liste zurück.

    Eingabezeilen mit unparsbarem Datum werden übersprungen. Wir geben die
    Messungen nach Zeitstempel sortiert zurück, damit die nachgelagerten
    Trend-Features sinnvoll sind.
    """

    rows: list[dict] = []
    for path in csv_paths:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for raw in reader:
                ts = _parse_datum_uhrzeit(raw.get("Datum"), raw.get("Uhrzeit"))
                if ts is None:
                    continue
                try:
                    sbp = int(float(raw["SBP"]))
                    dbp = int(float(raw["DBP"]))
                    hr = int(float(raw["HR"]))
                except (KeyError, TypeError, ValueError):
                    continue
                rows.append(
                    {
                        "datetime": ts.isoformat(timespec="seconds"),
                        "systolic": sbp,
                        "diastolic": dbp,
                        "heart_rate": hr,
                        "measurement_type": _normalize_messtyp(raw.get("Messtyp")),
                        "source": f"csv:{path.name}",
                    }
                )
    rows.sort(key=lambda r: r["datetime"])
    return rows


def train_from_measurements(
    measurements: list[dict],
    *,
    age_years: float | None = None,
    config: TrainingConfig | None = None,
    model_dir: Path | None = None,
    notes: str = "",
) -> TrainingResult:
    """Trainiere das Modell aus einer Liste vorbereiteter Messungen.

    Persistiert das Ergebnis im Modellverzeichnis und gibt die wichtigsten
    Metriken zurück.
    """

    if not measurements:
        raise ValueError("Keine Trainingsdaten übergeben.")

    cfg = config or TrainingConfig()
    target_dir = Path(model_dir) if model_dir else default_model_dir()

    fm = build_features(measurements, age_years=age_years)
    matrix, stats = standardize(fm.matrix)
    labels = fm.weak_labels.astype(np.float32)
    sample_weights = fm.sample_weights.astype(np.float32)
    features = matrix.astype(np.float32)

    model = build_model(input_dim=len(FEATURE_NAMES), config=cfg)
    history = fit_model(
        model=model,
        features=features,
        labels=labels,
        sample_weights=sample_weights,
        config=cfg,
    )

    card = build_model_card(
        sample_count=int(features.shape[0]),
        positive_count=int(labels.sum()),
        config=cfg,
        history_summary={
            "epochs_run": history.epochs_run,
            "final_loss": history.final_loss,
            "final_val_loss": history.final_val_loss,
            "best_val_loss": history.best_val_loss,
        },
        notes=notes,
    )
    save_model(target_dir, model=model, feature_stats=stats, card=card)

    return TrainingResult(
        sample_count=int(features.shape[0]),
        positive_count=int(labels.sum()),
        epochs_run=history.epochs_run,
        best_val_loss=history.best_val_loss,
        final_loss=history.final_loss,
        model_dir=target_dir,
        notes=notes,
    )


def train_from_csv(
    csv_paths: list[Path],
    *,
    age_years: float | None = None,
    config: TrainingConfig | None = None,
    model_dir: Path | None = None,
) -> TrainingResult:
    measurements = load_measurements_from_csv(csv_paths)
    if not measurements:
        raise ValueError("CSV enthielt keine verwertbaren Zeilen.")
    note = "csv: " + ", ".join(p.name for p in csv_paths)
    return train_from_measurements(
        measurements,
        age_years=age_years,
        config=config,
        model_dir=model_dir,
        notes=note,
    )


def predict_with_loaded_model(
    loaded: LoadedModel,
    measurements: list[dict],
    *,
    age_years: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Erzeuge Scores + Standardisierte Features für eine Mess-Liste.

    Wir nutzen die im Modell hinterlegten ``mean``/``std``-Werte, damit das
    Modell auch gut funktioniert, wenn der aktuelle Bericht eine andere
    Verteilung hat als die Trainingsdaten.
    """

    from .night_model import predict_scores  # lazy, hält keras außerhalb des Moduls

    fm = build_features(measurements, age_years=age_years)
    if fm.matrix.size == 0:
        return np.zeros(0, dtype=np.float64), np.zeros(0, dtype=np.float64)
    scaled, _ = standardize(fm.matrix, stats=loaded.feature_stats)
    scores = predict_scores(loaded.model, scaled.astype(np.float32))
    return scores, fm.night_drop_score


# --- Helpers --------------------------------------------------------------

def _parse_datum_uhrzeit(datum: str | None, uhrzeit: str | None) -> datetime | None:
    if not datum or not uhrzeit:
        return None
    datum = datum.strip().replace("\u00a0", " ")
    uhrzeit = uhrzeit.strip()
    match = _DATE_PATTERN.match(datum)
    if not match:
        return None
    day = int(match.group("day"))
    month_name = match.group("month").lower()
    month = GERMAN_MONTHS.get(month_name)
    if month is None:
        return None
    year_raw = int(match.group("year"))
    year = 2000 + year_raw if year_raw < 100 else year_raw
    try:
        hour, minute = (int(part) for part in uhrzeit.split(":")[:2])
    except (ValueError, IndexError):
        return None
    try:
        return datetime(year=year, month=month, day=day, hour=hour, minute=minute)
    except ValueError:
        return None


def _normalize_messtyp(value: str | None) -> str:
    if not value:
        return "unknown"
    lower = value.strip().lower()
    if "armband" in lower:
        return "armband"
    if "telefon" in lower or "phone" in lower:
        return "phone_measurement"
    if "kalibr" in lower:
        return "cuff_calibration"
    if "manschette" in lower or "cuff" in lower:
        return "cuff_measurement"
    return "unknown"
