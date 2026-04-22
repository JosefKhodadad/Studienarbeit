from __future__ import annotations

"""Pydantic-Schemas für die ML-Trainings- und Modell-Endpunkte."""

from typing import Literal

from pydantic import BaseModel, Field


JobStatus = Literal["idle", "running", "finished", "failed"]


class TrainingStartRequest(BaseModel):
    """Optionaler Body für ``POST /api/ml/training/start``.

    ``use_csv_files`` (default: ``True``) verwendet die im Projekt mitgelieferten
    Roh-CSV-Dateien als Trainingsdaten. ``use_repository`` (default: ``True``)
    schaltet zusätzlich die im Repository liegenden Berichts-Messungen dazu —
    so lernt das Modell aus *allen* importierten Hilo-PDFs (Aufgabenstellung:
    "aus allen importierten Dokumenten verarbeitet, damit das Modell das
    Verhalten des Patienten genauer erfassen kann").
    """

    use_csv_files: bool = True
    use_repository: bool = True
    age_years: float | None = None


class TrainingResultSummary(BaseModel):
    # ``model_dir`` kollidiert sonst mit Pydantics ``model_``-Namespace.
    model_config = {"protected_namespaces": ()}

    sample_count: int
    positive_count: int
    epochs_run: int
    final_loss: float
    best_val_loss: float | None = None
    model_dir: str
    notes: str = ""


class TrainingStatusResponse(BaseModel):
    model_config = {"protected_namespaces": ()}

    status: JobStatus
    started_at: str | None = None
    finished_at: str | None = None
    last_error: str | None = None
    last_result: TrainingResultSummary | None = None
    sources: list[str] = Field(default_factory=list)
    model_available: bool = False


class ModelCardResponse(BaseModel):
    available: bool
    created_at: str | None = None
    sample_count: int | None = None
    positive_count: int | None = None
    feature_names: list[str] = Field(default_factory=list)
    history_summary: dict = Field(default_factory=dict)
    training_config: dict = Field(default_factory=dict)
    notes: str = ""


class SleepEpisodeSchema(BaseModel):
    start: str
    end: str
    sleep_onset_estimate: str
    wake_estimate: str
    measurement_indices: list[int]
    confidence: float


class SleepEpisodesResponse(BaseModel):
    episodes: list[SleepEpisodeSchema] = Field(default_factory=list)
    expected_sleep_hours: tuple[float, float] | None = None
    notes: str = ""
