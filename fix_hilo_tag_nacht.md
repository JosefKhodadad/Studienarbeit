# Fix: Hilo Tag/Nacht-Werte im Monatsbericht

## Ursprünglicher Fehler
Im Dashboard wurden Tag- und Nachtbereiche inkonsistent dargestellt: Die Nacht-Anzahl war identisch zur Tag-Anzahl, obwohl das PDF unterschiedliche Werte in der Übersichtstabelle enthält. Dadurch wirkte die Trennung Tag/Nacht im Browser fehlerhaft.

## Exakte Fehlerursache (wo und warum)
- **Datei:** `backend/app/services/hilo/extractor.py`
- **Funktion:** `_extract_tabular_summary(...)`
- **Datenstruktur:** `summary["day_rest"|"night"|"all_measurements"]["measurements"]`
- **Konkreter Fehler:** Für die Zeile `Messungen` wurde nur ein 3er-Format erwartet (`count=3`).

Im Hilo-PDF liegt die Zeile aber oft als **9 Zahlen** vor (Sys/Dia/HR je Bereich):
- Tag: 3 Werte
- Nacht: 3 Werte
- Alle Messungen: 3 Werte

Der alte Code nahm bei 9er-Layout fälschlich die ersten drei Zahlen (`0,1,2`) und setzte damit Tag/Nacht/Gesamt auf denselben Bereich (Tag/Sys, Tag/Dia, Tag/HR), wodurch Nachtzählungen falsch wurden.

## Minimal geänderte Dateien
- `backend/app/services/hilo/extractor.py`
- `backend/tests/unit/test_hilo_parser.py`

## Was konkret geändert wurde
1. In `_extract_tabular_summary(...)` wird für `Messungen` jetzt zuerst das 9er-Layout gelesen (`count=9`), mit Fallback auf das bisherige 3er-Layout.
2. Bei 9er-Layout werden die korrekten Indizes verwendet:
   - Tag = `counts[0]`
   - Nacht = `counts[3]`
   - Alle = `counts[6]`
3. Bei 3er-Layout bleibt das alte Verhalten (`0,1,2`) erhalten.
4. Ein Unit-Test wurde ergänzt, der genau dieses 9er-Layout absichert.

## Warum diese Änderung ausreichend und korrekt ist
- Die Änderung greift **nur** an der Stelle ein, an der die Fehlzuordnung entsteht.
- Keine neue Architektur, keine neue Abhängigkeit, kein Refactoring.
- Parser-Output liefert danach getrennte und korrekte Zählwerte für Tag/Nacht/Gesamt.
- Frontend nutzt diese API-Felder bereits direkt; mit korrektem Backend-Output ist die Anzeige korrekt.

## Bewusst NICHT geänderte Teile
- Keine Änderungen an FastAPI-Routen/Response-Modellen.
- Keine Änderungen am Frontend-Mapping.
- Keine Änderungen an Messreihen-Extraktion oder Klassifikationslogik.

## Verifikation
- Unit-Test für 9er-`Messungen`-Layout hinzugefügt und erfolgreich ausgeführt.
- Reale PDF-Prüfung (`Hilo_Blutdruckbericht_JK_Apr.2026.pdf`) bestätigt getrennte Werte nach Fix:
  - Tag `518`
  - Nacht `162`
  - Alle `680`
