from fastapi.testclient import TestClient

from app.dependencies import get_import_service
from app.main import app
from app.services.aggregation_service import AggregationService
from app.services.import_service import ImportService
from app.services.parser_service import HiloParserService
from app.services.repository import InMemoryImportRepository


def _override_import_service() -> ImportService:
    return ImportService(
        repository=InMemoryImportRepository(),
        parser_service=HiloParserService(),
        aggregation_service=AggregationService(),
    )


def test_direct_json_import_supports_mobile_contract() -> None:
    payload = {
        "patient": {
            "full_name": "Max Mustermann",
            "birth_date": "2003-07-29",
            "gender": "male",
            "height_cm": 182,
            "weight_kg": 104,
            "email": "max@example.org",
        },
        "report": {
            "report_month": 3,
            "report_year": 2026,
            "source": "mobile_api",
        },
        "measurements": [
            {
                "datetime": "2026-03-01T00:33:00",
                "systolic": 136,
                "diastolic": 80,
                "heart_rate": 72,
                "measurement_type": "unknown",
            },
            {
                "datetime": "2026-03-01T09:12:00",
                "systolic": 129,
                "diastolic": 77,
                "heart_rate": 68,
                "measurement_type": "phone_measurement",
            },
        ],
    }

    service = _override_import_service()
    app.dependency_overrides[get_import_service] = lambda: service
    client = TestClient(app)

    try:
        response = client.post("/api/imports/measurements", json=payload)
        assert response.status_code == 200

        body = response.json()
        assert body["report"]["source"] == "mobile_api"
        assert body["summary"]["measurement_count_total"] == 2
        assert body["summary"]["measurement_count_day_rest"] == 1
        assert body["summary"]["measurement_count_night"] == 1
        assert body["patient"]["age_at_report_date"] == 22
    finally:
        app.dependency_overrides.clear()
