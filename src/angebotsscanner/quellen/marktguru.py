"""Marktguru: Prospektangebote aller großen Händler, regional über die PLZ.

**Inoffiziell.** marktguru.de hat keine öffentliche API. Die Webseite selbst
fragt ``api.marktguru.de`` mit zwei Schlüsseln ab, die im HTML der
Startseite stehen; dieser Adapter liest sie genauso aus (Cache 6 h) und
sucht dann je Begriff. Nur für den privaten Gebrauch, niedrige Frequenz,
ehrlicher User-Agent. Ändert Marktguru das Format, wirft der Adapter
``QuelleFehler`` – die übrigen Quellen laufen weiter.

Aldi führt Marktguru nicht; dafür gibt es einen eigenen Adapter.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

from ..config import Markt
from ..http import Abrufer
from ..modelle import Offer
from . import QuelleFehler

log = logging.getLogger(__name__)

STARTSEITE = "https://www.marktguru.de/"
SUCHE = "https://api.marktguru.de/api/v1/offers/search"
ANGEBOT_URL = "https://www.marktguru.de/offers/{id}"
BERLIN = ZoneInfo("Europe/Berlin")

_SCHLUESSEL = re.compile(r'"apiKey"\s*:\s*"([^"]+)".*?"clientKey"\s*:\s*"([^"]+)"', re.S)
#: „HINWEIS: MIT EDEKA APP 6.49 €“ – günstigerer Preis mit Kundenapp.
_APP_PREIS = re.compile(r"mit\s+([a-zäöü]+)\s+app\s+(\d+[.,]\d{1,2})", re.I)
#: Basiseinheit und Faktor je Marktguru-Einheit.
_EINHEITEN = {"kg": ("g", 1000.0), "l": ("ml", 1000.0), "stk": ("Stk", 1.0)}


def _datum(wert: str) -> date:
    """UTC-Zeitstempel → Kalendertag in Berlin (23:00Z ist schon der nächste Tag)."""
    return datetime.fromisoformat(wert.replace("Z", "+00:00")).astimezone(BERLIN).date()


class Marktguru:
    name = "marktguru"

    def __init__(
        self,
        abrufer: Abrufer,
        maerkte: Sequence[Markt],
        apps: Sequence[str] = (),
        max_begriffe: int = 80,
    ) -> None:
        self._abrufer = abrufer
        self._namen = {n.casefold(): m.name for m in maerkte for n in (m.namen or [m.name])}
        self._apps = {a.casefold() for a in apps}
        self._max = max_begriffe
        self._schluessel: tuple[str, str] | None = None

    # ------------------------------------------------------------ Schlüssel

    def _hole_schluessel(self, neu: bool = False) -> tuple[str, str]:
        if self._schluessel and not neu:
            return self._schluessel
        if neu:
            self._abrufer.verwerfe(STARTSEITE)
        html = self._abrufer.get(STARTSEITE)
        treffer = _SCHLUESSEL.search(html)
        if not treffer:
            raise QuelleFehler("Marktguru: Schlüssel nicht auf der Startseite gefunden")
        self._schluessel = (treffer.group(1), treffer.group(2))
        return self._schluessel

    def _suche(self, begriff: str, plz: str) -> dict:
        params = {"as": "web", "q": begriff, "zipCode": plz, "limit": "100", "offset": "0"}
        for versuch in (0, 1):
            api, client = self._hole_schluessel(neu=versuch == 1)
            try:
                roh = self._abrufer.get(
                    SUCHE, params=params, headers={"x-apikey": api, "x-clientkey": client}
                )
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in (401, 403):
                    if versuch == 0:
                        log.info("Marktguru lehnt die Schlüssel ab – hole sie neu")
                        continue
                    raise QuelleFehler(
                        f"Marktguru: Schlüssel werden nicht angenommen (HTTP {status}) – "
                        "nicht umgehen, Quelle abschalten oder Alternative nutzen"
                    ) from exc
                raise QuelleFehler(f"Marktguru: HTTP {status}") from exc
            except httpx.HTTPError as exc:
                raise QuelleFehler(f"Marktguru nicht erreichbar: {exc}") from exc
            try:
                return json.loads(roh)
            except json.JSONDecodeError as exc:
                raise QuelleFehler("Marktguru: Antwort ist kein JSON") from exc
        raise QuelleFehler("Marktguru: Schlüssel werden nicht angenommen")

    # ------------------------------------------------------------ Abruf

    def fetch(self, plz: str, suchbegriffe: Sequence[str] = ()) -> list[Offer]:
        begriffe = list(dict.fromkeys(b.strip() for b in suchbegriffe if b.strip()))
        if len(begriffe) > self._max:
            log.warning("Marktguru: %d Begriffe, nur die ersten %d", len(begriffe), self._max)
            begriffe = begriffe[: self._max]
        angebote: dict[str, Offer] = {}
        for begriff in begriffe:
            antwort = self._suche(begriff, plz)
            ergebnisse = antwort.get("results")
            if not isinstance(ergebnisse, list):
                raise QuelleFehler("Marktguru: Feld 'results' fehlt – Format geändert?")
            for roh in ergebnisse:
                angebot = self.normalisiere(roh)
                if angebot is not None:
                    angebote.setdefault(angebot.id, angebot)
        log.info("Marktguru: %d Begriffe, %d Angebote", len(begriffe), len(angebote))
        return list(angebote.values())

    def normalisiere(self, roh: dict) -> Offer | None:
        """Ein Marktguru-Ergebnis → ``Offer``; ``None`` für fremde Märkte."""
        markt = next(
            (
                self._namen[a["name"].casefold()]
                for a in roh.get("advertisers") or []
                if a.get("name", "").casefold() in self._namen
            ),
            None,
        )
        if markt is None:
            return None
        try:
            preis = float(roh["price"])
            zeitraum = (roh.get("validityDates") or [])[0]
            von, bis = _datum(zeitraum["from"]), _datum(zeitraum["to"])
        except (KeyError, IndexError, TypeError, ValueError):
            log.debug("Marktguru: Angebot %s ohne Preis/Gültigkeit übersprungen", roh.get("id"))
            return None

        beschreibung = roh.get("description") or ""
        grundpreis = roh.get("referencePrice")
        einheit_roh = ((roh.get("unit") or {}).get("shortName") or "").casefold()
        basis, faktor = _EINHEITEN.get(einheit_roh, (None, 1.0))
        menge = None
        if basis and roh.get("volume"):
            menge = round(float(roh["volume"]) * float(roh.get("quantity") or 1) * faktor, 1)

        app_pflicht = None
        if roh.get("requiresLoyalityMembership"):
            app_pflicht = markt.casefold()
        elif (app := _APP_PREIS.search(beschreibung)) and app.group(1).casefold() in self._apps:
            app_preis = float(app.group(2).replace(",", "."))
            if 0 < app_preis < preis:
                if grundpreis:
                    grundpreis = round(grundpreis * app_preis / preis, 2)
                preis = app_preis
                app_pflicht = app.group(1).casefold()

        kategorien = roh.get("categories") or []
        return Offer(
            id=f"marktguru:{roh['id']}",
            quelle=self.name,
            markt=markt,
            produkt=((roh.get("product") or {}).get("name") or "").strip(),
            marke=((roh.get("brand") or {}).get("name") or None),
            beschreibung=beschreibung,
            menge=menge,
            einheit=basis,
            preis=preis,
            grundpreis=float(grundpreis) if grundpreis else None,
            grundpreis_einheit=einheit_roh or None,
            regulaerer_preis=roh.get("oldPrice") or None,
            gueltig_von=von,
            gueltig_bis=bis,
            kategorie=kategorien[0]["name"] if kategorien else None,
            quelle_url=ANGEBOT_URL.format(id=roh["id"]),
            app_pflicht=app_pflicht,
        )
