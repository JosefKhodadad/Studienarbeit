# Hilo-Import und Dashboard-Integration

## Ziel

Die vorhandene Hilo-PDF-Parserlogik bleibt im Backend gekapselt. Das Frontend zeigt wieder das klassische 3-Spalten-Dashboard und nutzt den PDF-Import nur noch als kompakte Benutzerfunktion im Dashboard.

## Backend-Aufbau

- `HiloParserService`
  - kapselt den vorhandenen `HiloPDFParser`
  - liest Monat, Person, Summary und Messreihen aus dem PDF
- `ImportService`
  - nimmt den Upload entgegen
  - speichert den zuletzt importierten Bericht
- `AggregationService`
  - normalisiert IDs, Summary und Messarten
  - berechnet Warnungen und Dashboard-Daten
- `InMemoryImportRepository`
  - haelt den aktuellen Bericht fuer das Dashboard

## Verwendete Endpunkte

```text
POST /api/imports/hilo
GET /api/dashboard/monthly-summary
GET /api/patients/{id}/profile
POST /api/hilo/import
POST /api/hilo/import-and-save
```

Das Frontend zeigt keine technische API-Konfiguration mehr an, nutzt diese Routen intern aber weiterhin.

## Messarten im Dashboard

Die Icon-Erkennung bleibt die primaere Loesung fuer die Messart:

- Legenden-Icons aus dem PDF werden als Referenzsignaturen gelernt
- Messzeilen werden ueber die eingebetteten Bildobjekte zugeordnet
- unsichere Treffer bleiben `unknown`

Anzeige im alten Dashboard:

- `cuff_calibration` -> Spalte `Manschettenloses Geraet`
- `phone_measurement` -> Spalte `Telefonmessung`
- `cuff_measurement` -> Spalte `Manschettenmessung`
- `unknown` -> nur in Monatsstatistik und Warnungen

## Frontend-Verhalten

- oben im Dashboard gibt es nur eine kompakte PDF-Importleiste
- nach erfolgreichem Import laedt das Dashboard automatisch:
  - Berichtsmonat
  - Personprofil
  - Monatsstatistik
  - Warnungen
  - Messspalten und Charts
- ohne Import bleibt das alte manuelle Dashboard mit `localStorage`-Daten benutzbar

## Grenzen

- Speicherung ist aktuell nur In-Memory
- die Icon-Erkennung kann bei PDF-Abweichungen `unknown` liefern
- das Frontend faellt lokal auf `http://127.0.0.1:8000` zurueck, wenn es nicht am selben Origin wie das Backend laeuft
