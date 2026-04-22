from __future__ import annotations

"""HTTP-Routen für das ML-Training und die Modellauskunft."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import (
    get_import_repository,
    get_night_classifier,
    get_training_job_manager,
)
from app.schemas.ml import (
    ModelCardResponse,
    TrainingResultSummary,
    TrainingStartRequest,
    TrainingStatusResponse,
)
from app.services.ml.keras_classifier import KerasNightClassifier
from app.services.ml.training_jobs import TrainingJobManager
from app.services.ml.training_pipeline import load_measurements_from_csv
from app.services.repository import InMemoryImportRepository


router = APIRouter(prefix="/api/ml", tags=["ml"])


def _project_root() -> Path:
    # backend/app/api/routes/ml.py -> Studienarbeit/
    return Path(__file__).resolve().parents[4]


def _csv_paths() -> list[Path]:
    root = _project_root()
    candidates = [
        root / "hilo_rohdaten_April.csv",
        root / "hilo_rohdaten_märz.csv",
    ]
    existing = [p for p in candidates if p.exists()]
    return existing


@router.post("/training/start", response_model=TrainingStatusResponse)
def start_training(
    request: TrainingStartRequest | None = None,
    jobs: TrainingJobManager = Depends(get_training_job_manager),
    repo: InMemoryImportRepository = Depends(get_import_repository),
    classifier: KerasNightClassifier = Depends(get_night_classifier),
) -> TrainingStatusResponse:
    request = request or TrainingStartRequest()

    if jobs.is_running():
        return _status_response(jobs, classifier)

    measurements: list[dict] = []
    sources: list[str] = []
    if request.use_csv_files:
        csvs = _csv_paths()
        if csvs:
            measurements.extend(load_measurements_from_csv(csvs))
            sources.extend(p.name for p in csvs)
    if request.use_repository:
        for payload in repo.iter_all_reports():
            measurements.extend(payload.get("measurements") or [])
        sources.append(f"repo:{len(measurements)} Messungen")

    if not measurements:
        raise HTTPException(
            status_code=422,
            detail=(
                "Keine Trainingsdaten verfügbar: weder CSV-Rohdaten noch "
                "importierte Berichte vorhanden."
            ),
        )

    jobs.start_from_measurements(
        measurements,
        age_years=request.age_years,
        notes="; ".join(sources),
        sources=sources,
    )
    return _status_response(jobs, classifier)


@router.get("/training/status", response_model=TrainingStatusResponse)
def training_status(
    jobs: TrainingJobManager = Depends(get_training_job_manager),
    classifier: KerasNightClassifier = Depends(get_night_classifier),
) -> TrainingStatusResponse:
    return _status_response(jobs, classifier)


@router.get("/model-info", response_model=ModelCardResponse)
def model_info(
    classifier: KerasNightClassifier = Depends(get_night_classifier),
) -> ModelCardResponse:
    if not classifier.model_available():
        return ModelCardResponse(available=False)
    card = classifier.get_model_card() or {}
    return ModelCardResponse(
        available=True,
        created_at=card.get("created_at"),
        sample_count=card.get("sample_count"),
        positive_count=card.get("positive_count"),
        feature_names=list(card.get("feature_names") or []),
        history_summary=dict(card.get("history_summary") or {}),
        training_config=dict(card.get("training_config") or {}),
        notes=str(card.get("notes") or ""),
    )


# --- Hilfen --------------------------------------------------------------

def _status_response(
    jobs: TrainingJobManager,
    classifier: KerasNightClassifier,
) -> TrainingStatusResponse:
    state = jobs.get_state()
    last_result = state.get("last_result")
    summary: TrainingResultSummary | None = None
    if last_result:
        summary = TrainingResultSummary(
            sample_count=int(last_result.get("sample_count", 0)),
            positive_count=int(last_result.get("positive_count", 0)),
            epochs_run=int(last_result.get("epochs_run", 0)),
            final_loss=float(last_result.get("final_loss", 0.0)),
            best_val_loss=last_result.get("best_val_loss"),
            model_dir=str(last_result.get("model_dir", "")),
            notes=str(last_result.get("notes", "")),
        )
    return TrainingStatusResponse(
        status=state["status"],
        started_at=state.get("started_at"),
        finished_at=state.get("finished_at"),
        last_error=state.get("last_error"),
        last_result=summary,
        sources=list(state.get("sources") or []),
        model_available=classifier.model_available(),
    )
