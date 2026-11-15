#!/usr/bin/env python3
"""
Automatischer Sortierer fuer das 7-Ordner-System (Privat)
Kann:
1. Den Ordner '01_Eingang_Inbox' sortieren (Push-Prinzip)
2. Beliebige Quellordner, Unterordner oder Festplatten scannen und in das 7-Ordner-System uebertragen (Pull-Prinzip)
"""

import os
import sys
import shutil
import re
import json
import zlib
import hashlib
import argparse
import datetime
from pathlib import Path

# Basis-Pfad des 7-Ordner-Systems
BASE_DIR = Path(__file__).resolve().parent
INBOX_DIR = BASE_DIR / "01_Eingang_Inbox"
HISTORY_FILE = BASE_DIR / "sort_history.json"

# Relevante Dateiendungen (Dokumente, Vertraege, Belege, etc.)
VALID_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".odt", ".ods",
    ".txt", ".csv", ".rtf", ".scan", ".jpg", ".jpeg", ".png", ".tif", ".tiff"
}

# Verzeichnisse, die beim Scannen von Festplatten/Ordnern uebersprungen werden
IGNORED_DIRS = {
    "system volume information", "$recycle.bin", "windows", "program files",
    "program files (x86)", "appdata", "node_modules", ".git", ".idea", ".vscode"
}

