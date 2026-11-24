"""Bewertung: Relevanz × Ersparnis × Haltbarkeit, und was davon auf den Zettel darf.

Ersparnis wird in dieser Reihenfolge gemessen:

1. **historie** – Grundpreis gegen den Median früherer Angebote (sobald
   ``min_datenpunkte`` erreicht sind); am Tiefstpreis zählt immer
2. **referenz** – Grundpreis gegen ``referenz_grundpreis`` aus vorrat.yaml
   (ein selbst gesetzter Kilopreis ist verlässlicher als der Prospekt)
3. **streichpreis** – Preis gegen den regulären Preis der Quelle

Geplante Zutaten werden ohnehin gekauft: Ihr bestes Angebot kommt auf den
Zettel, solange es nicht teurer als üblich ist. Vorrat und Notvorrat nur
bei echter Ersparnis. Frischware nur, wenn sie für diesen Einkauf geplant ist.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from .config import Config
from .historie import Historie
from .matching import Treffer
from .modelle import Art, Bedarf, Offer

_BASIS = {"g": ("g", 1.0), "kg": ("g", 1000.0), "ml": ("ml", 1.0), "l": ("ml", 1000.0)}
_BASIS |= {"stk": ("Stk", 1.0), "stück": ("Stk", 1.0)}
MAX_PACKUNGEN = 10


@dataclass(frozen=True, slots=True)
class Ersparnis:
    prozent: float | None
    basis: str | None
    tiefstpreis: bool = False


@dataclass(frozen=True, slots=True)
class Bewertet:
    treffer: Treffer
    ersparnis: Ersparnis
    score: float
    menge: int


def relevanz(bedarf: Bedarf, config: Config) -> float:
    r = config.bewertung.relevanz
    if bedarf.art is Art.GEPLANT:
        return r.geplant if bedarf.woche == 0 else r.geplant_folgewoche
    if bedarf.art is Art.NOTVORRAT:
        return r.notvorrat
    return r.vorrat_knapp if bedarf.knapp else r.vorrat


def haltbarkeit(bedarf: Bedarf) -> float:
    """Frischware zählt nur, wenn sie für den kommenden Einkauf geplant ist."""
    if bedarf.frisch and (bedarf.art is not Art.GEPLANT or bedarf.woche > 0):
        return 0.0
    return 1.0


def ersparnis(
    bedarf: Bedarf, angebot: Offer, historie: Historie, config: Config, vor: date
) -> Ersparnis:
    gp = angebot.grundpreis
    stat = historie.statistik(bedarf.slug, angebot.grundpreis_einheit, vor) if gp else None
    if stat and gp:
        tiefst = gp <= stat.tiefst
        if stat.anzahl >= config.bewertung.min_datenpunkte or tiefst:
            return Ersparnis(round((1 - gp / stat.median) * 100, 1), "historie", tiefst)
    if bedarf.referenz_grundpreis and gp and angebot.grundpreis_einheit in ("kg", "l"):
        return Ersparnis(round((1 - gp / bedarf.referenz_grundpreis) * 100, 1), "referenz")
    if angebot.regulaerer_preis and angebot.regulaerer_preis > angebot.preis:
        return Ersparnis(
            round((1 - angebot.preis / angebot.regulaerer_preis) * 100, 1), "streichpreis"
        )
    return Ersparnis(None, None)


def packungen(bedarf: Bedarf, angebot: Offer) -> int:
    """Wie viele Packungen des Angebots decken den Bedarf?"""
    if bedarf.menge is None:
        return 1
    einheit = (bedarf.einheit or "").casefold()
    if einheit in _BASIS and angebot.menge and angebot.einheit:
        basis, faktor = _BASIS[einheit]
        if basis.casefold() == angebot.einheit.casefold():
            return max(1, min(MAX_PACKUNGEN, math.ceil(bedarf.menge * faktor / angebot.menge)))
        return 1
    if einheit in ("stk", "stück"):
        # Angebot ohne Stückzahl: „Eier (10 Stück)“ ist eine Packung, nicht zehn
        return 1
    # Packung, Dose, Glas …: die Menge ist schon eine Packungszahl
    return max(1, min(MAX_PACKUNGEN, math.ceil(bedarf.menge)))


def bewerte(treffer: Treffer, historie: Historie, config: Config, vor: date) -> Bewertet | None:
    """Bewertet einen Treffer; ``None``, wenn er nicht auf den Zettel gehört."""
    b, a = treffer.bedarf, treffer.angebot
    h = haltbarkeit(b)
    if h == 0:
        return None
    e = ersparnis(b, a, historie, config, vor)
    if b.art is Art.GEPLANT:
        if e.prozent is not None and e.prozent < 0 and not e.tiefstpreis:
            return None
    elif not e.tiefstpreis and (
        e.prozent is None or e.prozent < config.bewertung.min_ersparnis_prozent
    ):
        return None
    faktor = 1 + max(e.prozent or 0.0, 0.0) / 100
    return Bewertet(treffer, e, round(relevanz(b, config) * faktor * h, 3), packungen(b, a))


def bestes_je_bedarf(bewertete: list[Bewertet]) -> list[Bewertet]:
    """Je Bedarf das Angebot mit dem niedrigsten Grundpreis.

    Bewusst nicht der höchste Score: Der hängt an der Ersparnis, und eine
    Rabattangabe im Prospekt macht teure Mini-Tomaten nicht günstiger als
    Rispentomaten zum halben Kilopreis. Der Score ordnet nur die Vorschläge.
    """
    beste: dict[str, Bewertet] = {}
    for x in bewertete:
        schluessel = x.treffer.bedarf.slug
        alt = beste.get(schluessel)
        if alt is None or _rang(x) < _rang(alt):
            beste[schluessel] = x
    return list(beste.values())


def _rang(x: Bewertet) -> tuple[float, float, float]:
    a = x.treffer.angebot
    return (a.grundpreis or math.inf, a.preis, -x.score)
