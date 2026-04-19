# BlutdruckMonitor

BlutdruckMonitor ist eine Webanwendung fuer Blutdruckdaten mit einem klassischen 3-Spalten-Dashboard und einem integrierten Hilo-PDF-Import.

## Funktionen

- altes Dashboard-Layout mit drei Messspalten
- manueller Konfigurator fuer lokale Browser-Eintraege
- PDF-Import fuer Hilo-Monatsberichte
- Extraktion von:
  - Berichtsmonat
  - Personendaten (Name, E-Mail, Geschlecht, Geburtsdatum, Groesse, Gewicht)
  - Blutdruckmessungen mit Zeitstempel (SBP/DBP/HR)
  - Monatsuebersicht (Mittelwert, SD, Max, Mindest, Anzahl) fuer Tag (Ruhe), Nacht und Alle Messungen
  - Messart soweit per Icon erkennbar, andernfalls ``unknown``
- Anzeige von:
  - Monatszusammenfassung
  - Personprofil
  - Parsing-Warnungen
  - Verlaufscharts pro Messart

## Voraussetzungen

- Python 3.11 oder 3.12
- Moderner Browser (Chrome, Firefox, Edge)

Empfohlen wird eine virtuelle Umgebung:

```bash
cd backend
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate
pip install -r requirements.txt
```

## Start

### 1. Backend starten (Port 8000)

Aus dem ``backend``-Verzeichnis (mit aktivierter venv):

```bash
# Variante A: uvicorn ist im PATH
uvicorn app.main:app --reload --port 8000

# Variante B: falls "uvicorn" nicht gefunden wird (typisch Windows ohne aktivierte venv)
python -m uvicorn app.main:app --reload --port 8000
```

Gesundheitscheck im Browser oder via ``curl``:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

### 2. Frontend starten (Port 8080)

Das Frontend besteht aus statischen Dateien unter ``frontend/``. Es muss ueber einen HTTP-Server bereitgestellt werden, damit Browser keine ``file://``-Restriktionen anwenden:

```bash
cd frontend
python -m http.server 8080
```

Danach im Browser oeffnen:

```
http://127.0.0.1:8080/index.html
```

### Hinweise

- Das Frontend verwendet als Backend-Basis ``http://127.0.0.1:8000``. Wenn das Backend auf einem anderen Port oder Host laeuft, muss ``DEFAULT_API_BASE`` in ``frontend/app.js`` angepasst werden.
- CORS ist im Backend fuer alle Origins freigegeben (Entwicklungsmodus).
- Sowohl ``http://127.0.0.1:8080`` als auch ``http://localhost:8080`` funktionieren.

## API

Das Frontend zeigt keine technische API-Steuerung mehr an, nutzt intern aber weiterhin HTTP gegen das Backend.

Verwendete Endpunkte:

```text
POST /api/imports/hilo          (multipart/form-data: file)
POST /api/hilo/import           (Alias)
POST /api/hilo/import-and-save  (Alias)
POST /api/imports/measurements  (JSON-Body fuer Mobile-Sync)
GET  /api/dashboard/monthly-summary[?report_id=...]
GET  /api/patients/{id}/profile
GET  /health
```

Beispiel Upload manuell testen:

```bash
curl -F "file=@Hilo_Blutdruckbericht_JK_März2026.pdf" http://127.0.0.1:8000/api/imports/hilo
```

## Import-Ablauf

1. Im Dashboard wird eine PDF-Datei ausgewaehlt.
2. Das Frontend sendet die Datei an ``POST /api/imports/hilo``.
3. Das Backend parst das PDF (PyMuPDF) und extrahiert Monat, Patient, Summary, Messwerte und soweit moeglich Messarten.
4. Die Daten werden in einem In-Memory-Repository gespeichert.
5. Das Dashboard laedt anschliessend den aktuellen Bericht ueber ``GET /api/dashboard/monthly-summary``.

## Messarten im Dashboard

Importierte Messungen werden im alten 3-Spalten-Dashboard wie folgt zugeordnet:

- ``cuff_calibration`` -> Spalte ``Manschettenloses Geraet``
- ``phone_measurement`` -> Spalte ``Telefonmessung``
- ``cuff_measurement`` -> Spalte ``Manschettenmessung``
- ``unknown`` -> nur in Monatsstatistik und Warnungen (keine Einsortierung in eine Spalte)

## Bekannte Grenzen

- Die Berichtsdaten werden aktuell nur im laufenden Backend-Prozess gehalten (In-Memory).
- Hilo exportiert aktuell keine zuverlaessig positionsabhaengigen Legenden-Icons pro Zeile. Wenn keine klare Messart ermittelbar ist, werden Messungen als ``unknown`` markiert und erscheinen nur in der Monatsstatistik. Eine entsprechende Warnung wird im Dashboard angezeigt.
- Das Frontend nutzt fuer manuelle Eintraege weiterhin ``localStorage``.

## Tests

Die Tests arbeiten textbasiert mit Mock-Daten und benoetigen keine echten PDF-Fixtures:

```bash
cd backend
python -m pytest
```

## Troubleshooting

| Problem | Ursache | Loesung |
| --- | --- | --- |
| ``Aufrufen fehlgeschlagen`` / ``Backend ... nicht erreichbar`` | Backend laeuft nicht oder auf falschem Port | ``uvicorn app.main:app --reload --port 8000`` ausfuehren und ``http://127.0.0.1:8000/health`` pruefen |
| ``ModuleNotFoundError: fitz`` | PyMuPDF fehlt | ``pip install -r requirements.txt`` in aktivierter venv |
| ``uvicorn: command not found`` (Windows) | venv nicht aktiv oder Scripts-Pfad fehlt | venv aktivieren oder ``python -m uvicorn ...`` verwenden |
| Upload liefert 422 ``Hilo PDF import failed`` | PDF-Inhalt weicht zu stark vom Hilo-Layout ab | Parsing-Warnungen im Dashboard pruefen; bei Bedarf ``POST /api/imports/measurements`` mit JSON verwenden |
| Dashboard-Spalten leer trotz Import | Alle Messungen wurden als ``unknown`` klassifiziert | Ist der dokumentierte Fallback; siehe Abschnitt ``Bekannte Grenzen`` |
