from app.services.hilo.icon_detection import IconDetector


def test_icon_detector_matches_learned_signatures() -> None:
    detector = IconDetector()
    detector.learn_legend(
        {
            "kalibrierung": {"width": 10, "height": 10, "image": b"A" * 20},
            "manschette": {"width": 11, "height": 11, "image": b"B" * 20},
            "telefon": {"width": 12, "height": 12, "image": b"C" * 20},
        }
    )

    assert detector.detect({"width": 11, "height": 11, "image": b"B" * 20}) == "cuff_measurement"
    assert detector.detect({"width": 99, "height": 99, "image": b"X" * 4}) == "unknown"
