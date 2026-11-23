"""Preishistorie in SQLite: Was hat ein Artikel bisher im Angebot gekostet?

Jeder sichere Treffer wird mit Grundpreis gespeichert, je normalisiertem
Artikel (Slug). „Gutes Angebot“ heißt dann: Grundpreis unter dem Median
bzw. am Tiefstpreis der **früheren** Beobachtungen – gemessen an echten
Daten statt an der Rabattangabe im Prospekt.
"""

from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .modelle import Offer

_SCHEMA = """
CREATE TABLE IF NOT EXISTS beobachtung (
    angebot_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    markt TEXT NOT NULL,
    produkt TEXT NOT NULL,
    preis REAL NOT NULL,
    grundpreis REAL,
    grundpreis_einheit TEXT,
    gueltig_von TEXT NOT NULL,
    gueltig_bis TEXT NOT NULL,
    erfasst_am TEXT NOT NULL,
    PRIMARY KEY (angebot_id, slug)
);
CREATE INDEX IF NOT EXISTS beobachtung_slug ON beobachtung (slug, grundpreis_einheit);
"""


@dataclass(frozen=True, slots=True)
class Statistik:
    anzahl: int
    median: float
    tiefst: float


class Historie:
    def __init__(self, pfad: Path) -> None:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(pfad)
        self._db.executescript(_SCHEMA)

    def erfasse(self, slug: str, angebot: Offer, heute: date) -> None:
        """Idempotent: dasselbe Angebot zählt je Artikel nur einmal."""
        self._db.execute(
            "INSERT OR IGNORE INTO beobachtung VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                angebot.id,
                slug,
                angebot.markt,
                angebot.anzeigename,
                angebot.preis,
                angebot.grundpreis,
                angebot.grundpreis_einheit,
                angebot.gueltig_von.isoformat(),
                angebot.gueltig_bis.isoformat(),
                heute.isoformat(),
            ),
        )

    def speichern(self) -> None:
        self._db.commit()

    def statistik(self, slug: str, einheit: str | None, vor: date) -> Statistik | None:
        """Grundpreise früherer Angebote (abgelaufen vor ``vor``)."""
        if einheit is None:
            return None
        werte = [
            z[0]
            for z in self._db.execute(
                "SELECT grundpreis FROM beobachtung WHERE slug = ? AND grundpreis_einheit = ? "
                "AND grundpreis IS NOT NULL AND gueltig_bis < ?",
                (slug, einheit, vor.isoformat()),
            )
        ]
        if not werte:
            return None
        return Statistik(len(werte), statistics.median(werte), min(werte))

    def schliessen(self) -> None:
        self._db.close()
