# Hilo-Import und Dashboard-Integration

## Ziel

Die Hilo-PDF-Parserlogik bleibt im Backend gekapselt. Das Frontend zeigt das
klassische 3-Spalten-Dashboard und nutzt den PDF-Import als kompakte
Benutzerfunktion im Dashboard. Zusaetzlich koennen importierte Messungen ueber
den Konfigurator bearbeitet, geloescht und ergaenzt werden.

## Backend-Aufbau

- `HiloParserService` / `HiloPDFParser`
  - zweistufiger Parser:
    - PyMuPDF liest Monat, Person und Summary-Text
    - `pdfplumber_extractor` liest die Messtabelle positionsbasiert und
      klassifiziert jede Zeile ueber die Legenden-Icons am Seitenfuss
  - Fallback auf den reinen Textpfad (PyMuPDF), wenn pdfplumber keine Zeilen liefert
- `AggregationService`
  - normalisiert IDs, Summary und Messarten
  - vergibt pro Messung eine stabile `entry_id`
  - setzt `entry_status = original` und speichert `original_values`
  - berechnet Warnungen und Dashboard-Daten
  - bietet `rebuild_payload()` fuer den CRUD-Pfad (gleiche Logik, andere Quelle)
- `ImportService`
  - nimmt den Upload entgegen
  - speichert den zuletzt importierten Bericht
  - implementiert `create_measurement`, `update_measurement`, `delete_measurement`
    inklusive der Statusregeln
- `InMemoryImportRepository`
  - haelt den aktuellen Bericht fuer das Dashboard
  - `replace_current_payload()` ueberschreibt den aktuellen Bericht
    (referenzgleich ueber die `report_id`)

## Verwendete Endpunkte

```text
POST   /api/imports/hilo
POST   /api/hilo/import
POST   /api/hilo/import-and-save
POST   /api/imports/measurements
GET    /api/dashboard/monthly-summary
GET    /api/patients/{id}/profile
POST   /api/measurements
PATCH  /api/measurements/{entry_id}
DELETE /api/measurements/{entry_id}
```

## Messart-Erkennung

- Legenden-Icons werden pro Seite aus dem unteren Rand (ca. unteres Siebtel)
  ueber die Bildobjekte ausgelesen und als Referenzpositionen gelernt
- Messzeilen werden zeilenweise gruppiert; die Messart wird ueber die
  X-Position des zugehoerigen Icons bestimmt (linke bzw. rechte Spalte)
- Mapping der Legendentexte auf interne Typen:
  - `Kalibrierung mit Manschette` -> `cuff_calibration`
  - `Manschettenmessung` -> `cuff_measurement`
  - `Telefonmessung` -> `phone_measurement`
  - Standard (kein Legendentreffer) -> `armband`

Anzeige im Dashboard:

- `armband` -> Spalte `Manschettenloses Geraet`
- `cuff_calibration` -> Spalte `Manschettenloses Geraet`
- `phone_measurement` -> Spalte `Telefonmessung`
- `cuff_measurement` -> Spalte `Manschettenmessung`
- `unknown` -> nur in Monatsstatistik und Warnungen (Fallback)

## Status-Modell

Jede Messung besitzt neben den Werten:

- `entry_id` - stabiler, eindeutiger Bezeichner
- `source` - `hilo_pdf`, `manual`, `manual_local` oder `mobile_sync`
- `source_file` - Dateiname des PDFs beim Import
- `import_batch_id` - Import-Batch, dem die Zeile zugeordnet ist
- `entry_status` - einer der folgenden Werte:
  - `original` - unveraendert aus dem PDF
  - `edited` - mindestens ein Feld wurde geaendert
  - `restored_original` - Werte wurden auf die gespeicherten Originalwerte zurueckgesetzt
  - `manual` - Neueintrag ohne PDF-Herkunft
- `original_values` - Snapshot der importierten Werte (falls vorhanden)
- `edited_at` - Zeitpunkt der letzten Bearbeitung

Die Uebergaenge werden beim Update automatisch bestimmt:
Stimmen nach der Bearbeitung alle Werte wieder mit `original_values` ueberein,
wird `entry_status` auf `restored_original` gesetzt (bzw. `original`, wenn bereits
unveraendert). Sonst auf `edited`.

## Frontend-Verhalten

- Dashboard: gestylter `PDF auswaehlen`-Button (kein nackter Datei-Input).
  Nach erfolgreichem Import werden Monat, Profil, Monatsstatistik, Warnungen,
  Messspalten und Charts geladen.
- Konfigurator: Tabelle mit allen Eintraegen (importiert + lokal). Pro Zeile
  werden Typ-Badge, Status-Badge und Quelle angezeigt. Importierte und per
  Backend angelegte Eintraege (`scope = server`) lassen sich ueber
  `Bearbeiten` per Dialog aendern (PATCH) oder per `Loeschen` entfernen
  (DELETE). Lokale Eintraege (`scope = local`) bleiben im `localStorage`.
- Neueintrag im Konfigurator: Radiobutton `Speicherziel` entscheidet, ob die
  Messung lokal im Browser bleibt oder per `POST /api/measurements` in den
  aktuellen Bericht geschrieben wird.

## Grenzen

- Speicherung ist aktuell nur In-Memory
- die Icon-Erkennung kann bei stark abweichenden PDF-Layouts `unknown` liefern
- lokale Konfigurator-Eintraege werden ausschliesslich im Browser gehalten
  und erscheinen nicht im Dashboard
