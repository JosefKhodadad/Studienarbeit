import pdfplumber
import re
import sys
import csv
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

MONATE = {
    "januar": 1, "februar": 2, "maerz": 3, "märz": 3,
    "april": 4, "mai": 5, "juni": 6, "juli": 7,
    "august": 8, "september": 9, "oktober": 10,
    "november": 11, "dezember": 12
}

ZEILEN_PATTERN = re.compile(
    r"(\d{1,2}\s+\w+,\s+\d{2})\s+"
    r"(\d{2}:\d{2})\s+"
    r"(\d{2,3})\s+"
    r"(\d{2,3})\s+"
    r"(\d{2,3})"
)

def parse_datum_uhrzeit(datum_str: str, uhrzeit_str: str):
    try:
        teile = datum_str.replace(",", "").split()
        if len(teile) != 3:
            return None
        tag   = int(teile[0])
        monat = MONATE.get(teile[1].lower())
        jahr  = 2000 + int(teile[2])
        if monat is None:
            return None
        stunde, minute = map(int, uhrzeit_str.split(":"))
        return datetime(jahr, monat, tag, stunde, minute)
    except (ValueError, AttributeError):
        return None

def bestimme_icon_typ(bild_x: float, icon_x_bereiche: dict) -> str:
    """Messtyp anhand der X-Position des Bildes bestimmen."""
    beste_distanz = float("inf")
    bester_typ    = "Armband"
    for typ, (x_min, x_max) in icon_x_bereiche.items():
        if x_min <= bild_x <= x_max:
            return typ
        mitte = (x_min + x_max) / 2
        dist  = abs(bild_x - mitte)
        if dist < beste_distanz:
            beste_distanz = dist
            bester_typ    = typ
    return bester_typ

def kalibriere_icon_positionen(page) -> dict:
    """
    Liest die Legende am Seitenende und bestimmt die X-Positionen
    der drei Icon-Typen. Gibt dict {typ: x_mitte} zurueck.
    """
    page_height     = page.height
    legende_y_start = page_height * 0.85

    woerter         = page.extract_words(x_tolerance=5, y_tolerance=3)
    legende_woerter = [w for w in woerter if w["top"] >= legende_y_start]
    legende_bilder  = [img for img in page.images if img["top"] >= legende_y_start]

    schluessel = {
        "kalibrierung": "Kalibrierung mit Manschette",
        "manschetten":  "Manschettenmessung",
        "telefon":      "Telefonmessung",
    }

    icon_positionen = {}
    for wort in legende_woerter:
        wort_text = wort["text"].lower()
        for key, typ in schluessel.items():
            if key in wort_text and typ not in icon_positionen:
                wort_x     = wort["x0"]
                wort_y     = wort["top"]
                kandidaten = [
                    img for img in legende_bilder
                    if img["x1"] <= wort_x + 5
                    and abs(img["top"] - wort_y) < 20
                ]
                if kandidaten:
                    naechstes = max(kandidaten, key=lambda i: i["x1"])
                    x_mitte   = (naechstes["x0"] + naechstes["x1"]) / 2
                    icon_positionen[typ] = x_mitte

    return icon_positionen

def baue_x_bereiche(icon_positionen: dict, toleranz: float = 25.0) -> dict:
    return {
        typ: (x - toleranz, x + toleranz)
        for typ, x in icon_positionen.items()
    }

