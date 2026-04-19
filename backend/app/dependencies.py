from __future__ import annotations

from functools import lru_cache

from app.services.aggregation_service import AggregationService
from app.services.import_service import ImportService
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
def get_import_service() -> ImportService:
    return ImportService(
        repository=get_import_repository(),
        parser_service=get_parser_service(),
        aggregation_service=get_aggregation_service(),
    )
