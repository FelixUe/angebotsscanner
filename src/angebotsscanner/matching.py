"""Welches Angebot passt zu welchem Bedarf?

Stufe 1 sind Regeln: Der Artikelname, seine Aliasse und die Synonyme aus
``config.yaml`` müssen im Produktnamen vorkommen (als Wort oder Grundwort
eines Kompositums, mit Fuzzy-Toleranz), Wörter aus ``nicht`` schließen aus.
Die Kategorie der Quelle dient als zweite Meinung:

- Name **und** Kategorie passen (oder die Quelle hat keine Kategorie) → sicher
- die Kategorie heißt genau wie der Artikel (Barilla Collezione in *Nudeln*,
  Tchibo Feine Milde in *Kaffee*) → sicher; Ausreißer wie Kaffeepads fängt
  ``nicht`` ab
- sonst nur eines von beiden → unklar (z. B. „Butter Spekulatius“ in *Kekse*)
- keins → kein Treffer

Unklare Fälle werden nicht vorgeschlagen, sondern im Ergebnis aufgeführt.
Eine zweite Stufe (LLM) kann sie später entscheiden.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from . import text
from .config import Config
from .modelle import Bedarf, Offer


class Urteil(StrEnum):
    SICHER = "sicher"
    UNKLAR = "unklar"
    NEIN = "nein"


@dataclass(frozen=True, slots=True)
class Treffer:
    bedarf: Bedarf
    angebot: Offer
    urteil: Urteil
    grund: str


def begriffe(bedarf: Bedarf, config: Config) -> list[str]:
    regel = config.regel(bedarf.slug, bedarf.name)
    return list(dict.fromkeys([bedarf.name, *bedarf.aliases, *regel.begriffe]))


def suchbegriff(bedarf: Bedarf, config: Config) -> str:
    return config.regel(bedarf.slug, bedarf.name).suche or bedarf.name


def _grundwort(begriff: str) -> str:
    w = text.woerter(begriff)
    return w[-1] if w else ""


def ist_ausgeschlossen(angebot: Offer, ausschluesse: list[str]) -> bool:
    gesamt = " ".join(filter(None, [angebot.marke, angebot.produkt, angebot.beschreibung]))
    return any(text.enthaelt_teil(gesamt, a) for a in ausschluesse)


def beurteile(bedarf: Bedarf, angebot: Offer, config: Config) -> Treffer:
    regel = config.regel(bedarf.slug, bedarf.name)
    kopf = " ".join(filter(None, [angebot.marke, angebot.produkt]))
    for wort in regel.nicht:
        if text.enthaelt_teil(kopf, wort):
            return Treffer(bedarf, angebot, Urteil.NEIN, f"ausgeschlossen durch „{wort}“")

    liste = begriffe(bedarf, config)
    im_namen = next((b for b in liste if text.enthaelt_begriff(angebot.produkt, b)), None)
    kategorie = angebot.kategorie or ""
    # Zur Bestätigung reicht das Grundwort: „Haltbare Milch“ in Kategorie Milch
    in_kategorie = next((b for b in liste if text.enthaelt_begriff(kategorie, _grundwort(b))), None)
    # Kategorie heißt genau wie der Artikel (Nudeln, Kaffee, Käse): Das reicht allein
    kategorie_ist_artikel = any(
        [text.stamm(w) for w in text.woerter(kategorie)] == [text.stamm(w) for w in text.woerter(b)]
        for b in liste
    )

    if im_namen and (in_kategorie or not angebot.kategorie):
        return Treffer(bedarf, angebot, Urteil.SICHER, f"„{im_namen}“ im Namen")
    if kategorie_ist_artikel:
        return Treffer(bedarf, angebot, Urteil.SICHER, f"Kategorie {angebot.kategorie}")
    if im_namen:
        return Treffer(
            bedarf, angebot, Urteil.UNKLAR, f"„{im_namen}“ im Namen, Kategorie {angebot.kategorie}"
        )
    if any(text.enthaelt_begriff(kategorie, b) for b in liste):
        return Treffer(bedarf, angebot, Urteil.UNKLAR, f"nur Kategorie {angebot.kategorie}")
    return Treffer(bedarf, angebot, Urteil.NEIN, "kein Bezug")


def zuordnen(
    bedarfe: list[Bedarf], angebote: list[Offer], config: Config
) -> tuple[list[Treffer], list[Treffer]]:
    """Alle sicheren und unklaren Treffer zwischen Bedarf und Angeboten."""
    sicher: list[Treffer] = []
    unklar: list[Treffer] = []
    for bedarf in bedarfe:
        for angebot in angebote:
            t = beurteile(bedarf, angebot, config)
            if t.urteil is Urteil.SICHER:
                sicher.append(t)
            elif t.urteil is Urteil.UNKLAR:
                unklar.append(t)
    return sicher, unklar
