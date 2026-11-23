# CLAUDE.md

Hinweise für Claude Code zur Arbeit in diesem Repo.

## Worum es geht

Eigener Dienst neben dem [Essensplaner](https://github.com/FelixUe/essensplaner):
holt jede Woche die Angebote ausgewählter Märkte, gleicht sie mit geplanten
Zutaten, regelmäßigen Vorräten und Notvorrat ab und schlägt gute Treffer für den
Einkaufszettel vor. Ziel ist die Einkaufsliste in **Mealie**; bis die Anbindung
steht, ist die Ausgabe JSON auf stdout.

Python 3.12, httpx, pydantic, rapidfuzz, SQLite. Keine Frameworks.

## Aufbau

```
src/angebotsscanner/
  config.py      config.yaml + vorrat.yaml (pydantic)
  modelle.py     Offer, Bedarf, Vorschlag, Ergebnis – der Datenvertrag
  http.py        Abrufer: User-Agent, Mindestabstand, SQLite-Cache
  quellen/       ein Modul je Quelle, Schnittstelle OfferSource.fetch(plz, suchbegriffe)
    marktguru.py EDEKA, Penny … (inoffiziell, Schlüssel von der Startseite)
    aldi_nord.py __NEXT_DATA__ der Angebotsseite, ein Abruf, Suchbegriffe egal
  text.py        Normalisierung, Wort-/Kompositum-Vergleich
  matching.py    Bedarf ↔ Angebot: sicher / unklar / nein
  historie.py    Preishistorie je Artikel (Slug) in SQLite
  bewertung.py   Ersparnis, Relevanz × Ersparnis × Haltbarkeit, Auswahl
  scanner.py     ein Lauf von Bedarf bis Ergebnis
  mealie.py      Bedarf aus der Mealie-Liste lesen, Angebote hineinschreiben
  cli.py         `angebotsscanner scan | angebote | mealie`
tests/fixtures/  gekürzte echte Antworten (KW 48/2026)
```

Datenfluss: Bedarf (Mealie-Liste „Wocheneinkauf“ oder JSON) + `vorrat.yaml`
→ `zusammenfassen` → Suchbegriffe → Quellen → Vorfilter (Einkaufstag,
App-Pflicht, Ausschlüsse, regionale Doppel) → Matching → Bewertung →
bestes Angebot je Bedarf → ein Vorschlag je Angebot.

## Befehle

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/angebotsscanner --config config.yaml scan --bedarf bedarf.json --vorrat vorrat.yaml
.venv/bin/angebotsscanner --config config.yaml angebote butter   # Rohsicht einer Quelle
set -a; . ./.env; set +a; .venv/bin/angebotsscanner --config config.yaml mealie --dry-run
```

## Konventionen

- **Deutsche Benennung** wie im Essensplaner. Commit-Betreffs ohne `feat:`/`fix:`.
- **Keine Live-Calls in Tests.** Quellen bekommen einen `httpx.MockTransport`
  (`tests/conftest.py`). Neue Fixtures kürzen (nur genutzte Felder, keine
  Tracking-URLs, keine echten Schlüssel).
- **Werte gehören in `config.yaml`,** nicht in den Code: Märkte, Ausschlüsse,
  Synonyme/`nicht`-Regeln, Schwellen. `config.yaml`, `vorrat.yaml`, `.env` und
  `daten/` sind gitignored – das Repo ist öffentlich, die PLZ steht nur lokal.
- **Respektvoll abrufen:** ehrlicher User-Agent, `abstand_s`, Cache. Verlangt eine
  Quelle Login, CAPTCHA oder sperrt sie (401/403 nach Neuholen der Schlüssel),
  wird **nicht** umgangen (kein Browser-Fingerprinting, keine UA-Rotation), sondern
  `QuelleFehler` geworfen und gemeldet.
- **Eine Quelle darf den Lauf nicht beenden.** `scanne` fängt Fehler je Quelle,
  loggt sie und schreibt sie nach `Ergebnis.fehler`.

## Fachliche Regeln (nicht aus dem Code ableitbar)

- Ein Angebot zählt nur, wenn es am **Einkaufstag** (Freitag) gilt.
- **Frischware** nur, wenn sie für genau diesen Einkauf geplant ist; Vorratszutaten
  der Folgewoche dürfen vorgezogen werden.
- **Auswahl je Bedarf nach Grundpreis**, nicht nach Score – Prospekt-Rabatte
  sind kein Qualitätsmaß. Der Score ordnet nur die Vorschläge.
- **Ersparnis:** Historie (Median früherer Wochen, ab `min_datenpunkte`, Tiefstpreis
  immer) → `referenz_grundpreis` aus vorrat.yaml → Streichpreis der Quelle.
- Geplantes kommt auch ohne Ersparnisangabe auf den Zettel (wird eh gekauft),
  aber nicht, wenn es laut Historie/Referenz teurer als üblich ist. Vorrat und
  Notvorrat nur ab `min_ersparnis_prozent`.
- **Matching:** Name + Kategorie → sicher; Kategorie heißt wie der Artikel → sicher;
  nur eins von beiden → unklar (wird nicht vorgeschlagen, steht in `unklar`).
  Ausreißer über `regeln.<artikel>.nicht` abfangen, nicht im Code.
- Aldi liefert keine brauchbare Warengruppe (`kategorie=None`), ein Namenstreffer
  gilt dort als sicher. Ausreißer wie Frischkäse für „Käse“ deshalb über
  `nicht`-Regeln abfangen. Aldi-Streichpreise sind meist UVPs.
- Nie vorschlagen: Blauschimmelkäse, Salbei, Fenchel (`ausschluesse`).

## Stand und nächste Schritte

1. ✅ MVP: Marktguru (EDEKA, Penny), Matching, Historie, CLI/JSON
2. ✅ Aldi-Nord-Adapter (`aldi-nord.de/angebote.html`, eingebettetes `__NEXT_DATA__`,
   bundesweit, Wellen Mo und Do, Non-Food über `ohne_gruppen`)
3. ✅ Essensplaner schreibt den vollständigen Wochenzettel in die Mealie-Liste
   „Wocheneinkauf“ (Repo `FelixUe/essensplaner`, Erweiterung von `mealie-sync`)
4. ✅ Scanner liest Bedarf aus dieser Liste und schreibt Angebote hinein
   (Notiz „🏷 …“, Metadaten in `extras`, abgehakte Einträge nie anfassen).
   Bindestrich-Komposita zählen nach dem Grundwort („Sahne-Joghurt“ ≠ Sahne);
   Fehltreffer aus Live-Läufen landen als `nicht`-Regel in `config.example.yaml`
5. Betrieb: Dockerfile, Compose im Netz `proxy`, So + Mi 19:00, Healthcheck