def extract_zeilen_mit_position(page) -> list:
    """
    Extrahiert alle Messzeilen einer Seite MIT ihrer X/Y-Position.

    KERNFIX gegenueber vorheriger Version:
    - Pro Y-Zeile werden ALLE Regex-Treffer gesucht (findall statt search)
    - Jeder Treffer bekommt die X-Position seines Datum-Worts zugewiesen
    - So werden linke UND rechte Tabellenspalte korrekt erfasst

    Ablauf:
    1. Woerter nach Y-Zeile gruppieren (Toleranz 4pt)
    2. Innerhalb jeder Y-Zeile: Woerter nach X sortieren
    3. Zeile als Text zusammensetzen
    4. findall() -> alle Messungen in dieser Zeile finden
    5. Fuer jeden Treffer: X-Position des zugehoerigen Datum-Worts bestimmen
    """
    woerter = page.extract_words(x_tolerance=3, y_tolerance=3)

    # Woerter nach Y-Zeile gruppieren
    zeilen_dict = {}
    for w in woerter:
        y_key = round(w["top"] / 4) * 4
        zeilen_dict.setdefault(y_key, []).append(w)

    # Jede Zeile nach X sortieren
    for y_key in zeilen_dict:
        zeilen_dict[y_key].sort(key=lambda w: w["x0"])

    ergebnisse = []

    for y_key, zeilen_woerter in sorted(zeilen_dict.items()):
        zeilen_text = " ".join(w["text"] for w in zeilen_woerter)

        # ALLE Treffer in dieser Zeile finden (links + rechts)
        alle_treffer = list(ZEILEN_PATTERN.finditer(zeilen_text))
        if not alle_treffer:
            continue

        # Y-Mitte der Zeile
        zeile_y = (zeilen_woerter[0]["top"] + zeilen_woerter[0]["bottom"]) / 2

        for treffer_idx, match in enumerate(alle_treffer):
            datum, uhrzeit, sbp, dbp, hr = match.groups()
            datum = " ".join(datum.split())

            # X-Position bestimmen:
            # Der erste Treffer gehoert zur linken Haelfte,
            # der zweite Treffer zur rechten Haelfte.
            # Wir suchen das Datum-Wort (erste Zahl der Gruppe) im Wort-Array.
            #
            # Strategie: Alle Datum-Woerter (beginnen mit Zahl) in der Zeile
            # nach X sortieren und den n-ten nehmen (n = treffer_idx).
            datum_tag = datum.split()[0]  # z.B. "26"
            datum_woerter_in_zeile = [
                w for w in zeilen_woerter
                if w["text"] == datum_tag
            ]
            datum_woerter_in_zeile.sort(key=lambda w: w["x0"])

            if treffer_idx < len(datum_woerter_in_zeile):
                zeile_x = datum_woerter_in_zeile[treffer_idx]["x0"]
            elif datum_woerter_in_zeile:
                zeile_x = datum_woerter_in_zeile[-1]["x0"]
            else:
                # Fallback: Uhrzeit-Wort suchen
                uhrzeit_woerter = [w for w in zeilen_woerter if w["text"] == uhrzeit]
                uhrzeit_woerter.sort(key=lambda w: w["x0"])
                if treffer_idx < len(uhrzeit_woerter):
                    zeile_x = uhrzeit_woerter[treffer_idx]["x0"]
                elif uhrzeit_woerter:
                    zeile_x = uhrzeit_woerter[-1]["x0"]
                else:
                    zeile_x = 0  # Letzter Fallback

            ergebnisse.append({
                "datum":   datum,
                "uhrzeit": uhrzeit,
                "sbp":     int(sbp),
                "dbp":     int(dbp),
                "hr":      int(hr),
                "zeile_x": zeile_x,
                "zeile_y": zeile_y,
            })

    return ergebnisse

