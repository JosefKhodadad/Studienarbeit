from pathlib import Path

from fastapi.testclient import TestClient

from app.dependencies import get_import_service
from app.main import app
from app.services.aggregation_service import AggregationService
from app.services.import_service import ImportService
from app.services.repository import InMemoryImportRepository


class _FakeParserService:
    def parse_file(self, _path: str | Path) -> dict:
        return {
            "patient": {
                "full_name": "Max Mustermann",
                "email": "max@example.org",
                "gender": "male",
                "birth_date": "1985-03-08",
                "height_cm": 178,
                "weight_kg": 80.5,
            },
            "report": {
                "report_month": 3,
                "report_year": 2026,
                "source": "hilo_pdf",
            },
            "summary": {
                "day_rest": {"mean": 124, "sd": 8, "max": 140, "min": 108, "measurements": 2},
                "night": {"mean": 112, "sd": 7, "max": 126, "min": 99, "measurements": 1},
                "all_measurements": {"mean": 120, "sd": 9, "max": 140, "min": 99, "measurements": 3},
            },
            "measurements": [
                {
                    "datetime": "2026-03-01T07:30:00",
                    "systolic": 123,
                    "diastolic": 79,
                    "heart_rate": 68,
                    "measurement_type": "unknown",
                    "source_page": 2,
                    "source_column": "left",
                    "row_index_on_page": 1,
                },
                {
                    "datetime": "2026-03-01T10:15:00",
                    "systolic": 128,
                    "diastolic": 82,
                    "heart_rate": 72,
                    "measurement_type": "phone_measurement",
                    "source_page": 2,
                    "source_column": "left",
                    "row_index_on_page": 2,
                },
                {
                    "datetime": "2026-03-01T23:10:00",
                    "systolic": 117,
                    "diastolic": 73,
                    "heart_rate": 61,
                    "measurement_type": "cuff_measurement",
                    "source_page": 2,
                    "source_column": "right",
                    "row_index_on_page": 3,
                },
            ],
        }

    def parse_bytes(self, _content: bytes) -> dict:
        return self.parse_file("unused")


def _override_import_service() -> ImportService:
    return ImportService(
        repository=InMemoryImportRepository(),
        parser_service=_FakeParserService(),
        aggregation_service=AggregationService(),
    )


def test_import_endpoint_with_file_path_exposes_dashboard_data(tmp_path) -> None:
    sample = tmp_path / "hilo_report_input.txt"
    sample.write_text("placeholder", encoding="utf-8")

    service = _override_import_service()
    app.dependency_overrides[get_import_service] = lambda: service
    client = TestClient(app)

    try:
        response = client.post("/api/imports/hilo", data={"file_path": str(sample)})
        assert response.status_code == 200

        body = response.json()
        assert body["patient"]["full_name"] == "Max Mustermann"
        assert body["report"]["source"] == "hilo_pdf"
        assert body["summary"]["measurement_count_total"] == 3
        assert body["summary"]["measurement_count_by_type"]["unknown"] == 1

        report_id = body["report"]["id"]
        patient_id = body["patient"]["id"]

        summary_response = client.get(f"/api/dashboard/monthly-summary?report_id={report_id}")
        assert summary_response.status_code == 200
        assert summary_response.json()["report"]["id"] == report_id

        profile_response = client.get(f"/api/patients/{patient_id}/profile")
        assert profile_response.status_code == 200
        assert profile_response.json()["patient"]["full_name"] == "Max Mustermann"

        legacy_response = client.post("/api/hilo/import", data={"file_path": str(sample)})
        assert legacy_response.status_code == 200
    finally:
        app.dependency_overrides.clear()
