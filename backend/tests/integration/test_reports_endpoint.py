from __future__ import annotations

from fastapi.testclient import TestClient

from app.dependencies import get_import_service
from app.main import app
from app.services.aggregation_service import AggregationService
from app.services.import_service import ImportService
from app.services.repository import InMemoryImportRepository


class _TwoReportParser:
    """Return one of two deterministic parsed payloads per call."""

    def __init__(self) -> None:
        self._calls = 0

    def parse_bytes(self, _content: bytes) -> dict:
        self._calls += 1
        if self._calls == 1:
            return {
                "patient": {"full_name": "Anna Test", "birth_date": "1990-01-01"},
                "report": {"report_month": 2, "report_year": 2026, "source": "hilo_pdf"},
                "summary": {
                    "day_rest": {"measurements": 1},
                    "night": {"measurements": 1},
                    "all_measurements": {"measurements": 2},
                },
                "measurements": [
                    {
                        "datetime": "2026-02-10T09:00:00",
                        "systolic": 122, "diastolic": 78, "heart_rate": 70,
                        "measurement_type": "phone_measurement",
                    },
                    {
                        "datetime": "2026-02-10T23:45:00",
                        "systolic": 108, "diastolic": 65, "heart_rate": 58,
                        "measurement_type": "armband",
                    },
                ],
            }
        return {
            "patient": {"full_name": "Anna Test", "birth_date": "1990-01-01"},
            "report": {"report_month": 3, "report_year": 2026, "source": "hilo_pdf"},
            "summary": {
                "day_rest": {"measurements": 1},
                "night": {"measurements": 0},
                "all_measurements": {"measurements": 1},
            },
            "measurements": [
                {
                    "datetime": "2026-03-02T10:30:00",
                    "systolic": 125, "diastolic": 82, "heart_rate": 74,
                    "measurement_type": "cuff_measurement",
                },
            ],
        }

    def parse_file(self, _path) -> dict:
        return self.parse_bytes(b"")


def _client() -> TestClient:
    service = ImportService(
        repository=InMemoryImportRepository(),
        parser_service=_TwoReportParser(),
        aggregation_service=AggregationService(),
    )
    app.dependency_overrides[get_import_service] = lambda: service
    return TestClient(app)


def test_multi_report_lifecycle() -> None:
    client = _client()
    try:
        # Upload two reports sequentially.
        first = client.post("/api/imports/hilo", files={"file": ("a.pdf", b"placeholder", "application/pdf")})
        assert first.status_code == 200
        first_id = first.json()["report"]["id"]

        second = client.post("/api/imports/hilo", files={"file": ("b.pdf", b"placeholder", "application/pdf")})
        assert second.status_code == 200
        second_id = second.json()["report"]["id"]

        # List should return both, newest first, second is current.
        listing = client.get("/api/reports")
        assert listing.status_code == 200
        body = listing.json()
        assert body["current_report_id"] == second_id
        ids = [r["id"] for r in body["reports"]]
        assert set(ids) == {first_id, second_id}

        # Activate the older report.
        activated = client.post(f"/api/reports/{first_id}/activate")
        assert activated.status_code == 200
        assert activated.json()["report"]["id"] == first_id

        # Unified view must contain measurements from both reports and annotate them.
        unified = client.get("/api/dashboard/unified-measurements")
        assert unified.status_code == 200
        u_body = unified.json()
        assert len(u_body["measurements"]) == 3
        ids_in_unified = {m["report_id"] for m in u_body["measurements"]}
        assert ids_in_unified == {first_id, second_id}
        # Each measurement has a classification.
        for m in u_body["measurements"]:
            assert "is_night" in m
            assert "classification_score" in m
            assert "classification_method" in m

        # Delete the older report; only the other remains and becomes current.
        removed = client.delete(f"/api/reports/{first_id}")
        assert removed.status_code == 200
        remaining = removed.json()
        assert remaining["current_report_id"] == second_id
        assert len(remaining["reports"]) == 1
        assert remaining["reports"][0]["id"] == second_id

        # Deleting an unknown id yields 404.
        missing = client.delete("/api/reports/unknown")
        assert missing.status_code == 404
    finally:
        app.dependency_overrides.clear()
