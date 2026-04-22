from __future__ import annotations

import threading
import time
from pathlib import Path

from app.services.ml.training_jobs import TrainingJobManager
from app.services.ml.training_pipeline import TrainingResult


def _wait_for(predicate, *, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Timeout waiting for training job state change")


def _fake_result() -> TrainingResult:
    return TrainingResult(
        sample_count=10,
        positive_count=3,
        epochs_run=2,
        best_val_loss=0.42,
        final_loss=0.43,
        model_dir=Path("/tmp/fake_model_dir"),
        notes="unit-test",
    )


def test_training_job_manager_transitions_idle_to_finished() -> None:
    completed = threading.Event()
    manager = TrainingJobManager(on_finished=completed.set)

    state = manager._start(sources=["unit:fake"], target=_fake_result)
    # Hinweis: status kann bereits "finished" sein, wenn der Thread sehr
    # schnell durchlaeuft. Daher pruefen wir nur die uebergebenen sources
    # und warten danach auf das Completion-Event.
    assert state["sources"] == ["unit:fake"]

    assert completed.wait(timeout=5.0)
    _wait_for(lambda: manager.get_state()["status"] == "finished")

    final = manager.get_state()
    assert final["status"] == "finished"
    assert final["last_result"]["sample_count"] == 10
    assert final["last_result"]["positive_count"] == 3
    assert final["last_error"] is None
    assert final["finished_at"] is not None


def test_training_job_manager_records_failure() -> None:
    completed = threading.Event()
    manager = TrainingJobManager(on_finished=completed.set)

    def boom() -> TrainingResult:
        raise RuntimeError("explosion")

    manager._start(sources=["unit:bad"], target=boom)
    assert completed.wait(timeout=5.0)
    _wait_for(lambda: manager.get_state()["status"] == "failed")

    state = manager.get_state()
    assert state["status"] == "failed"
    assert state["last_error"] is not None
    assert "explosion" in state["last_error"]


def test_training_job_manager_is_idempotent_while_running() -> None:
    gate = threading.Event()
    finished = threading.Event()
    manager = TrainingJobManager(on_finished=finished.set)

    def slow() -> TrainingResult:
        gate.wait(timeout=2.0)
        return _fake_result()

    manager._start(sources=["unit:slow"], target=slow)
    second = manager._start(sources=["unit:second"], target=_fake_result)
    # Zweiter Start darf den laufenden Job nicht ueberschreiben.
    assert second["status"] == "running"
    assert second["sources"] == ["unit:slow"]

    gate.set()
    assert finished.wait(timeout=5.0)


def test_start_from_measurements_passes_explicit_sources() -> None:
    manager = TrainingJobManager()
    captured: dict = {}

    # Wir patchen die innere _start-Methode, um nur die Source-Logik zu
    # pruefen, ohne das echte Keras-Training anzustossen.
    original_start = manager._start

    def fake_start(*, sources, target):  # noqa: ARG001
        captured["sources"] = list(sources)
        return {"status": "running", "sources": list(sources)}

    manager._start = fake_start  # type: ignore[assignment]
    try:
        manager.start_from_measurements(
            [{"datetime": "2026-04-01T22:00:00", "systolic": 120, "diastolic": 80, "heart_rate": 70}],
            sources=["april.csv", "maerz.csv"],
        )
    finally:
        manager._start = original_start  # type: ignore[assignment]

    assert captured["sources"] == ["april.csv", "maerz.csv"]