# Zuordnungsregeln (Schluesselwoerter -> Zielunterordner)
RULES = [
    # 02 Finanzen & Steuern
    {
        "target": "02_Finanzen_Steuern/01_Bank_Kontoauszuege",
        "keywords": ["kontoauszug", "sparkasse", "dkb", "ing", "comdirect", "bank", "visa", "mastercard", "paypal", "ueberweisung"]
    },
    {
        "target": "02_Finanzen_Steuern/02_Steuererklaerung",
        "keywords": ["steuererklaerung", "steuererklärung", "finanzamt", "elster", "lohnsteuer", "einkommensteuer", "steuerbescheid"]
    },
    {
        "target": "02_Finanzen_Steuern/03_Geldanlage_Vorsorge",
        "keywords": ["depot", "trade_republic", "scalable", "aktien", "etf", "fonds", "bauspar", "rente", "altersvorsorge", "börse", "boerse", "investor", "pitchdeck", "inventur", "jwdw"]
    },
    {
        "target": "02_Finanzen_Steuern/04_Kredite_Darlehen",
        "keywords": ["kredit", "darlehen", "tilgung", "zinsen", "finanzierung", "schufa"]
    },
    {
        "target": "02_Finanzen_Steuern/05_Quittungen_Belege",
        "keywords": ["quittung", "barzahlung", "bewirtung"]
    },

    # 03 Vertraege & Versicherungen
    {
        "target": "03_Vertraege_Versicherungen/01_Versicherungen",
        "keywords": ["haftpflicht", "hausrat", "versicherung", "allianz", "huk", "ergo", "devk", "unfallversicherung", "rechtsschutz", "versicherungsbestaetigung"]
    },
    {
        "target": "03_Vertraege_Versicherungen/02_Vertraege_Mobilfunk_Internet",
        "keywords": ["telekom", "vodafone", "o2", "1und1", "mobilfunk", "internet", "router", "handy", "dsl", "simkarte"]
    },
    {
        "target": "03_Vertraege_Versicherungen/03_Fahrzeuge_Kfz",
        "keywords": ["kfz", "auto", "tuev", "tüv", "adac", "zulassung", "fahrzeugschein", "strafzettel", "bußgeld", "utzfahrzeuge", "fahrzeug"]
    },
    {
        "target": "03_Vertraege_Versicherungen/04_Mitgliedschaften_Abos",
        "keywords": ["mitglied", "verein", "fitnessstudio", "abonnement", "abo", "bahncard"]
    },
    {
        "target": "03_Vertraege_Versicherungen/05_Vollmachten_Verfuegungen",
        "keywords": ["vollmacht", "patientenverfuegung", "vorsorgevollmacht", "testament", "notar"]
    },

    # 04 Beruf & Bildung
    {
        "target": "04_Beruf_Bildung/01_Gehaltsabrechnungen",
        "keywords": ["gehalt", "entgelt", "lohn", "lohnabrechnung", "gehaltsabrechnung", "bezuegemitteilung"]
    },
    {
        "target": "04_Beruf_Bildung/02_Arbeitsvertraege",
        "keywords": ["arbeitsvertrag", "anstellungsvertrag", "kuendigung", "aufhebungsvertrag", "abmahnung", "abwart", "aufträge", "auftraege"]
    },
    {
        "target": "04_Beruf_Bildung/03_Zeugnisse_Zertifikate",
        "keywords": ["zeugnis", "zertifikat", "diplom", "bachelor", "master", "weiterbildung", "schulzeugnis", "verriegelung", "spanner", "antrieb", "taenzer", "vstx"]
    },
    {
        "target": "04_Beruf_Bildung/04_Bewerbungen_Lebenslauf",
        "keywords": ["lebenslauf", "bewerbung", "anschreiben", "motivationsschreiben"]
    },

    # 05 Gesundheit & Familie
    {
        "target": "05_Gesundheit_Familie/01_Krankenkasse",
        "keywords": ["aok", "tk", "techniker", "barmer", "krankenkasse", "erstattung", "krankengeld", "bonusprogramm"]
    },
    {
        "target": "05_Gesundheit_Familie/02_Aerzte_Befunde",
        "keywords": ["arzt", "befund", "rezept", "krankenhaus", "labor", "impfung", "zahnarzt", "patientenakte", "diagnose", "fasten", "glucose", "ernaehrung", "rezept", "muffin", "kalorien", "halbrahm"]
    },
    {
        "target": "05_Gesundheit_Familie/03_Familienurkunden",
        "keywords": ["geburtsurkunde", "heiratsurkunde", "stammbuch", "taufschein", "sterbeurkunde"]
    },
    {
        "target": "05_Gesundheit_Familie/04_Impfungen_Vorsorge",
        "keywords": ["impfpass", "impfbuch", "vorsorgepass", "mutterpass"]
    },

    # 06 Wohnen & Eigentum
    {
        "target": "06_Wohnen_Eigentum/01_Miete_Nebenkosten",
        "keywords": ["miete", "mietvertrag", "nebenkosten", "betriebskosten", "heizkosten", "vermieter", "strom", "gas", "wasser", "vattenfall", "eon", "auszug", "reinigung", "mietverband", "mv-merkbl", "umzugsangebot"]
    },
    {
        "target": "06_Wohnen_Eigentum/02_Geraete_Garantien_Belege",
        "keywords": ["garantie", "rechnung", "kassenbon", "mediamarkt", "amazon", "saturn", "otto", "beleg", "lieferschein", "kaufbeleg", "samsung", "jura", "entkalkung", "sideboard", "topper", "jysk", "coop", "ricardo", "chf"]
    },
    {
        "target": "06_Wohnen_Eigentum/03_Handwerker_Renovierung",
        "keywords": ["handwerker", "renovierung", "maler", "sanitaer", "wartung", "schornsteinfeger", "installation", "sicherheitsnachweis"]
    },
    {
        "target": "06_Wohnen_Eigentum/04_Hausverwaltung_Grundbuch",
        "keywords": ["grundbuch", "hausverwaltung", "eigentuemer", "wohnungseigentum", "wirtschaftsplan"]
    },

    # 07 Archiv & Historie
    {
        "target": "07_Archiv_Historie/01_Ausweise_Reisepaesse",
        "keywords": ["ausweis", "reisepass", "fuehrerschein", "personalausweis", "meldebescheinigung", "pass"]
    },
    {
        "target": "07_Archiv_Historie/02_Buecher_Medien",
        "keywords": ["teufelsleib", "franz, andreas", "epub", "roman", "buch"]
    },
    {
        "target": "07_Archiv_Historie/03_Abgeschlossenes",
        "keywords": ["archiv", "historisch", "abgeschlossen", "alt", "geburtstag"]
    },
    {
        "target": "07_Archiv_Historie/04_Erkannte_Duplikate",
        "keywords": ["duplikat"]
    }
]


