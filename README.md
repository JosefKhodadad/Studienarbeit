# BlutdruckMonitor

BlutdruckMonitor ist eine Webanwendung fuer Blutdruckdaten mit einem klassischen 3-Spalten-Dashboard und einem integrierten Hilo-PDF-Import.

## Funktionen

- Dashboard-Layout mit drei Messspalten (manschettenlos/Armband, Telefon, Manschette)
- Konfigurator mit zwei Speicherzielen:
  - lokale Browser-Eintraege (``localStorage``, wie bisher)
  - direkt in den aktuellen Bericht (persistiert im Backend, erscheint im Dashboard)
- PDF-Import fuer Hilo-Monatsberichte mit pdfplumber-basierter Icon-Erkennung
- Bearbeiten und Loeschen importierter Messungen; Statusverfolgung je Eintrag
  (``original``, ``edited``, ``restored_original``, ``manual``)
- Extraktion von:
  - Berichtsmonat
  - Personendaten (Name, E-Mail, Geschlecht, Geburtsdatum, Groesse, Gewicht)
  - Blutdruckmessungen mit Zeitstempel (SBP/DBP/HR)
  - Monatsuebersicht (Mittelwert, SD, Max, Mindest, Anzahl) fuer Tag (Ruhe), Nacht und Alle Messungen
  - Messart ueber positionsabhaengige Icon-Erkennung
    (``armband``, ``cuff_calibration``, ``phone_measurement``, ``cuff_measurement``;
    ``unknown`` nur als Fallback)
- Anzeige von:
  - Monatszusammenfassung
  - Personprofil
  - Parsing-Warnungen
  - Verlaufscharts pro Messart
  - Statusbadges pro Eintrag im Konfigurator
