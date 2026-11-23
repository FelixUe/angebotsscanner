# Angebotsscanner

Holt jede Woche die Prospektangebote deiner Supermärkte und gleicht sie mit drei
Listen ab:

1. Zutaten der geplanten Gerichte (z. B. aus dem [Essensplaner](https://github.com/FelixUe/essensplaner))
2. regelmäßige Vorräte (Nudeln, Reis, Passata, Kaffee …)
3. Notvorrat

Heraus kommt eine Vorschlagsliste: Was lohnt sich diesen Einkauf, wo, zu welchem
Preis, wie viel gespart, bis wann gültig. Ob etwas wirklich günstig ist, misst der
Scanner an seiner eigenen Preishistorie, nicht an der Rabattangabe im Prospekt.

> **Status:** MVP. Quellen sind Marktguru (EDEKA, Penny, REWE, Kaufland …) und
> die Angebotsseite von Aldi Nord. Die Anbindung an Mealie folgt.

## Quellen

| Quelle | Märkte | Art |
|---|---|---|
| Marktguru | EDEKA, Penny, Netto, REWE, Kaufland, Lidl … (regional über die PLZ) | **inoffiziell**, standardmäßig aus |
| Aldi Nord | Aldi Nord (bundesweit gleiche Preise) | öffentliche Angebotsseite, ein Abruf je Lauf |

Marktguru hat keine öffentliche API. Der Adapter fragt die Schnittstelle ab, die
auch die Webseite nutzt, mit ehrlichem User-Agent, 2 s Abstand und 6 h Cache.
Das ist nur für den privaten Gebrauch gedacht und geschieht auf eigenes Risiko.
Aldi Nord fehlt bei Marktguru. Der Adapter liest dort die Daten, die in
`aldi-nord.de/angebote.html` eingebettet sind (laut robots.txt erlaubt). Die
Streichpreise sind meist UVPs, deshalb geht die eigene Historie vor.

Sperrt eine Quelle den Zugriff, wird das gemeldet und nicht umgangen.

## Setup

1. Python 3.12 und [uv](https://docs.astral.sh/uv/) installieren.
2. `git clone https://github.com/FelixUe/angebotsscanner && cd angebotsscanner`
3. `uv venv --python 3.12 && uv pip install -e .`
4. `cp config.example.yaml config.yaml`, dann PLZ und Märkte eintragen.
   Unter `quellen.marktguru.aktiv: true` schaltest du die Quelle bewusst ein.
5. `cp vorrat.example.yaml vorrat.yaml` und an deine Vorräte anpassen.
   `referenz_grundpreis` hilft, solange die Historie noch leer ist.
6. Bedarf als JSON bereitstellen, Format siehe `bedarf.example.json`. Fürs Erste
   reicht die Beispieldatei.
7. Probelauf:
   `.venv/bin/angebotsscanner --config config.yaml scan --bedarf bedarf.example.json --vorrat vorrat.yaml`
8. Rohdaten einer Quelle ansehen: `.venv/bin/angebotsscanner --config config.yaml angebote butter`
9. Preishistorie und Cache landen in `daten/`. Je öfter der Scanner läuft, desto
   besser wird die Bewertung.
10. Mit Mealie: `cp .env.example .env`, URL und API-Token eintragen, dann
    `set -a; . ./.env; set +a; .venv/bin/angebotsscanner --config config.yaml mealie --dry-run`
    und ohne `--dry-run` schreiben.

## Mealie

`angebotsscanner mealie` liest den Wochenzettel, den der Essensplaner in die
Liste „Wocheneinkauf“ schreibt, und hängt passende Angebote an die Zeile an:
`… · 🏷 2× Speisezwiebeln 2 kg: EDEKA 1,99 € bis Sa 28.11.`. Alles vor 🏷
gehört dem Essensplaner, alles danach dem Scanner; Markt, Preis und Gültigkeit
stehen zusätzlich in `extras.angebotsscanner_*`. Vorrat ohne eigene Zeile
bekommt eine neue unter dem Label „Angebote“. Abgehakte Zeilen bleiben
unangetastet, abgelaufene Hinweise verschwinden, ein zweiter Lauf ändert nichts.

## Ausgabe

```json
{
  "einkaufstag": "2026-11-27",
  "vorschlaege": [
    { "angebot_id": "marktguru:25…", "artikel": "Gut & Günstig Pasta 500 g",
      "slug": "spaghetti", "menge": 3, "markt": "EDEKA", "preis": 0.59,
      "grundpreis": 1.18, "grundpreis_einheit": "kg", "ersparnis_prozent": 41.0,
      "ersparnis_basis": "referenz", "gueltig_von": "2026-11-23",
      "gueltig_bis": "2026-11-28", "grund": "Spaghetti Bolognese (Mo) + Vorrat",
      "art": "geplant", "quelle": "angebotsscanner", "score": 1.0 }
  ],
  "unklar": [],
  "fehler": {}
}
```

## Betrieb im Container

`cp .env.example .env`, PLZ und Mealie-Token eintragen, dann
`docker compose up -d --build`. Der Container läuft sonntags und mittwochs um
19:00 (`betrieb.laeufe` in der Konfiguration) und braucht keinen Port, nur
Zugang zu Mealie. `docker compose exec angebotsscanner angebotsscanner gesund`
zeigt den letzten Lauf.

## Entwicklung

```bash
uv pip install -e ".[dev]"
.venv/bin/pytest -q          # keine Live-Calls, nur Fixtures
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Architektur und Konventionen: [CLAUDE.md](CLAUDE.md).
