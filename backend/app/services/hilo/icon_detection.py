from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IconSignature:
    width: int
    height: int
    size: int
    digest_prefix: bytes


def _build_signature(image_meta: dict[str, Any]) -> IconSignature:
    image_bytes = image_meta.get("image", b"")
    return IconSignature(
        width=int(image_meta.get("width", 0)),
        height=int(image_meta.get("height", 0)),
        size=len(image_bytes),
        digest_prefix=image_bytes[:16],
    )


class IconDetector:
    """Detects measurement type by comparing inline PDF image objects."""

    LEGEND_MAP = {
        "kalibrierung": "calibration_with_cuff",
        "manschette": "cuff_measurement",
        "telefon": "phone_measurement",
    }

    def __init__(self) -> None:
        self._legend_signatures: dict[IconSignature, str] = {}

    def learn_legend(self, legend_images: dict[str, dict[str, Any]]) -> None:
        self._legend_signatures.clear()
        for legend_key, meta in legend_images.items():
            normalized = self.LEGEND_MAP.get(legend_key.lower())
            if not normalized:
                continue
            self._legend_signatures[_build_signature(meta)] = normalized

    def detect(self, image_meta: dict[str, Any] | None) -> str:
        if not image_meta:
            return "unknown"
        detected = self._legend_signatures.get(_build_signature(image_meta))
        return detected or "unknown"
