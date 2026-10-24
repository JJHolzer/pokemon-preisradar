# Pokémon Preisradar

Sammelmappe für Pokémon-Karten und versiegelte Produkte mit täglich aktualisierten Cardmarket-Preisen.

**App:** https://jjholzer.github.io/pokemon-preisradar/

## So funktioniert es
- **Täglicher Abruf** (`.github/workflows/update.yml`, ca. 06:20 Uhr): lädt den öffentlichen Cardmarket Price Guide, verknüpft ihn mit deutschen Kartennamen, Nummern und Bildern von TCGdex, schreibt den Preisverlauf je Produkt fort und veröffentlicht die Seite auf GitHub Pages.
- **Auslöser:** Der GitHub-Zeitplan im Workflow hat sich als unzuverlässig erwiesen. Der Abruf wird deshalb zweimal täglich (06:30 und 12:30 Uhr) von einem Cronjob auf Jonas' Home-Server per `workflow_dispatch` gestartet (Skript `~/agents/preisradar-trigger.sh`, Fine-grained Token nur für dieses Repo).
- **Verlauf** liegt im Branch `daten` (wird täglich neu geschrieben, wächst im Git nicht an).
- **Mappe, Einkaufspreise, Fotos** bleiben auf dem Gerät des Nutzers (IndexedDB). Sicherung als Datei über „Import & Sicherung“.
- **Kartenerkennung** per Foto läuft im Browser (Tesseract.js), Fotos werden nicht hochgeladen.

## Dateien
- `web/` – die App (eine HTML-Datei, kein Build)
- `scripts/update.py` – Abruf und Aufbau von Katalog und Verlauf
- `data/tcgdex-de.json` – Startbestand der TCGdex-Zuordnung (wird im Branch `daten` fortgeführt)

Preise: Cardmarket Price Guide. Kartendaten und Bilder: TCGdex. Kein offizielles Produkt von Cardmarket, Nintendo oder The Pokémon Company.
