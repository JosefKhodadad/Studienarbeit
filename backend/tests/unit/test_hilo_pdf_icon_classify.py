from __future__ import annotations

from app.services.hilo.pdfplumber_extractor import (
    DEFAULT_TYPE,
    ICON_TOLERANCE_MIN_PT,
    ICON_TOLERANCE_PT,
    LEGEND_TYPE_MAP,
    Y_MATCH_TOLERANCE_PT,
    _build_x_ranges,
    _classify_icon,
    _derive_tolerance,
    _detect_row_type,
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


# ---------------------------------------------------------------------------
# Digest-based row-type detection
#
# In real Hilo PDFs every Telefon icon (regardless of which column it sits in)
# reuses the same image stream as the legend's Telefon icon. The positional
# fallback alone misclassifies the right-column icons because they sit ~108pt
# away from any legend center; the digest match is what restores them.
# ---------------------------------------------------------------------------


def _stub_image(*, x_center: float, y: float, digest_bytes: bytes | None) -> dict[str, object]:
    """Build a minimal pdfplumber-style image dict for the row-type detector.

    The detector reads ``x0/x1/top/bottom`` for geometry and ``stream.get_rawdata()``
    for the digest. A ``digest_bytes`` of ``None`` simulates an icon whose stream
    couldn't be hashed (e.g. an external XObject) so the positional fallback kicks in.
    """
    class _Stream:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def get_rawdata(self) -> bytes:
            return self._data

    return {
        "x0": x_center - 3.0,
        "x1": x_center + 3.0,
        "top": y - 3.0,
        "bottom": y + 3.0,
        "stream": _Stream(digest_bytes) if digest_bytes is not None else None,
    }


def test_detect_row_type_uses_digest_over_position_for_right_column() -> None:
    """Regression: a right-column phone icon at x~261 must be classified as
    phone via its image-stream digest, even though it sits >100pt away from
    every legend x-center and would otherwise fall through to ``armband``."""
    import hashlib

    page_middle = 140.625
    phone_stream = b"phone-icon-pixels"
    phone_digest = hashlib.md5(phone_stream).hexdigest()
    digest_map = {phone_digest: "Telefonmessung"}

    # Legend x-positions (left side of the page) - none of them sit anywhere
    # near the right-column icon, so the positional fallback would default
    # to armband.
    ranges, tolerance = _build_x_ranges(_three_legend_positions())
    right_column_icon = _stub_image(x_center=261.0, y=200.0, digest_bytes=phone_stream)

    detected = _detect_row_type(
        row_y=200.0,
        row_x=170.0,  # right column
        page_middle=page_middle,
        table_images=[right_column_icon],
        x_ranges=ranges,
        digest_map=digest_map,
        tolerance=tolerance,
    )
    assert detected == LEGEND_TYPE_MAP["Telefonmessung"]


def test_detect_row_type_falls_back_to_position_when_digest_unknown() -> None:
    """If we can't get a digest (or it doesn't match the legend), the
    positional logic still has to run so older PDFs keep working."""
    page_middle = 140.625
    ranges, tolerance = _build_x_ranges(_three_legend_positions())
    # Icon sits inside the Telefonmessung x-band (180 +/- 16 = [164, 196]).
    icon = _stub_image(x_center=185.0, y=200.0, digest_bytes=None)

    detected = _detect_row_type(
        row_y=200.0,
        row_x=170.0,
        page_middle=page_middle,
        table_images=[icon],
        x_ranges=ranges,
        digest_map={},
        tolerance=tolerance,
    )
    assert detected == LEGEND_TYPE_MAP["Telefonmessung"]


def test_detect_row_type_returns_default_when_no_icon_in_row() -> None:
    """Row with no nearby icon -> cuffless armband default."""
    icon_far_away = _stub_image(x_center=125.0, y=400.0, digest_bytes=b"x")
    detected = _detect_row_type(
        row_y=200.0,
        row_x=50.0,
        page_middle=140.625,
        table_images=[icon_far_away],
        x_ranges={},
        digest_map={},
        tolerance=ICON_TOLERANCE_PT,
    )
    assert detected == DEFAULT_TYPE


def test_detect_row_type_respects_column_separation() -> None:
    """A phone icon in column 2 must not type a column-1 row, even if it
    happens to share the same Y."""
    import hashlib

    page_middle = 140.625
    phone_stream = b"phone-icon-pixels"
    phone_digest = hashlib.md5(phone_stream).hexdigest()
    digest_map = {phone_digest: "Telefonmessung"}

    right_icon = _stub_image(x_center=261.0, y=200.0, digest_bytes=phone_stream)

    detected = _detect_row_type(
        row_y=200.0,
        row_x=50.0,  # left column
        page_middle=page_middle,
        table_images=[right_icon],
        x_ranges={},
        digest_map=digest_map,
        tolerance=ICON_TOLERANCE_PT,
    )
    assert detected == DEFAULT_TYPE


def test_detect_row_type_y_tolerance_excludes_distant_icons() -> None:
    """Icon a full row above the measurement must not be picked up."""
    import hashlib

    phone_stream = b"phone-icon-pixels"
    phone_digest = hashlib.md5(phone_stream).hexdigest()
    digest_map = {phone_digest: "Telefonmessung"}
    far_icon = _stub_image(
        x_center=125.0,
        y=200.0 - (Y_MATCH_TOLERANCE_PT * 3),
        digest_bytes=phone_stream,
    )
    detected = _detect_row_type(
        row_y=200.0,
        row_x=50.0,
        page_middle=140.625,
        table_images=[far_icon],
        x_ranges={},
        digest_map=digest_map,
        tolerance=ICON_TOLERANCE_PT,
    )
    assert detected == DEFAULT_TYPE
