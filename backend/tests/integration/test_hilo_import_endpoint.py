from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


class _FakeParser:
    def parse_file(self, _path: str | Path) -> dict:
        return {
            "patient": {
                "full_name": "Max Mustermann",
                "email": "max@example.org",
                "gender": "männlich",
                "birth_date": "1985-03-08",
                "age_at_report_date": 41,
                "height_cm": 178,
                "weight_kg": 80.5,
            },
            "report": {"report_month": 3, "report_year": 2026, "source": "hilo_pdf"},
            "summary": {
                "day_rest": {"mean": 1, "sd": 1, "max": 1, "min": 1, "measurements": 1},
                "night": {"mean": 1, "sd": 1, "max": 1, "min": 1, "measurements": 1},
                "all_measurements": {"mean": 1, "sd": 1, "max": 1, "min": 1, "measurements": 1},
            },
            "measurements": [],
        }

    def parse_bytes(self, _content: bytes) -> dict:
        return self.parse_file("unused")


def test_import_endpoint_with_file_path(monkeypatch, tmp_path) -> None:
    sample = tmp_path / "hilo_report.pdf"
    sample.write_text("placeholder")

    monkeypatch.setattr("app.api.routes.hilo_import.HiloPDFParser", _FakeParser)
    client = TestClient(app)

    response = client.post("/api/hilo/import", data={"file_path": str(sample)})

    assert response.status_code == 200
    body = response.json()
    assert body["patient"]["full_name"] == "Max Mustermann"
    assert body["report"]["source"] == "hilo_pdf"