def extract_hilo_data(pdf_path: str):
    """
    Hauptfunktion: Extrahiert alle Messungen inkl. korrektem Messtyp.
    Beide Tabellenspalten (links + rechts) werden vollstaendig erfasst.
    """
    measurements       = []
    seen               = set()
    messtyp_stats      = {}
    globale_x_bereiche = {}

    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        print(f"Gesamtseiten: {total_pages} - Verarbeite Seiten 2 bis {total_pages - 1}\n")

        for page_num, page in enumerate(pdf.pages[1:-1], start=2):

            # Schritt 1: Icon-Positionen aus Legende kalibrieren
            icon_positionen = kalibriere_icon_positionen(page)
            if icon_positionen:
                x_bereiche         = baue_x_bereiche(icon_positionen, toleranz=25.0)
                globale_x_bereiche = x_bereiche
                print(f"  Seite {page_num}: Legende -> {list(icon_positionen.keys())}")
            else:
                x_bereiche = globale_x_bereiche
                print(f"  Seite {page_num}: Keine Legende -> globale X-Bereiche.")

            # Schritt 2: Seitenmitte als Trennlinie links/rechts
            seiten_mitte = page.width / 2

            # Schritt 3: Bilder im Tabellenbereich laden
            tabellen_y_ende = page.height * 0.85
            tabellen_bilder = [
                img for img in page.images
                if img["top"] < tabellen_y_ende
            ]

            # Schritt 4: Messzeilen MIT X/Y-Position (beide Spalten!)
            zeilen     = extract_zeilen_mit_position(page)
            page_count = 0

            for zeile in zeilen:
                datum   = zeile["datum"]
                uhrzeit = zeile["uhrzeit"]

                key = (datum, uhrzeit)
                if key in seen:
                    continue
                seen.add(key)

                zeile_x = zeile["zeile_x"]
                zeile_y = zeile["zeile_y"]

                zeile_in_rechter_haelfte = zeile_x >= seiten_mitte

                # Schritt 5: Bild suchen - Y UND X-Haelfte muessen passen
                messtyp = "Armband"

                if tabellen_bilder and x_bereiche:
                    kandidaten = []
                    for img in tabellen_bilder:
                        img_y_mitte = (img["top"] + img["bottom"]) / 2
                        img_x_mitte = (img["x0"] + img["x1"]) / 2

                        y_passt             = abs(img_y_mitte - zeile_y) <= 8
                        bild_rechts         = img_x_mitte >= seiten_mitte
                        x_haelfte_passt     = (bild_rechts == zeile_in_rechter_haelfte)

                        if y_passt and x_haelfte_passt:
                            kandidaten.append(img)

                    if kandidaten:
                        bild   = max(kandidaten, key=lambda i: i["x0"])
                        bild_x = (bild["x0"] + bild["x1"]) / 2
                        messtyp = bestimme_icon_typ(bild_x, x_bereiche)

                messtyp_stats[messtyp] = messtyp_stats.get(messtyp, 0) + 1

                dt = parse_datum_uhrzeit(datum, uhrzeit)
                measurements.append({
                    "Datum":     datum,
                    "Uhrzeit":   uhrzeit,
                    "SBP":       zeile["sbp"],
                    "DBP":       zeile["dbp"],
                    "HR":        zeile["hr"],
                    "Messtyp":   messtyp,
                    "_sort_key": dt
                })
                page_count += 1

            print(f"  Seite {page_num}: {page_count} Messungen verarbeitet.")

    # Chronologisch sortieren
    measurements.sort(key=lambda x: (x["_sort_key"] is None, x["_sort_key"]))
    for m in measurements:
        del m["_sort_key"]

    print(f"\nGesamt: {len(measurements)} Messungen.\n")
    print("Messtyp-Verteilung:")
    print("-" * 48)
    for typ, anzahl in sorted(messtyp_stats.items(), key=lambda x: -x[1]):
        print(f"  {typ:<42} {anzahl:>5}x")
    print("-" * 48)

    return measurements

def print_measurements(measurements: list):
    print(f"\n{'Nr.':<5} {'Datum':<18} {'Uhrzeit':<10} "
          f"{'SBP':<6} {'DBP':<6} {'HR':<6} {'Messtyp'}")
    print("-" * 85)
    for i, m in enumerate(measurements, start=1):
        print(
            f"{i:<5} {m['Datum']:<18} {m['Uhrzeit']:<10} "
            f"{m['SBP']:<6} {m['DBP']:<6} {m['HR']:<6} {m['Messtyp']}"
        )
    print("-" * 85)
    print(f"Gesamt: {len(measurements)} Messungen.")

def export_to_csv(measurements: list, output_path: str = "hilo_rohdaten.csv"):
    fieldnames = ["Datum", "Uhrzeit", "SBP", "DBP", "HR", "Messtyp"]
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(measurements)
    print(f"\nCSV gespeichert: {output_path}")

# --- Hauptprogramm ---
if __name__ == "__main__":
    PDF_DATEI = r"C:\Users\z004u9xh\OneDrive - Siemens AG\StudienarbeitMonitor\Studienarbeit\Hilo_Blutdruckbericht_JK_Apr.2026.pdf"
    PDF_DATEI_ONE = r"C:\Users\z004u9xh\OneDrive - Siemens AG\StudienarbeitMonitor\Studienarbeit\Hilo_Blutdruckbericht_JK_Maerz2026.pdf"
    daten = extract_hilo_data(PDF_DATEI_ONE)
    if daten:
        print_measurements(daten)
        export_to_csv(daten)
    else:
        print("Keine Daten gefunden.")