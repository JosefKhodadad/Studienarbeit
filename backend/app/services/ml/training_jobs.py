from __future__ import annotations

"""Hintergrund-Trainings-Jobs mit Status-Automat.

Wir starten höchstens einen Trainingsjob gleichzeitig — Keras teilt sich
intern Threads/Prozesse mit dem PyTorch-Backend, und parallele Trainings
würden sich gegenseitig die Cache-/Speicherbasis ziehen. Der ``run``-Aufruf
gibt sofort zurück; der eigentliche Lauf passiert in einem Daemon-Thread.

Status-Übergänge:

    idle  ──start──▶ running ──ok──▶ finished
                          └──err─▶ failed

``running`` und ``finished`` enthalten die letzten Trainingsmetriken, damit
das Frontend live anzeigen kann, was zuletzt passiert ist.
"""

import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal

from .feature_engineering import NightDayReference
from .training_pipeline import TrainingResult, train_from_csv, train_from_measurements


JobStatus = Literal["idle", "running", "finished", "failed"]


@dataclass
class JobState:
    status: JobStatus = "idle"
    started_at: str | None = None
    finished_at: str | None = None
    last_error: str | None = None
    last_result: dict | None = None
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "last_error": self.last_error,
            "last_result": self.last_result,
            "sources": list(self.sources),
        }


class TrainingJobManager:
    """Steuert genau einen Trainingsjob serialisiert über eine Lock-Variable."""

    def __init__(self, *, on_finished: Callable[[], None] | None = None) -> None:
        self._state = JobState()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._on_finished = on_finished

    # --- Status -------------------------------------------------------

    def get_state(self) -> dict:
        with self._lock:
            return self._state.to_dict()

    def is_running(self) -> bool:
        with self._lock:
            return self._state.status == "running"

    # --- Triggers ------------------------------------------------------

    def start_from_csv(
        self,
        csv_paths: list[Path],
        *,
        age_years: float | None = None,
        model_dir: Path | None = None,
    ) -> dict:
        sources = [p.name for p in csv_paths]
        return self._start(
            sources=sources,
            target=lambda: train_from_csv(
                csv_paths,
                age_years=age_years,
                model_dir=model_dir,
            ),
        )

    def start_from_measurements(
        self,
        measurements: list[dict],
        *,
        age_years: float | None = None,
        model_dir: Path | None = None,
        notes: str = "",
        sources: list[str] | None = None,
        reference: NightDayReference | None = None,
    ) -> dict:
        # Wenn der Aufrufer keine Quellen mitliefert, fallen wir auf eine
        # generische Beschreibung zurück, damit der Status-Endpunkt nicht
        # leer bleibt.
        effective_sources = list(sources) if sources else [
            f"inline:{len(measurements)} Messungen"
        ]
        return self._start(
            sources=effective_sources,
            target=lambda: train_from_measurements(
                measurements,
                age_years=age_years,
                model_dir=model_dir,
                notes=notes,
                reference=reference,
            ),
        )

    # --- intern -------------------------------------------------------

    def _start(self, *, sources: list[str], target: Callable[[], TrainingResult]) -> dict:
        with self._lock:
            if self._state.status == "running":
                # Idempotent: bereits laufender Job, aktuellen Status zurückgeben.
                return self._state.to_dict()
            self._state = JobState(
                status="running",
                started_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
                sources=list(sources),
            )

        thread = threading.Thread(
            target=self._run_target,
            args=(target,),
            name="ml-training-job",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        return self.get_state()

    def _run_target(self, target: Callable[[], TrainingResult]) -> None:
        try:
            result = target()
            with self._lock:
                self._state.status = "finished"
                self._state.finished_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
                self._state.last_result = {
                    "sample_count": result.sample_count,
                    "positive_count": result.positive_count,
                    "epochs_run": result.epochs_run,
                    "best_val_loss": result.best_val_loss,
                    "final_loss": result.final_loss,
                    "model_dir": str(result.model_dir),
                    "notes": result.notes,
                }
                self._state.last_error = None
        except Exception as exc:  # noqa: BLE001 — wir wollen jeden Fehler sehen
            with self._lock:
                self._state.status = "failed"
                self._state.finished_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
                self._state.last_error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        finally:
            if self._on_finished is not None:
                try:
                    self._on_finished()
                except Exception:  # noqa: BLE001
                    pass
