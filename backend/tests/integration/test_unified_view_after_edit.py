"""Regressionstest für den Schlaf-Episoden-Bug nach Konfigurator-Edit.

Szenario: Ein Monatsbericht wird importiert (naive Zeitstempel), danach
bearbeitet das Frontend einen Eintrag und sendet ihn als tz-aware
UTC-String (``new Date(...).toISOString()`` → ``...+00:00``/``Z``). Ohne
Normalisierung mischt der Store naive und tz-aware Datetimes, und
``/api/dashboard/unified-measurements`` scheitert beim Sortieren im
Sleep-Phase-Detektor mit ``TypeError``. Der Test stellt sicher, dass
der Endpunkt nach einer solchen Bearbeitung erfolgreich antwortet und
die Schlaf-Episoden weiterhin berechnet werden.
"""

from fastapi.testclient import TestClient

from app.dependencies import get_import_service
from app.main import app
from app.services.aggregation_service import AggregationService
from app.services.import_service import ImportService
from app.services.parser_service import HiloParserService
from app.services.repository import InMemoryImportRepository


def test_unified_view_works_after_tz_aware_edit() -> None:
    # Über mehrere Requests hinweg dieselbe Service-Instanz nutzen, sonst
    # verliert das In-Memory-Repository zwischen den Aufrufen den Zustand.
    shared_service = ImportService(
        repository=InMemoryImportRepository(),
        parser_service=HiloParserService(),
        aggregation_service=AggregationService(),
    )
    app.dependency_overrides[get_import_service] = lambda: shared_service
    try:
        client = TestClient(app)

        # Import im PDF-Stil: naive Zeitstempel, zwei Tage mit Tag- und
        # Nachtmessungen, damit der Sleep-Phase-Detektor etwas zu sortieren hat.
        import_payload = {
            "patient": {"full_name": "Test", "birth_date": "1990-01-01"},
            "report": {"report_month": 3, "report_year": 2026, "source": "mobile_api"},
            "measurements": [
                {"datetime": "2026-03-01T09:00:00", "systolic": 128, "diastolic": 82, "heart_rate": 72, "measurement_type": "armband"},
                {"datetime": "2026-03-01T14:00:00", "systolic": 135, "diastolic": 85, "heart_rate": 75, "measurement_type": "armband"},
                {"datetime": "2026-03-01T23:00:00", "systolic": 115, "diastolic": 72, "heart_rate": 60, "measurement_type": "armband"},
                {"datetime": "2026-03-02T02:30:00", "systolic": 112, "diastolic": 70, "heart_rate": 58, "measurement_type": "armband"},
                {"datetime": "2026-03-02T10:00:00", "systolic": 130, "diastolic": 84, "heart_rate": 73, "measurement_type": "armband"},
            ],
        }
        r = client.post("/api/imports/measurements", json=import_payload)
        assert r.status_code == 200, r.text
        entry_id = r.json()["measurements"][0]["entry_id"]

        # Edit mit tz-aware UTC (wie das Browser-Frontend es vor dem Fix schickte).
        r = client.patch(
            f"/api/measurements/{entry_id}",
            json={
                "datetime": "2026-03-01T09:15:00+00:00",
                "systolic": 126,
                "diastolic": 80,
                "heart_rate": 70,
                "measurement_type": "armband",
            },
        )
        assert r.status_code == 200, r.text

        # Neue Messung via POST ebenfalls mit tz-aware Zeitstempel.
        r = client.post(
            "/api/measurements",
            json={
                "datetime": "2026-03-02T23:30:00+00:00",
                "systolic": 118,
                "diastolic": 74,
                "heart_rate": 62,
                "measurement_type": "armband",
            },
        )
        assert r.status_code == 201, r.text

        # Unified View muss jetzt antworten und die Schlaf-Episoden enthalten.
        r = client.get("/api/dashboard/unified-measurements")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["measurements"], "Messungen fehlen im Unified-Payload"
        assert isinstance(body.get("sleep_episodes"), list)
        # Alle gespeicherten Zeitstempel sind naiv (kein +/-Offset, kein Z).
        for m in body["measurements"]:
            ts = m["datetime"]
            assert "+" not in ts[10:] and not ts.endswith("Z"), ts
    finally:
        app.dependency_overrides.pop(get_import_service, None)
