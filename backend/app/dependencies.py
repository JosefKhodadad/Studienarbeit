from __future__ import annotations

from functools import lru_cache

from app.services.aggregation_service import AggregationService
from app.services.classification_service import NightDayClassifier
from app.services.import_service import ImportService
from app.services.ml.keras_classifier import KerasNightClassifier
from app.services.ml.training_jobs import TrainingJobManager
from app.services.parser_service import HiloParserService
from app.services.repository import InMemoryImportRepository


@lru_cache
def get_import_repository() -> InMemoryImportRepository:
    return InMemoryImportRepository()


@lru_cache
def get_parser_service() -> HiloParserService:
    return HiloParserService()


@lru_cache
def get_aggregation_service() -> AggregationService:
    return AggregationService()


@lru_cache
def get_night_classifier() -> KerasNightClassifier:
    """Hauptklassifikator: Keras-Modell mit logistischem Fallback.

    Solange kein trainiertes Modell vorliegt, verhält sich der Adapter
    transparent wie der bisherige :class:`NightDayClassifier`.
    """

    return KerasNightClassifier(fallback=NightDayClassifier())


@lru_cache
def get_training_job_manager() -> TrainingJobManager:
    classifier = get_night_classifier()
    # Wenn ein Training fertig ist, soll der Modell-Cache des Klassifikators
    # geleert werden, damit der nächste Klassifikationsaufruf das frische
    # Modell lädt.
    return TrainingJobManager(on_finished=classifier.invalidate_cache)


@lru_cache
def get_import_service() -> ImportService:
    return ImportService(
        repository=get_import_repository(),
        parser_service=get_parser_service(),
        aggregation_service=get_aggregation_service(),
        classifier=get_night_classifier(),
    )
