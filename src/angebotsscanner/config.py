"""Konfiguration: config.yaml (Märkte, Regeln, Bewertung) und vorrat.yaml.

Beide Dateien sind fachliche Vorgaben, keine Zugangsdaten. Die Pfade kommen
aus ``ANGEBOTSSCANNER_CONFIG`` bzw. ``ANGEBOTSSCANNER_VORRAT``.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from . import text

WOCHENTAGE = ("montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag")


class Standort(BaseModel):
    plz: str


class Einkauf(BaseModel):
    wochentag: str = "freitag"
    vorlauf_wochen: int = 2

    @property
    def wochentag_index(self) -> int:
        return WOCHENTAGE.index(self.wochentag.casefold())


class Markt(BaseModel):
    name: str
    quelle: str
    #: Bezeichnungen, unter denen die Quelle den Markt führt.
    namen: list[str] = Field(default_factory=list)


class QuelleEinstellung(BaseModel):
    aktiv: bool = True
    abstand_s: float = 2.0
    max_begriffe: int = 80


class Http(BaseModel):
    user_agent: str = "angebotsscanner/0.1 (privater Gebrauch)"
    timeout_s: float = 20.0
    cache_stunden: float = 6.0


class Relevanz(BaseModel):
    geplant: float = 1.0
    geplant_folgewoche: float = 0.9
    vorrat_knapp: float = 0.8
    vorrat: float = 0.6
    notvorrat: float = 0.4


class Bewertung(BaseModel):
    min_ersparnis_prozent: float = 20
    min_datenpunkte: int = 3
    relevanz: Relevanz = Field(default_factory=Relevanz)


class Regel(BaseModel):
    suche: str | None = None
    begriffe: list[str] = Field(default_factory=list)
    nicht: list[str] = Field(default_factory=list)


class Config(BaseModel):
    standort: Standort
    einkauf: Einkauf = Field(default_factory=Einkauf)
    maerkte: list[Markt]
    quellen: dict[str, QuelleEinstellung] = Field(default_factory=dict)
    http: Http = Field(default_factory=Http)
    apps: list[str] = Field(default_factory=list)
    bewertung: Bewertung = Field(default_factory=Bewertung)
    ausschluesse: list[str] = Field(default_factory=list)
    regeln: dict[str, Regel] = Field(default_factory=dict)
    #: Hier liegen Preishistorie und Cache.
    datenverzeichnis: Path = Path("daten")

    def quelle(self, name: str) -> QuelleEinstellung:
        return self.quellen.get(name, QuelleEinstellung())

    def maerkte_fuer(self, quelle: str) -> list[Markt]:
        return [m for m in self.maerkte if m.quelle == quelle]

    def regel(self, slug: str, name: str) -> Regel:
        """Regel über den Kurznamen, sonst über den normalisierten Namen."""
        if slug in self.regeln:
            return self.regeln[slug]
        schluessel = {text.normalisiere(k): v for k, v in self.regeln.items()}
        return schluessel.get(text.normalisiere(name), Regel())


class VorratArtikel(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    mindestbestand: float = 0
    zielmenge: float = 1
    einheit: str = "Packung"
    bestand: float | None = None
    haltbar: bool = True
    notvorrat: bool = False
    #: €/kg bzw. €/l – Vergleichswert, bis die Historie trägt.
    referenz_grundpreis: float | None = None

    @property
    def slug(self) -> str:
        return text.slug(self.name)


class Vorrat(BaseModel):
    artikel: list[VorratArtikel] = Field(default_factory=list)


def _yaml(pfad: Path) -> dict:
    with pfad.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def lade_config(pfad: Path | str | None = None) -> Config:
    pfad = Path(pfad or os.environ.get("ANGEBOTSSCANNER_CONFIG", "config.yaml"))
    config = Config.model_validate(_yaml(pfad))
    if not config.datenverzeichnis.is_absolute():
        config.datenverzeichnis = pfad.parent / config.datenverzeichnis
    return config


def lade_vorrat(pfad: Path | str | None = None) -> Vorrat:
    pfad = Path(pfad or os.environ.get("ANGEBOTSSCANNER_VORRAT", "vorrat.yaml"))
    if not pfad.is_file():
        return Vorrat()
    return Vorrat.model_validate(_yaml(pfad))
