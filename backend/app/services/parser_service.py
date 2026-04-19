from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.hilo.parser import HiloPDFParser


class HiloParserService:
    """Thin adapter that keeps PDF-specific parsing behind a dedicated service."""

    def __init__(self, parser: HiloPDFParser | None = None) -> None:
        self._parser = parser or HiloPDFParser()

    def parse_file(self, file_path: str | Path) -> dict[str, Any]:
        return self._parser.parse_file(file_path)

    def parse_bytes(self, content: bytes) -> dict[str, Any]:
        return self._parser.parse_bytes(content)