def extract_pdf_keywords(path: Path) -> str:
    """Extrahiert Text aus PDF-Streams fuer inhaltsbasierte Sortierung."""
    try:
        data = path.read_bytes()
        streams = re.findall(rb'stream[\r\n]+(.*?)[\r\n]+endstream', data, re.DOTALL)
        found_words = []
        for s in streams[:12]:
            try:
                decomp = zlib.decompress(s).decode('latin1', 'ignore')
                words = re.findall(r'[A-Za-z0-9äöüÄÖÜß\.\-_]{4,}', decomp)
                for w in words:
                    w_lower = w.lower()
                    if any(term in w_lower for term in ['coop', 'migros', 'rechnung', 'auftrag', 'schweiz', 'konto', 'steuererkl', 'finanzamt', 'miete', 'versicherung', 'gehalt']):
                        found_words.append(w_lower)
            except Exception:
                pass
        return " ".join(found_words)
    except Exception:
        return ""


def file_hash(path: Path) -> str:
    """Berechnet schnellen Hash fuer Deduplizierung."""
    hasher = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception:
        return ""


def find_target_folder(filename: str, content: str = "") -> str:
    """Bestimmt den Zielordner anhand von Schlagwoertern im Namen/Inhalt."""
    search_text = f"{filename} {content}".lower()
    for rule in RULES:
        for kw in rule["keywords"]:
            if len(kw) <= 3:
                # Kurze Kuerzel wie 'tk', 'ing', 'o2', 'aok' nur als ganzes Wort pruefen
                if re.search(r'\b' + re.escape(kw) + r'\b', search_text, re.IGNORECASE):
                    return rule["target"]
            else:
                if kw in search_text:
                    return rule["target"]
    return None


def collect_source_files(source_dir: Path, recursive: bool = True):
    """Sammelt alle relevanten Dokumente aus dem Quellverzeichnis."""
    found_files = []
    if recursive:
        for root, dirs, files in os.walk(source_dir):
            # Ueberspringe System- und versteckte Ordner
            dirs[:] = [d for d in dirs if d.lower() not in IGNORED_DIRS and not d.startswith(".")]
            for f in files:
                p = Path(root) / f
                if p.suffix.lower() in VALID_EXTENSIONS and not f.startswith("~$") and not f.startswith("."):
                    found_files.append(p)
    else:
        for p in source_dir.iterdir():
            if p.is_file() and p.suffix.lower() in VALID_EXTENSIONS and not p.name.startswith("~$"):
                found_files.append(p)
    return found_files


