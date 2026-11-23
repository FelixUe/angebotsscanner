"""Datenmodelle: Angebot, Bedarf, Vorschlag.

``Offer`` ist die normalisierte Form, die jeder Adapter liefert – egal wie
die Quelle aussieht. Mengen stehen in Basiseinheiten (g, ml, Stk), der
Grundpreis in €/kg, €/l oder €/Stk.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


class Offer(BaseModel):
    #: Eindeutig über alle Quellen, z. B. ``marktguru:25818207``.
    id: str
    quelle: str
    markt: str
    produkt: str
    marke: str | None = None
    beschreibung: str = ""
    #: Packungsinhalt in der Basiseinheit (g, ml, Stk).
    menge: float | None = None
    einheit: str | None = None
    preis: float
    #: €/kg, €/l oder €/Stk.
    grundpreis: float | None = None
    grundpreis_einheit: str | None = None
    regulaerer_preis: float | None = None
    gueltig_von: date
    gueltig_bis: date
    kategorie: str | None = None
    quelle_url: str | None = None
    #: Preis gilt nur mit dieser Kundenkarte/App (z. B. ``penny``).
    app_pflicht: str | None = None

    def gilt_am(self, tag: date) -> bool:
        return self.gueltig_von <= tag <= self.gueltig_bis

    @property
    def anzeigename(self) -> str:
        teile = [self.marke, self.produkt] if self.marke else [self.produkt]
        name = " ".join(t for t in teile if t)
        if self.menge and self.einheit:
            name += f" {formatiere_menge(self.menge, self.einheit)}"
        return name


class Art(StrEnum):
    GEPLANT = "geplant"
    VORRAT = "vorrat"
    NOTVORRAT = "notvorrat"


class Bedarf(BaseModel):
    """Etwas, das gebraucht wird: Zutat eines Gerichts oder Vorratsartikel."""

    slug: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    art: Art = Art.GEPLANT
    frisch: bool = False
    #: Wofür – Gericht mit Kochtag oder „Vorrat“.
    gericht: str | None = None
    #: 0 = kommender Einkauf, 1 = Woche danach.
    woche: int = 0
    menge: float | None = None
    einheit: str | None = None
    #: Nur Vorrat: liegt der Bestand unter dem Mindestbestand?
    knapp: bool = False
    referenz_grundpreis: float | None = None


class Vorschlag(BaseModel):
    """Ein Angebot, das auf den Einkaufszettel soll (Datenvertrag zum Ziel)."""

    angebot_id: str
    artikel: str
    slug: str
    menge: int
    markt: str
    preis: float
    grundpreis: float | None = None
    grundpreis_einheit: str | None = None
    regulaerer_preis: float | None = None
    ersparnis_prozent: float | None = None
    #: Woran die Ersparnis gemessen ist: historie, streichpreis, referenz.
    ersparnis_basis: str | None = None
    gueltig_von: date
    gueltig_bis: date
    grund: str
    art: Art
    quelle: str = "angebotsscanner"
    quelle_url: str | None = None
    score: float


class Unklar(BaseModel):
    """Treffer, den die Regeln nicht sicher entscheiden – nicht vorgeschlagen."""

    slug: str
    angebot_id: str
    artikel: str
    markt: str
    grund: str


class Ergebnis(BaseModel):
    einkaufstag: date
    vorschlaege: list[Vorschlag] = Field(default_factory=list)
    unklar: list[Unklar] = Field(default_factory=list)
    #: Quellen, die ausgefallen sind, mit Fehlermeldung.
    fehler: dict[str, str] = Field(default_factory=dict)


def formatiere_menge(menge: float, einheit: str) -> str:
    if einheit == "g" and menge >= 1000:
        return f"{menge / 1000:g} kg"
    if einheit == "ml" and menge >= 1000:
        return f"{menge / 1000:g} l"
    return f"{menge:g} {einheit}"
