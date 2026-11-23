"""Angebotsquellen – ein Modul je Quelle, alle hinter derselben Schnittstelle.

Eine Quelle liefert normalisierte ``Offer``-Objekte für die Märkte, die in
der Konfiguration auf sie zeigen. Suchbasierte Quellen (Marktguru) nutzen
die Suchbegriffe, prospektbasierte (Aldi) ignorieren sie.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ..modelle import Offer


class QuelleFehler(Exception):
    """Die Quelle ist nicht erreichbar oder hat ihr Format geändert."""


class OfferSource(Protocol):
    name: str

    def fetch(self, plz: str, suchbegriffe: Sequence[str] = ()) -> list[Offer]: ...
