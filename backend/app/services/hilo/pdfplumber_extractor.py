"""pdfplumber-based Hilo measurement extractor.

This module is the faithful backend port of the standalone reference script
``pdfParser.py``. The production parser historically relied on PyMuPDF plus
image-signature matching for the measurement type and fell back to ``unknown``
for the vast majority of rows. pdfplumber exposes word/image bounding boxes
which lets us reproduce the reference logic:

* detect the legend at the bottom of each page to calibrate icon signatures
  (image-stream digests) and as a positional fallback their x-bands;
* split the page into a left and a right column;
* find measurements in each y-row (left AND right) via regex;
* assign a measurement type by matching the icon sitting in the same row /
  same column against the calibrated legend signatures. The x-position is
  only used as a coarse fallback for PDFs that don't reuse the legend image
  streams in the table.

The returned payload matches the structure produced by
``app.services.hilo.extractor.extract_measurement_rows`` so the rest of the
aggregation pipeline keeps working unchanged.

Why digests, not x-positions? In real Hilo monthly PDFs all table icons
(calibration, cuff, phone) sit at the *same* x-coordinate within their
column - the x-coordinate encodes the column, not the icon type. Using only
x-positions confuses the column-2 icons (which sit ~108pt away from the
Telefon legend center) with the cuffless armband default and drops them on
the floor. The image stream id is what really discriminates the three icons.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import re
from typing import Any, Iterable

from .date_parser import GERMAN_MONTHS


logger = logging.getLogger(__name__)


ZEILEN_PATTERN = re.compile(
    r"(\d{1,2}\s+\w+,\s+\d{2})\s+"
    r"(\d{2}:\d{2})\s+"
    r"(\d{2,3})\s+"
    r"(\d{2,3})\s+"
    r"(\d{2,3})"
)

# German legend labels as used in Hilo exports mapped to the backend's type ids.
LEGEND_TYPE_MAP = {
    "Kalibrierung mit Manschette": "cuff_calibration",
    "Manschettenmessung": "cuff_measurement",
    "Telefonmessung": "phone_measurement",
}

LEGEND_KEYWORDS = {
    "kalibrierung": "Kalibrierung mit Manschette",
    "manschetten": "Manschettenmessung",
    "telefon": "Telefonmessung",
}

DEFAULT_TYPE = "armband"  # cuffless Hilo armband reading (no icon next to row)

# Maximum half-width of an icon-band around a calibrated center. Real Hilo PDFs
# place the three legend icons roughly 30-50pt apart, so 25pt is the upper
# bound that still keeps the bands disjoint. The actual tolerance per page is
# derived dynamically from the spacing of the calibrated icons.
ICON_TOLERANCE_PT = 25.0
ICON_TOLERANCE_MIN_PT = 8.0
# Beyond this multiple of the per-page tolerance we no longer snap a candidate
# icon to the closest band; instead we treat it as "kein Symbol" and return
# the cuffless-armband default. This guards against decorative images getting
# misclassified as a measurement type when their x-position happens to be the
# arithmetically closest band but is still far from all icon centers.
ICON_SNAP_CUTOFF_FACTOR = 2.0
Y_MATCH_TOLERANCE_PT = 8.0
PROBE_MARKER = "v2-digest-fix"  # diagnostic: set by the active extractor module
# Y-window in which we look for the *legend* (Kalibrierung / Manschettenmessung
# / Telefonmessung) at the bottom of a page. The legend row in real Hilo PDFs
# sits at the very bottom (top ~ 489 of a 530pt page = 92.3 %). A wider window
# (e.g. 0.85) sweeps stray table icons near the page bottom into the legend
# and drops them from the row-icon search, which is why we tightened it.
LEGEND_AREA_Y_RATIO = 0.91
ICON_MAX_SIDE_PT = 20.0  # legend & table icons are ~6pt squares


@dataclass
class ExtractedMeasurement:
    date_iso: str
    time: str
    systolic: int
    diastolic: int
    heart_rate: int
    measurement_type: str
    source_page: int
    source_column: str
    row_index_on_page: int


def parse_date_iso(datum_str: str) -> str | None:
    """Convert ``"26 März, 26"`` style tokens into ``"YYYY-MM-DD"``."""
    parts = datum_str.replace(",", "").split()
    if len(parts) != 3:
        return None
    try:
        day = int(parts[0])
    except ValueError:
        return None
    month_token = parts[1].lower().replace("ä", "ae").replace("ã¤", "ae")
    month = GERMAN_MONTHS.get(parts[1].lower()) or GERMAN_MONTHS.get(month_token)
    if month is None:
        return None
    try:
        year = 2000 + int(parts[2])
    except ValueError:
        return None
    try:
        return f"{year:04d}-{month:02d}-{day:02d}"
    except ValueError:
        return None


def _is_icon_image(img: dict[str, Any]) -> bool:
    """Return True for small inline images that look like a Hilo legend icon."""
    return (
        img["x1"] - img["x0"] <= ICON_MAX_SIDE_PT
        and img["bottom"] - img["top"] <= ICON_MAX_SIDE_PT
    )


def _image_digest(img: dict[str, Any]) -> str | None:
    """Return a stable digest of the image's pixel stream, or ``None``.

    Two icons rendered from the same XObject share the same digest, which
    is exactly how the Hilo PDF marks icon types: every Telefon icon in
    every row reuses the same image stream, regardless of its column.
    """
    stream = img.get("stream")
    if stream is None:
        return None
    try:
        data = stream.get_rawdata()
    except Exception:  # pragma: no cover - defensive (broken PDF stream)
        return None
    if not data:
        return None
    return hashlib.md5(data).hexdigest()


def _icon_for_word(word: dict[str, Any], images: list[dict[str, Any]]) -> dict[str, Any] | None:
    wort_x = word["x0"]
    wort_y = word["top"]
    candidates = [
        img for img in images
        if img["x1"] <= wort_x + 5 and abs(img["top"] - wort_y) < 20
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda i: i["x1"])


def _calibrate_icon_positions(page: Any) -> dict[str, float]:
    """Locate legend icons at the bottom of ``page`` and return ``{type: x_center}``."""
    return _calibrate_legend(page)[0]


def _calibrate_legend(page: Any) -> tuple[dict[str, float], dict[str, str]]:
    """Locate legend icons and return ``(positions, digest_to_canonical)``.

    ``positions`` maps the canonical legend name (e.g. ``"Telefonmessung"``)
    to the x-center of its legend icon - kept for the positional fallback in
    :func:`_classify_icon`. ``digest_to_canonical`` maps the icon's image
    stream digest to the same canonical name; this is the authoritative
    signal we use to type table icons.
    """
    page_height = page.height
    legend_y_start = page_height * LEGEND_AREA_Y_RATIO

    words = page.extract_words(x_tolerance=5, y_tolerance=3)
    legend_words = [w for w in words if w["top"] >= legend_y_start]
    legend_images = [
        img for img in page.images
        if img["top"] >= legend_y_start and _is_icon_image(img)
    ]

    positions: dict[str, float] = {}
    digest_map: dict[str, str] = {}
    for word in legend_words:
        word_text = word["text"].lower()
        for keyword, canonical in LEGEND_KEYWORDS.items():
            if keyword in word_text and canonical not in positions:
                icon = _icon_for_word(word, legend_images)
                if icon is None:
                    continue
                positions[canonical] = (icon["x0"] + icon["x1"]) / 2
                digest = _image_digest(icon)
                if digest:
                    digest_map[digest] = canonical
    return positions, digest_map


def _derive_tolerance(icon_positions: dict[str, float]) -> float:
    """Pick a half-band width that keeps neighbouring icon bands disjoint.

    With 3 calibrated icons we use 40 % of the smallest neighbour gap. With a
    single icon we fall back to ``ICON_TOLERANCE_PT``. The result is clamped
    to ``[ICON_TOLERANCE_MIN_PT, ICON_TOLERANCE_PT]`` so unusually tight or
    unusually wide layouts still produce a usable band.
    """
    if not icon_positions:
        return ICON_TOLERANCE_PT
    centers = sorted(icon_positions.values())
    if len(centers) < 2:
        return ICON_TOLERANCE_PT
    gaps = [centers[i + 1] - centers[i] for i in range(len(centers) - 1)]
    derived = min(gaps) * 0.4
    return max(ICON_TOLERANCE_MIN_PT, min(ICON_TOLERANCE_PT, derived))


def _build_x_ranges(
    icon_positions: dict[str, float],
    tolerance: float | None = None,
) -> tuple[dict[str, tuple[float, float]], float]:
    effective_tolerance = tolerance if tolerance is not None else _derive_tolerance(icon_positions)
    ranges = {
        canonical: (x - effective_tolerance, x + effective_tolerance)
        for canonical, x in icon_positions.items()
    }
    return ranges, effective_tolerance


def _classify_icon(
    x_center: float,
    x_ranges: dict[str, tuple[float, float]],
    *,
    tolerance: float = ICON_TOLERANCE_PT,
    snap_cutoff_factor: float = ICON_SNAP_CUTOFF_FACTOR,
) -> str:
    """Map an x-coordinate to its canonical legend name, else fall back to DEFAULT_TYPE.

    A direct hit (``x_center`` inside a band) is preferred. Outside any band we
    snap to the closest centre, but only if it sits within
    ``snap_cutoff_factor * tolerance``; further-away candidates are treated as
    "kein Symbol" so decorative or stray images do not get misclassified.
    """
    if not x_ranges:
        return DEFAULT_TYPE
    best_canonical: str | None = None
    best_distance = float("inf")
    for canonical, (x_min, x_max) in x_ranges.items():
        if x_min <= x_center <= x_max:
            return LEGEND_TYPE_MAP.get(canonical, DEFAULT_TYPE)
        midpoint = (x_min + x_max) / 2
        distance = abs(x_center - midpoint)
        if distance < best_distance:
            best_distance = distance
            best_canonical = canonical
    if best_canonical is None:
        return DEFAULT_TYPE
    if best_distance > snap_cutoff_factor * tolerance:
        logger.debug(
            "icon snap rejected: x_center=%.2f closest=%s distance=%.2f tolerance=%.2f",
            x_center, best_canonical, best_distance, tolerance,
        )
        return DEFAULT_TYPE
    return LEGEND_TYPE_MAP.get(best_canonical, DEFAULT_TYPE)


def _group_words_by_row(words: Iterable[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    rows: dict[int, list[dict[str, Any]]] = {}
    for word in words:
        y_key = round(word["top"] / 4) * 4
        rows.setdefault(y_key, []).append(word)
    for row in rows.values():
        row.sort(key=lambda w: w["x0"])
    return rows


def _matches_in_row(
    zeilen_woerter: list[dict[str, Any]],
) -> list[tuple[dict[str, str], float, float]]:
    """Return all measurement regex matches in a single y-row together with x/y.

    The Hilo layout uses two columns per page; both need to be captured.
    """
    line_text = " ".join(w["text"] for w in zeilen_woerter)
    matches = list(ZEILEN_PATTERN.finditer(line_text))
    if not matches:
        return []

    zeile_y = (zeilen_woerter[0]["top"] + zeilen_woerter[0]["bottom"]) / 2
    results: list[tuple[dict[str, str], float, float]] = []

    for idx, match in enumerate(matches):
        datum, uhrzeit, sbp, dbp, hr = match.groups()
        datum_clean = " ".join(datum.split())
        datum_tag = datum_clean.split()[0]

        # Find the n-th occurrence of the leading day-of-month token in the row
        day_words = [w for w in zeilen_woerter if w["text"] == datum_tag]
        day_words.sort(key=lambda w: w["x0"])
        if idx < len(day_words):
            x_anchor = day_words[idx]["x0"]
        elif day_words:
            x_anchor = day_words[-1]["x0"]
        else:
            time_words = [w for w in zeilen_woerter if w["text"] == uhrzeit]
            time_words.sort(key=lambda w: w["x0"])
            x_anchor = time_words[idx]["x0"] if idx < len(time_words) else (time_words[-1]["x0"] if time_words else 0.0)

        row_payload = {
            "datum": datum_clean,
            "uhrzeit": uhrzeit,
            "sbp": sbp,
            "dbp": dbp,
            "hr": hr,
        }
        results.append((row_payload, x_anchor, zeile_y))
    return results


def _detect_row_type(
    row_y: float,
    row_x: float,
    page_middle: float,
    table_images: list[dict[str, Any]],
    x_ranges: dict[str, tuple[float, float]],
    digest_map: dict[str, str],
    *,
    tolerance: float = ICON_TOLERANCE_PT,
) -> str:
    if not table_images:
        return DEFAULT_TYPE

    row_right_half = row_x >= page_middle
    candidates = []
    for img in table_images:
        img_y_mid = (img["top"] + img["bottom"]) / 2
        img_x_mid = (img["x0"] + img["x1"]) / 2
        y_matches = abs(img_y_mid - row_y) <= Y_MATCH_TOLERANCE_PT
        same_column = (img_x_mid >= page_middle) == row_right_half
        if y_matches and same_column:
            candidates.append(img)

    if not candidates:
        return DEFAULT_TYPE

    nearest = max(candidates, key=lambda i: i["x0"])

    # Primary signal: image stream digest matches one of the calibrated legend
    # icons. This works regardless of which table column the icon sits in.
    digest = _image_digest(nearest)
    if digest and digest in digest_map:
        detected = LEGEND_TYPE_MAP.get(digest_map[digest], DEFAULT_TYPE)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "row icon decision (digest): row_y=%.2f row_x=%.2f digest=%s -> %s",
                row_y, row_x, digest[:10], detected,
            )
        return detected

    # Positional fallback for PDFs whose table icons don't share streams with
    # the legend (older / regenerated reports).
    if not x_ranges:
        return DEFAULT_TYPE
    x_center = (nearest["x0"] + nearest["x1"]) / 2
    detected = _classify_icon(x_center, x_ranges, tolerance=tolerance)
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "row icon decision (x-pos fallback): row_y=%.2f row_x=%.2f "
            "x_center=%.2f -> %s (tolerance=%.2f, candidates=%d)",
            row_y, row_x, x_center, detected, tolerance, len(candidates),
        )
    return detected


def extract_measurements(pdf_source: str | bytes) -> list[ExtractedMeasurement]:
    """Extract measurements incl. measurement_type from a Hilo monthly PDF.

    ``pdf_source`` may be a file path or raw PDF bytes.
    """
    try:
        import pdfplumber  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "pdfplumber ist nicht installiert. Bitte 'pip install pdfplumber' im Backend-venv ausfuehren."
        ) from exc

    if isinstance(pdf_source, (bytes, bytearray)):
        import io

        pdf_context = pdfplumber.open(io.BytesIO(pdf_source))
    else:
        pdf_context = pdfplumber.open(str(pdf_source))

    results: list[ExtractedMeasurement] = []
    # Dedupe by the *full* measurement tuple (date, time, sbp, dbp, hr): two
    # readings can legitimately share the same minute (e.g. an armband reading
    # next to a phone reading at the same `HH:MM`), so deduplicating on
    # (date, time) alone silently drops one of them. The full tuple still
    # protects against a row being matched twice if the regex would ever pick
    # it up across page boundaries.
    seen: set[tuple[str, str, str, str, str]] = set()
    cached_ranges: dict[str, tuple[float, float]] = {}
    cached_tolerance = ICON_TOLERANCE_PT
    cached_digest_map: dict[str, str] = {}

    with pdf_context as pdf:
        total_pages = len(pdf.pages)
        if total_pages < 3:
            logger.warning("Hilo PDF has only %d pages - skipping measurement extraction", total_pages)
            return results

        # Pages 2..N-1 in 1-based numbering map to indices 1..N-2 in 0-based.
        for page_index, page in enumerate(pdf.pages[1:-1], start=2):
            calibrated, digest_map = _calibrate_legend(page)
            if calibrated:
                cached_ranges, cached_tolerance = _build_x_ranges(calibrated)
                logger.debug(
                    "page %d legend calibrated: %s (tolerance=%.2f, digests=%d)",
                    page_index,
                    {k: round(v, 2) for k, v in calibrated.items()},
                    cached_tolerance,
                    len(digest_map),
                )
            elif not cached_ranges:
                logger.warning(
                    "page %d: legend calibration failed and no previous "
                    "ranges available - rows on this page will default to "
                    "armband", page_index,
                )
            if digest_map:
                cached_digest_map = digest_map
            x_ranges = cached_ranges
            tolerance = cached_tolerance
            digest_lookup = cached_digest_map

            page_middle = page.width / 2
            table_y_end = page.height * LEGEND_AREA_Y_RATIO
            table_images = [img for img in page.images if img["top"] < table_y_end]

            words = page.extract_words(x_tolerance=3, y_tolerance=3)
            rows = _group_words_by_row(words)

            row_counter = 0
            for y_key in sorted(rows.keys()):
                row_words = rows[y_key]
                for row_payload, row_x, row_y in _matches_in_row(row_words):
                    dedupe_key = (
                        row_payload["datum"],
                        row_payload["uhrzeit"],
                        row_payload["sbp"],
                        row_payload["dbp"],
                        row_payload["hr"],
                    )
                    if dedupe_key in seen:
                        continue
                    seen.add(dedupe_key)

                    date_iso = parse_date_iso(row_payload["datum"])
                    if not date_iso:
                        logger.warning(
                            "Skipping unparseable Hilo date: %r on page %d",
                            row_payload["datum"],
                            page_index,
                        )
                        continue

                    row_counter += 1
                    measurement_type = _detect_row_type(
                        row_y=row_y,
                        row_x=row_x,
                        page_middle=page_middle,
                        table_images=table_images,
                        x_ranges=x_ranges,
                        digest_map=digest_lookup,
                        tolerance=tolerance,
                    )
                    source_column = "right" if row_x >= page_middle else "left"
                    results.append(
                        ExtractedMeasurement(
                            date_iso=date_iso,
                            time=row_payload["uhrzeit"],
                            systolic=int(row_payload["sbp"]),
                            diastolic=int(row_payload["dbp"]),
                            heart_rate=int(row_payload["hr"]),
                            measurement_type=measurement_type,
                            source_page=page_index,
                            source_column=source_column,
                            row_index_on_page=row_counter,
                        )
                    )

    return results


__all__ = [
    "DEFAULT_TYPE",
    "ExtractedMeasurement",
    "LEGEND_TYPE_MAP",
    "extract_measurements",
    "parse_date_iso",
]
