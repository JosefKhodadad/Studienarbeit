from __future__ import annotations

from app.services.hilo.pdfplumber_extractor import (
    DEFAULT_TYPE,
    ICON_TOLERANCE_MIN_PT,
    ICON_TOLERANCE_PT,
    LEGEND_TYPE_MAP,
    _build_x_ranges,
    _classify_icon,
    _derive_tolerance,
)


def _three_legend_positions() -> dict[str, float]:
    # Simulierte x-Mittelpunkte der drei Legenden-Icons in der unteren
    # Zeile einer Hilo-PDF. Abstand 40pt zwischen den Icons.
    return {
        "Kalibrierung mit Manschette": 100.0,
        "Manschettenmessung": 140.0,
        "Telefonmessung": 180.0,
    }


def test_derive_tolerance_keeps_neighbouring_bands_disjoint() -> None:
    tolerance = _derive_tolerance(_three_legend_positions())
    # 40pt Gap * 0.4 = 16pt -- innerhalb der Grenzen [8, 25].
    assert tolerance == 16.0


def test_derive_tolerance_clamps_to_min_for_dense_icons() -> None:
    dense = {"a": 100.0, "b": 105.0, "c": 110.0}  # 5pt Gap
    tolerance = _derive_tolerance(dense)
    assert tolerance == ICON_TOLERANCE_MIN_PT


def test_derive_tolerance_clamps_to_max_for_wide_icons() -> None:
    wide = {"a": 100.0, "b": 200.0, "c": 300.0}  # 100pt Gap -> derive=40
    tolerance = _derive_tolerance(wide)
    assert tolerance == ICON_TOLERANCE_PT


def test_classify_icon_returns_canonical_for_direct_hit() -> None:
    ranges, tolerance = _build_x_ranges(_three_legend_positions())
    detected = _classify_icon(140.0, ranges, tolerance=tolerance)
    assert detected == LEGEND_TYPE_MAP["Manschettenmessung"]


def test_classify_icon_snaps_to_nearest_within_cutoff() -> None:
    ranges, tolerance = _build_x_ranges(_three_legend_positions())
    # 118pt liegt knapp ausserhalb der Kalibrierungs-Bande (100 +/- 16 = [84,116]),
    # ist aber dem Kalibrierungs-Mittelpunkt naeher (Distanz 18) als dem
    # Manschetten-Mittelpunkt (Distanz 22). 18 < 32 (= 2 * tolerance), also
    # darf gesnappt werden.
    detected = _classify_icon(118.0, ranges, tolerance=tolerance)
    assert detected == LEGEND_TYPE_MAP["Kalibrierung mit Manschette"]


def test_classify_icon_falls_back_when_far_from_any_band() -> None:
    ranges, tolerance = _build_x_ranges(_three_legend_positions())
    # 350pt liegt sehr weit von allen drei Icons entfernt -> kein Snap,
    # stattdessen DEFAULT_TYPE (= cuffless armband).
    detected = _classify_icon(350.0, ranges, tolerance=tolerance)
    assert detected == DEFAULT_TYPE


def test_classify_icon_returns_default_when_no_ranges() -> None:
    assert _classify_icon(100.0, {}, tolerance=10.0) == DEFAULT_TYPE
