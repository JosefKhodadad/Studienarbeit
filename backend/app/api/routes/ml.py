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
from app.services.ml.feature_engineering import NightDayReference
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
    repo_summaries: list[dict] = []
    if request.use_csv_files:
        csvs = _csv_paths()
        if csvs:
            measurements.extend(load_measurements_from_csv(csvs))
            sources.extend(p.name for p in csvs)
    if request.use_repository:
        repo_count_before = len(measurements)
        for payload in repo.iter_all_reports():
            measurements.extend(payload.get("measurements") or [])
            summary = payload.get("summary")
            if summary:
                repo_summaries.append(summary)
        repo_added = len(measurements) - repo_count_before
        sources.append(f"repo:{repo_added} Messungen aus {len(repo_summaries)} Berichten")

    if not measurements:
        raise HTTPException(
            status_code=422,
            detail=(
                "Keine Trainingsdaten verfügbar: weder CSV-Rohdaten noch "
                "importierte Berichte vorhanden."
            ),
        )

    reference = _aggregate_reference(repo_summaries)

    jobs.start_from_measurements(
        measurements,
        age_years=request.age_years,
        notes="; ".join(sources),
        sources=sources,
        reference=reference,
    )
    return _status_response(jobs, classifier)


def _aggregate_reference(summaries: list[dict]) -> NightDayReference | None:
    """Mittele die Nacht-/Tag-Referenzwerte über alle vorhandenen Berichte.

    Damit lernt das Modell mit *einem* konsistenten Erwartungsband, das den
    typischen Patientenrhythmus über alle bisher importierten Berichte
    abbildet. Ein Bericht ohne PDF-Übersicht (kein Mittelwert) wird einfach
    übersprungen.
    """

    if not summaries:
        return None

    def _gather(section_key: str, field_key: str) -> tuple[float, int]:
        total, weight = 0.0, 0
        for summary in summaries:
            section = summary.get(section_key) or {}
            value = section.get(field_key)
            count = int(section.get("measurements") or 0) or 1
            if value is None:
                continue
            try:
                total += float(value) * count
                weight += count
            except (TypeError, ValueError):
                continue
        return total, weight

    def _mean(section_key: str, field_key: str) -> float | None:
        total, weight = _gather(section_key, field_key)
        if weight <= 0:
            return None
        return total / weight

    night_sbp = _mean("night", "mean")
    night_dbp = _mean("night", "mean_diastolic")
    night_hr = _mean("night", "mean_heart_rate")
    if night_sbp is None and night_dbp is None and night_hr is None:
        return None
    return NightDayReference(
        night_sbp_mean=night_sbp,
        night_dbp_mean=night_dbp,
        night_hr_mean=night_hr,
        night_sbp_min=_mean("night", "min"),
        night_sbp_max=_mean("night", "max"),
        night_dbp_min=_mean("night", "min_diastolic"),
        night_hr_min=_mean("night", "min_heart_rate"),
        day_sbp_mean=_mean("day_rest", "mean"),
        day_dbp_mean=_mean("day_rest", "mean_diastolic"),
        day_hr_mean=_mean("day_rest", "mean_heart_rate"),
    )


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