- Tag/Nacht-Klassifikation per neuronalem Netz (Keras 3 mit PyTorch-Backend)
  inklusive Hintergrund-Trainings-API, Schlafepisoden-Erkennung und
  Visualisierung der geschaetzten Schlafphasen im Verlaufschart

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
POST   /api/imports/hilo          (multipart/form-data: file)
POST   /api/hilo/import           (Alias)
POST   /api/hilo/import-and-save  (Alias)
POST   /api/imports/measurements  (JSON-Body fuer Mobile-Sync)
GET    /api/dashboard/monthly-summary[?report_id=...]
GET    /api/patients/{id}/profile
POST   /api/measurements          (JSON-Body: neue Messung im aktuellen Bericht)
PATCH  /api/measurements/{entry_id}  (JSON-Body: Felder einer Messung aktualisieren)
DELETE /api/measurements/{entry_id}  (Messung aus dem aktuellen Bericht entfernen)
POST   /api/ml/train              (Training des Tag/Nacht-Modells im Hintergrund)
GET    /api/ml/status             (aktueller Zustand des Trainingslaufs)
GET    /api/ml/model-info         (Artefakt-Pfad + Metadaten des letzten Modells)
POST   /api/ml/classify           (Tag/Nacht-Klassifikation einer Messliste)
GET    /health
```

Beispiel Upload manuell testen:

```bash
curl -F "file=@Hilo_Blutdruckbericht_JK_März2026.pdf" http://127.0.0.1:8000/api/imports/hilo
```

## Import-Ablauf

1. Im Dashboard wird eine PDF-Datei ueber den gestylten ``PDF auswaehlen``-Button gewaehlt.
2. Das Frontend sendet die Datei an ``POST /api/imports/hilo``.
3. Das Backend parst das PDF zweistufig:
   - PyMuPDF extrahiert Monat, Patient und Summary-Text.
   - pdfplumber liest die Messtabelle positionsbasiert und ordnet jede Zeile
     ueber die Legenden-Icons einer Messart zu
     (``armband``, ``cuff_calibration``, ``phone_measurement``, ``cuff_measurement``).
4. Jeder Messung werden eine eindeutige ``entry_id`` und die Originalwerte
   (``original_values``) zugeordnet. Der ``entry_status`` ist zu Beginn ``original``.
5. Die Daten werden in einem In-Memory-Repository gespeichert.
6. Das Dashboard laedt anschliessend den aktuellen Bericht ueber
   ``GET /api/dashboard/monthly-summary``.

## Messart-Zuordnung im Dashboard

- ``armband`` -> Spalte ``Manschettenloses Geraet``
- ``cuff_calibration`` -> Spalte ``Manschettenloses Geraet`` (Kalibrierlauf wird dort mitgezaehlt)
- ``phone_measurement`` -> Spalte ``Telefonmessung``
- ``cuff_measurement`` -> Spalte ``Manschettenmessung``
- ``unknown`` -> nur in Monatsstatistik und Warnungen (Fallback bei fehlender Icon-Zuordnung)

## Status einer Messung

Jede Messung traegt einen ``entry_status``:

- ``original`` - wurde aus dem PDF importiert und nicht veraendert
- ``edited`` - wurde nach dem Import bearbeitet
- ``restored_original`` - wurde bearbeitet und anschliessend wieder auf die
  Originalwerte zurueckgesetzt (automatisch erkannt beim erneuten Speichern)
- ``manual`` - im Konfigurator als neue Messung angelegt (kein Original)

Im Konfigurator wird der Status pro Eintrag als farbiges Badge angezeigt.
Bearbeitungen sind ueber den ``Bearbeiten``-Button in der Tabelle moeglich.

## Manueller Konfigurator: zwei Speicherziele

Im Konfigurator kann beim Anlegen einer neuen Messung zwischen zwei
Speicherzielen gewaehlt werden:

- ``Nur lokal`` - Eintrag verbleibt im Browser (``localStorage``), wird nicht an das Backend gesendet.
- ``In aktuellen Bericht`` - Eintrag wird per ``POST /api/measurements`` an das Backend
  uebertragen, persistiert im aktuellen Monatsbericht und erscheint unmittelbar
  im Dashboard.

## Tag/Nacht-Klassifikation (Keras 3 + PyTorch)

Zur Trennung von Tag- und Nachtmessungen kommt zusaetzlich zur regelbasierten
Heuristik (Uhrzeit + erlaubte Messarten) ein kleines neuronales Netz zum
Einsatz. Implementiert ist es mit Keras 3 unter Verwendung des
PyTorch-Backends (``KERAS_BACKEND=torch``). TensorFlow wird unter Windows mit
OneDrive-Pfaden problematisch (lange Dateinamen, native DLLs); Keras + Torch
laeuft dort stabil und liefert die gleichen Modell-APIs.

### Pipeline

1. ``app/services/ml/feature_engineering.py`` baut Features aus Zeit
   (sin/cos der Stunde, Wochentag), SBP/DBP/HR und der Messart und liefert
   eine schwache Voretikettierung (``weak_labels``: 1 = Nacht, 0 = Tag) auf
   Basis von Uhrzeit und ``measurement_type`` (Telefonmessungen werden im
   Hilo-Workflow nur am Tag erfasst und entsprechend hart als Tag gelabelt).
2. ``app/services/ml/keras_model.py`` trainiert ein kompaktes MLP
   (Dropout, EarlyStopping, ModelCheckpoint, atomarer Schreibvorgang ueber
   tempdir + ``os.replace``).
3. ``app/services/ml/constraint.py`` setzt den Top-K-Constraint um:
   die ``expected_night_count`` Messungen mit den hoechsten Scores werden
   als Nacht klassifiziert. Diese Erwartung kommt aus der PDF-Monatsuebersicht
   (Zeile *Nacht*, Spalte *Anzahl*) und wirkt damit als harte Constraint auf
   das Modellergebnis. Faellt die Erwartung weg, wird auf einen klassischen
   Score-Schwellenwert zurueckgegriffen.
4. ``app/services/ml/sleep_phase.py`` gruppiert benachbarte Nacht-Messungen
   zu Schlaf-Episoden, schaetzt einen plausiblen Sleep-Onset und vergleicht
   die Episodendauer mit alters-typischen Erwartungswerten (siehe Quellen).

### Background-Trainings-API

```text
POST   /api/ml/train          (startet Training; akzeptiert optional sources)
GET    /api/ml/status         (idle | running | finished | failed + Metadaten)
GET    /api/ml/model-info     (Pfad + letzte Trainingsstatistiken, falls vorhanden)
POST   /api/ml/classify       (klassifiziert eine Messliste mit dem aktiven Modell)
```

Der Trainingslauf laeuft in einem Hintergrund-Thread
(``app/services/ml/training_jobs.py``). Es kann immer nur ein Training
gleichzeitig laufen; ein zweiter Aufruf liefert den aktuellen Lauf zurueck,
ohne ihn zu ueberschreiben. Modellartefakte landen unter
``backend/var/ml_model/`` (``model.keras``, ``feature_stats.json``,
``training_meta.json``).

### Frontend

Im Analyse-Bereich erscheint ein ``Tag/Nacht-Modell``-Panel mit
Statusbadge (``Heuristik`` / ``Training laeuft`` / ``Modell aktiv`` /
``Training fehlgeschlagen``), Auswahl der Trainingsquellen
(Roh-CSV, importierte Berichte) und einem ``Modell trainieren``-Button.
Nach erfolgreichem Training wird die Klassifikationsmethode unterhalb des
Charts angezeigt (``Heuristik``, ``Score-Schwellenwert`` oder
``Keras + Constraint``); im Verlaufschart werden erkannte Schlafepisoden
als dezente Banden hinterlegt.

## Quellen (Schlafphasen-Heuristik)

- Hirshkowitz et al., *National Sleep Foundation's sleep time duration
  recommendations*, Sleep Health, 2015 (alters-typische Schlafdauern).
- ESH/ESC Guidelines for the Management of Arterial Hypertension, 2023
  (Bedeutung von Nacht-/Tag-Blutdruckunterschied, Dipping-Profile).
- American Thoracic Society, *Healthy Sleep in Adults*, ATS Patient
  Education Series, 2017.

## Bekannte Grenzen

- Die Berichtsdaten werden aktuell nur im laufenden Backend-Prozess gehalten (In-Memory).
- Die Icon-Erkennung arbeitet positionsbasiert (pdfplumber). Weicht das
  PDF-Layout stark von den bisher gesehenen Hilo-Berichten ab, bleibt ``unknown``
  als Fallback vorgesehen.
- Lokale Konfigurator-Eintraege (``Nur lokal``) bleiben weiterhin ausschliesslich
  im ``localStorage`` und werden nicht im Dashboard angezeigt.
- Das Keras-Modell wird lokal mit wenigen hundert bis tausend Messungen
  trainiert. Es ersetzt keine klinische Beurteilung und dient nur dazu,
  Tag/Nacht besser zu trennen, wenn die reine Uhrzeit-Heuristik versagt
  (z. B. Schichtarbeit, sehr spaete Abendmessungen). Ohne PDF-Monatsuebersicht
  fehlt der Top-K-Constraint; dann greift der Score-Schwellenwert.

## Tests

Die Tests arbeiten textbasiert mit Mock-Daten und benoetigen keine echten PDF-Fixtures:

```bash
cd backend
python -m pytest
```

Abgedeckt sind:

- Parser (``test_pdf_parser*``, ``test_hilo_pdf_icon_classify.py``)
- ML-Features (``test_ml_feature_engineering.py``)
- Constraint-Ranking (``test_ml_constraint.py``)
- Schlafphasen-Grouping (``test_ml_sleep_phase.py``)
- Hintergrund-Trainingsjob-Zustaende (``test_ml_training_jobs.py``)

## Troubleshooting

| Problem | Ursache | Loesung |
| --- | --- | --- |
| ``Aufrufen fehlgeschlagen`` / ``Backend ... nicht erreichbar`` | Backend laeuft nicht oder auf falschem Port | ``uvicorn app.main:app --reload --port 8000`` ausfuehren und ``http://127.0.0.1:8000/health`` pruefen |
| ``ModuleNotFoundError: fitz`` | PyMuPDF fehlt | ``pip install -r requirements.txt`` in aktivierter venv |
| ``ModuleNotFoundError: pdfplumber`` | pdfplumber fehlt (seit Version 1.2.0 benoetigt) | ``pip install -r requirements.txt`` in aktivierter venv |
| ``uvicorn: command not found`` (Windows) | venv nicht aktiv oder Scripts-Pfad fehlt | venv aktivieren oder ``python -m uvicorn ...`` verwenden |
| Upload liefert 422 ``Hilo PDF import failed`` | PDF-Inhalt weicht zu stark vom Hilo-Layout ab | Parsing-Warnungen im Dashboard pruefen; bei Bedarf ``POST /api/imports/measurements`` mit JSON verwenden |
| Dashboard-Spalten leer trotz Import | Alle Messungen wurden als ``unknown`` klassifiziert | Ist der dokumentierte Fallback; Parsing-Warnungen im Dashboard pruefen |
| ``PATCH /api/measurements/...`` liefert 404 | Kein Bericht aktiv oder ``entry_id`` unbekannt | Zuerst PDF importieren oder Eintrag ueber ``POST /api/measurements`` anlegen |
