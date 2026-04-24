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


def test_bp_category_classification_follows_esh_2023() -> None:
    service = AggregationService()
    cases = [
        (118, 78, "optimal"),
        (119, 79, "optimal"),
        (120, 75, "normal"),
        (125, 84, "normal"),
        (130, 82, "high_normal"),
        (138, 88, "high_normal"),
        (140, 85, "hypertension_grade_1"),
        (150, 95, "hypertension_grade_1"),
        (160, 95, "hypertension_grade_2"),
        (178, 108, "hypertension_grade_2"),
        (180, 95, "hypertension_grade_3"),
        (200, 120, "hypertension_grade_3"),
        (85, 65, "hypotension"),
        (110, 55, "hypotension"),
    ]
    for sbp, dbp, expected in cases:
        assert service._classify_bp(sbp, dbp) == expected, (sbp, dbp, expected)


def test_bp_category_breakdown_is_included_in_summary() -> None:
    service = AggregationService()
    payload = DirectImportRequest.model_validate(
        {
            "patient": {"full_name": "Testperson"},
            "report": {"report_month": 3, "report_year": 2026, "source": "mobile_api"},
            "measurements": [
                {"datetime": "2026-03-01T09:00:00", "systolic": 115, "diastolic": 75, "heart_rate": 68, "measurement_type": "armband"},
                {"datetime": "2026-03-01T10:00:00", "systolic": 125, "diastolic": 82, "heart_rate": 70, "measurement_type": "armband"},
                {"datetime": "2026-03-01T11:00:00", "systolic": 135, "diastolic": 86, "heart_rate": 72, "measurement_type": "armband"},
                {"datetime": "2026-03-01T12:00:00", "systolic": 145, "diastolic": 92, "heart_rate": 74, "measurement_type": "armband"},
                {"datetime": "2026-03-01T13:00:00", "systolic": 165, "diastolic": 102, "heart_rate": 80, "measurement_type": "armband"},
                {"datetime": "2026-03-01T14:00:00", "systolic": 85, "diastolic": 55, "heart_rate": 60, "measurement_type": "armband"},
            ],
        }
    )
    response = service.build_from_direct_import(payload)
    categories = response.summary.measurement_count_by_bp_category
    assert categories.optimal == 1
    assert categories.normal == 1
    assert categories.high_normal == 1
    assert categories.hypertension_grade_1 == 1
    assert categories.hypertension_grade_2 == 1
    assert categories.hypotension == 1