def run_organization(source_path: Path, action: str = "copy", dry_run: bool = True, recursive: bool = True, target_path: Path = None):
    if not source_path.exists():
        print(f"Fehler: Pfad existiert nicht: {source_path}")
        return

    # Standard-Ziel: Falls kein Ziel angegeben, erstelle <Quellordner>_01 am gleichen Ort
    if target_path:
        dest_base = target_path
    elif source_path != INBOX_DIR:
        dest_base = source_path.parent / f"{source_path.name}_01"
    else:
        dest_base = BASE_DIR

    print("=" * 70)
    print(f"7-ORDNER-SYSTEM: QUELL-SCAN & SORTIERUNG")
    print(f"Quelle:       {source_path}")
    print(f"Ziel:         {dest_base}")
    print(f"Aktion:       {'KOPIEREN (Sicherheitsmodus)' if action == 'copy' else 'VERSCHIEBEN'}")
    print(f"Modus:        {'VORSCHAU (Dry-Run)' if dry_run else 'ECHTBETRIEB'}")
    print(f"Rekursiv:     {'Ja (inkl. aller Unterordner)' if recursive else 'Nein'}")
    print("=" * 70)

    files = collect_source_files(source_path, recursive=recursive)
    if not files:
        print("Keine passenden Dokumente gefunden.")
        return

    print(f"Gefundene Dokumente: {len(files)}\n")

    matched = 0
    unmatched = 0
    seen_hashes = set()
    duplicates = 0
    transaction = {
        "timestamp": datetime.datetime.now().isoformat(),
        "action": action,
        "items": []
    }

    for file_path in files:
        # Duplikatpruefung
        fhash = file_hash(file_path)
        if fhash and fhash in seen_hashes:
            duplicates += 1
            print(f"[DUPLIKAT UEBERSPRUNGEN] {file_path.name}")
            continue
        if fhash:
            seen_hashes.add(fhash)

        # Optional Text anlesen
        content = ""
        if file_path.suffix.lower() in [".txt", ".csv", ".json", ".log"]:
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")[:2000]
            except Exception:
                pass
        elif file_path.suffix.lower() == ".pdf":
            content = extract_pdf_keywords(file_path)

        target_sub = find_target_folder(file_path.name, content)

        if target_sub:
            matched += 1
            dest_dir = dest_base / target_sub
            dest_file = dest_dir / file_path.name

            # Namenskonflikt verhindern
            if dest_file.exists():
                stem = file_path.stem
                suffix = file_path.suffix
                dest_file = dest_dir / f"{stem}_{datetime.datetime.now().strftime('%H%M%S')}{suffix}"

            verb = "KOPIEREN" if action == "copy" else "VERSCHIEBEN"
            status = f"[{verb}]" if not dry_run else f"[VORSCHAU {verb}]"
            print(f"{status} {file_path.name}")
            print(f"   └── Quelle: {file_path.parent}")
            print(f"   └── Ziel:   {target_sub}/")

            if not dry_run:
                dest_dir.mkdir(parents=True, exist_ok=True)
                if action == "copy":
                    shutil.copy2(str(file_path), str(dest_file))
                else:
                    shutil.move(str(file_path), str(dest_file))
                transaction["items"].append({"src": str(file_path), "dest": str(dest_file)})
        else:
            unmatched += 1
            # Unbekannte Dateien landen auf Wunsch in der Inbox zur Pruefung
            print(f"[UNBEKANNT -> INBOX] {file_path.name}")
            print(f"   └── Ziel: 01_Eingang_Inbox/")
            if not dry_run:
                inbox_dir = dest_base / "01_Eingang_Inbox"
                inbox_dir.mkdir(parents=True, exist_ok=True)
                inbox_dest = inbox_dir / file_path.name
                if action == "copy":
                    shutil.copy2(str(file_path), str(inbox_dest))
                else:
                    shutil.move(str(file_path), str(inbox_dest))
                transaction["items"].append({"src": str(file_path), "dest": str(inbox_dest)})

    if not dry_run and transaction["items"]:
        try:
            history = []
            if HISTORY_FILE.exists():
                history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            history.append(transaction)
            HISTORY_FILE.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"Fehler beim Speichern der Historie: {e}")

    print("\n" + "-" * 70)
    print(f"ZUSAMMENFASSUNG:")
    print(f" - Verarbeitete Dateien: {len(files)}")
    print(f" - Automatisch zugeordnet (02-07): {matched}")
    print(f" - Unbekannt / In Inbox verbleibend: {unmatched}")
    print(f" - Erkannte Duplikate: {duplicates}")
    if dry_run:
        print("\nFuehren Sie den Befehl mit '--execute' aus, um die Aktion wirklich anzuwenden.")
    else:
        print(f"\nAktion erfolgreich ausgefuehrt! ({len(transaction['items'])} Dateien)")
        print("Tipp: Mit 'python auto_sort.py --undo' koennen Sie diese Aktion jederzeit rueckgaengig machen.")
    print("=" * 70)


