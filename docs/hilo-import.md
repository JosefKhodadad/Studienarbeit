# Hilo-/Aktiia-PDF-Import

## Überblick
Das Backend erweitert die bestehende Webanwendung um einen textbasierten Import von Hilo-Monatsberichten (PDF) via PyMuPDF (`fitz`).

### API-Endpunkte
- `POST /api/hilo/import`
  - akzeptiert entweder `multipart/form-data` mit Datei (`file`) oder einen lokalen Dateipfad (`file_path`)
  - liefert eine JSON-Vorschau mit `patient`, `report`, `summary`, `measurements`
- `POST /api/hilo/import-and-save`
  - derzeit Alias für den Import-Preview-Flow

## Parser-Logik
1. **Seite 1**: Extraktion von Personendaten, Berichtsmonat/-jahr und Summary-Blöcken (Tag/Ruhe, Nacht, Alle Messungen).
2. **Seiten 2–17**: Extraktion der Messreihen mit Datum, Uhrzeit, SYS, DIA, HR.
3. **Seite 18**: wird gezielt als Datenquelle ignoriert.
4. **Mapping**: Vereinheitlichung in API-Schema inkl. `age_at_report_date`, ISO-Datetime und Herkunftsfeldern (`source_page`, `source_column`, `row_index_on_page`).

## Icon-Erkennung ohne Binär-Exports
- Es werden keine externen Bilddateien erzeugt.
- Bildobjekte werden direkt aus dem geöffneten PDF extrahiert (`page.get_images` + `doc.extract_image`).
- Legenden-Icons auf Seite 18 werden zur Laufzeit als Referenzsignaturen gelernt.
- Messzeilen-Icons werden mit diesen Signaturen verglichen.
- Wenn kein sicheres Matching möglich ist: `measurement_type = "unknown"`.

## Bekannte Grenzen
- Exakte Layoutabweichungen im PDF können Regex-basierte Extraktion beeinflussen.
- Die Zuordnung von Icon zu Zeile basiert auf der Reihenfolge der Bildobjekte pro Seite; bei stark abweichendem Rendering kann `unknown` zurückgegeben werden.
- Ohne installierte Abhängigkeit `pymupdf` wird ein klarer Laufzeitfehler geworfen.

## Beispiel
```bash
curl -X POST http://localhost:8000/api/hilo/import \
  -F "file_path=sample_data/hilo_report.pdf"
```
