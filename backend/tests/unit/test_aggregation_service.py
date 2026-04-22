from app.schemas.hilo_import import DirectImportRequest
from app.services.aggregation_service import AggregationService


def test_aggregation_service_builds_dashboard_contract() -> None:
    service = AggregationService()
    payload = DirectImportRequest.model_validate(
        {
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
                "file_name": "mobile-sync.json",
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
                {
                    "datetime": "2026-03-01T22:48:00",
                    "systolic": 121,
                    "diastolic": 74,
                    "heart_rate": 63,
                    "measurement_type": "calibration_with_cuff",
                },
            ],
        }
    )

    response = service.build_from_direct_import(payload)

    assert response.patient.id.startswith("patient_")
    assert response.report.id.startswith("report_")
    assert response.summary.measurement_count_total == 3
    assert response.summary.measurement_count_by_type.cuff_calibration == 1
    assert response.summary.measurement_count_by_type.phone_measurement == 1
    assert response.summary.measurement_count_by_type.unknown == 1
    assert response.summary.measurement_count_day_rest == 1
    assert response.summary.measurement_count_night == 2
    assert response.warnings == ["measurement_type could not be reliably determined for all rows"]
