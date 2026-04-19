from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.schemas.hilo_import import HiloImportResponse
from app.services.hilo.parser import HiloPDFParser

router = APIRouter(prefix="/api/hilo", tags=["hilo-import"])


@router.post("/import", response_model=HiloImportResponse)
async def import_hilo_report(
    file: UploadFile | None = File(default=None),
    file_path: str | None = Form(default=None),
) -> HiloImportResponse:
    if not file and not file_path:
        raise HTTPException(status_code=400, detail="Bitte Datei-Upload oder file_path angeben.")

    parser = HiloPDFParser()
    try:
        if file:
            content = await file.read()
            parsed = parser.parse_bytes(content)
        else:
            path = Path(file_path or "")
            if not path.exists():
                raise HTTPException(status_code=404, detail=f"Datei nicht gefunden: {path}")
            parsed = parser.parse_file(path)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Hilo-PDF konnte nicht geparst werden: {exc}") from exc

    return HiloImportResponse.model_validate(parsed)


@router.post("/import-and-save", response_model=HiloImportResponse)
async def import_and_save_hilo_report(
    file: UploadFile | None = File(default=None),
    file_path: str | None = Form(default=None),
) -> HiloImportResponse:
    # Der bestehende App-Flow speichert manuelle Werte clientseitig in localStorage.
    # Diese API liefert vorerst eine validierte Import-Vorschau zurück.
    return await import_hilo_report(file=file, file_path=file_path)