def replace_original_with_target(source_path: Path, target_path: Path = None):
    """Ersetzt den Originalordner durch den sortierten _01 Ordner (mit Sicherheits-Backup)."""
    target = target_path if target_path else (source_path.parent / f"{source_path.name}_01")

    if not source_path.exists():
        print(f"Fehler: Quellordner existiert nicht: {source_path}")
        return
    if not target.exists():
        print(f"Fehler: Sortierter Zielordner existiert nicht: {target}")
        return

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = source_path.parent / f"{source_path.name}_Backup_{ts}"

    print("=" * 70)
    print("ORDNER-AUSTAUSCH (SWAP):")
    print(f"1. Originalordner sichern: {source_path.name} -> {backup_path.name}")
    print(f"2. Sortierten Ordner aktivieren: {target.name} -> {source_path.name}")
    print("=" * 70)

    try:
        source_path.rename(backup_path)
        target.rename(source_path)
        print(f"\nErfolg! '{source_path.name}' enthaelt jetzt die saubere 7-Ordner-Struktur.")
        print(f"Ihr alter unberuehrter Ordner wurde gesichert unter: '{backup_path.name}'")
        print("=" * 70)
    except Exception as e:
        print(f"Fehler beim Umbenennen: {e}")
        # Rollback falls noetig
        if not source_path.exists() and backup_path.exists():
            backup_path.rename(source_path)
    if not HISTORY_FILE.exists():
        print("Keine Historie vorhanden. Nichts zum Rueckgaengigmachen.")
        return

    try:
        history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        if not history:
            print("Keine Aktionen in der Historie vorhanden.")
            return

        last_tx = history.pop()
        action = last_tx.get("action", "copy")
        items = last_tx.get("items", [])

        print("=" * 70)
        print(f"UNDO: RUECKGAENGIG MACHEN DER LETZTEN AKTION ({action.upper()})")
        print(f"Betroffene Dateien: {len(items)}")
        print("=" * 70)

        reverted = 0
        for it in items:
            src_orig = Path(it["src"])
            dest_sorted = Path(it["dest"])

            if action == "copy":
                if dest_sorted.exists():
                    dest_sorted.unlink()
                    print(f"[GELOESCHT (KOPIE)] {dest_sorted.name}")
                    reverted += 1
            elif action == "move":
                if dest_sorted.exists():
                    src_orig.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(dest_sorted), str(src_orig))
                    print(f"[ZURUECKVERSCHOBEN] {dest_sorted.name} -> {src_orig.parent}")
                    reverted += 1

        HISTORY_FILE.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nUndo abgeschlossen: {reverted} Datei(en) wurden zurueckgesetzt.")
        print("=" * 70)
    except Exception as e:
        print(f"Fehler beim Rueckgaengigmachen: {e}")


def choose_folder_gui() -> str:
    """Oeffnet ein Windows-Auswahlfenster fuer den Ordner."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(title="Waehlen Sie den zu sortierenden Quellordner")
        root.destroy()
        return selected
    except Exception:
        return ""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="7-Ordner-System Auto-Sortierer")
    parser.add_argument("--source", type=str, help="Quellordner oder Laufwerk (z. B. 'D:\\Alte_Dokumente' oder 'C:\\Users\\...\\Downloads')")
    parser.add_argument("--target", type=str, help="Zielordner fuer das 7-Ordner-System (Standard: lokales Verzeichnis)")
    parser.add_argument("--gui", action="store_true", help="Oeffnet ein Windows-Fenster zur Ordnerauswahl")
    parser.add_argument("--action", choices=["copy", "move"], default="copy", help="copy (sicher, behaelt Originale) oder move (verschiebt)")
    parser.add_argument("--execute", action="store_true", help="Fuehrt die Aktion aus (ohne Flag: nur Dry-Run Vorschau)")
    parser.add_argument("--dry-run", action="store_true", help="Erzwingt den Vorschau-Modus (Standard)")
    parser.add_argument("--undo", action="store_true", help="Macht die letzte Sortierung rueckgaengig")
    parser.add_argument("--replace", action="store_true", help="Ersetzt den Quellordner durch den sortierten _01 Ordner (sichert den alten Ordner als Backup)")
    parser.add_argument("--non-recursive", action="store_true", help="Durchsucht nur den obersten Ordner, keine Unterordner")

    args = parser.parse_args()

    if args.replace:
        if not args.source:
            print("Fehler: Bitte geben Sie mit '--source' den zu ersetzenden Quellordner an.")
            sys.exit(1)
        replace_original_with_target(Path(args.source), Path(args.target) if args.target else None)
        sys.exit(0)

    if args.undo:
        revert_last_transaction()
        sys.exit(0)

    # Quellordner ermitteln
    source = None
    if args.gui:
        print("Oeffne Windows-Ordnerauswahl...")
        chosen = choose_folder_gui()
        if chosen:
            source = Path(chosen)
        else:
            print("Kein Ordner ausgewaehlt.")
            sys.exit(0)
    elif args.source:
        source = Path(args.source)
    else:
        # Standard-Modus: Inbox pruefen
        source = INBOX_DIR
        # Wenn nur Inbox sortiert wird, ist Verschieben das normale Verhalten
        if args.action == "copy" and "--action" not in sys.argv:
            args.action = "move"

    run_organization(
        source_path=source,
        action=args.action,
        dry_run=not args.execute,
        recursive=not args.non_recursive,
        target_path=Path(args.target) if args.target else None
    )

