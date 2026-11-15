# Das 7-Ordner-System (Privat)

Dieses Verzeichnis ist nach der 7-Ordner-Methode strukturiert, um private Unterlagen übersichtlich, schnell auffindbar und automatisiert zu verwalten.

## Ordnerübersicht

| Ordner | Unterordner | Typische Dokumente |
|---|---|---|
| **01_Eingang_Inbox** | *(keine)* | Alle neuen Downloads, Scans, Rechnungen vor der Sortierung |
| **02_Finanzen_Steuern** | `Bank_Kontoauszuege`<br>`Steuererklaerung`<br>`Geldanlage_Vorsorge` | Kontoauszüge, Kreditkarten, Steuerbescheide, ELSTER, Depots, Bausparen |
| **03_Vertraege_Versicherungen** | `Versicherungen`<br>`Vertraege_Mobilfunk_Internet`<br>`Fahrzeuge_Kfz` | Haftpflicht, Hausrat, Mobilfunk-, Internetverträge, TÜV, Kfz-Schein |
| **04_Beruf_Bildung** | `Gehaltsabrechnungen`<br>`Arbeitsvertraege`<br>`Zeugnisse_Zertifikate` | Lohnabrechnungen, Arbeitsverträge, Kündigungen, Arbeitszeugnisse, Zertifikate |
| **05_Gesundheit_Familie** | `Krankenkasse`<br>`Aerzte_Befunde`<br>`Familienurkunden` | TK/AOK-Schreiben, Erstattungen, Arztbefunde, Impfpass, Geburtsurkunde |
| **06_Wohnen_Eigentum** | `Miete_Nebenkosten`<br>`Geraete_Garantien_Belege`<br>`Handwerker_Renovierung` | Mietvertrag, Nebenkosten, Strom/Gas, Amazon/Kaufbelege, Handwerkerrechnungen |
| **07_Archiv_Historie** | `Ausweise_Reisepaesse`<br>`Abgeschlossenes` | Kopien von Pässen/Führerschein, beendete Verträge, alte Projekte |

---

## Dateinamen-Konvention

Alle Dokumente sollten folgendem Standard folgen:
`YYYY-MM-DD_Absender_Kategorie_Dokumenttitel.pdf`

*Beispiele:*
- `2024-01-15_Telekom_Rechnung_Mobilfunk.pdf`
- `2024-02-01_Allianz_Versicherungsschein_Haftpflicht.pdf`
- `2024-03-31_Arbeitgeber_Gehalt_Maerz.pdf`

---

## Programme & Ausführung

In diesem Verzeichnis stehen Ihnen nun folgende Werkzeuge zur Verfügung:

1. **`7-Ordner-System.exe`** (Eigenständige Windows-Anwendung)
   - Doppelklick genügt – läuft direkt auf jedem Windows-PC ohne Installation oder Python-Befehle!
   - Enthält die grafische Oberfläche mit Ordnerwahl, Live-Vorschau, Back-Knopf und Undo-Funktion.

2. **`app.pyw`** (Python GUI)
   - Die Quellcode-Version der grafischen Oberfläche.

3. **`auto_sort.py`** (Kommandozeilen-Werkzeug)
   - Für Skripte, Automatisierungen oder schnelle Terminal-Befehle.
   ```bash
   # Vorschau:
   python auto_sort.py --source "C:\Users\Sortier_Test" --dry-run
   # Ausführen:
   python auto_sort.py --source "C:\Users\Sortier_Test" --execute
   # Rückgängig machen:
   python auto_sort.py --undo
   ```

