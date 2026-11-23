"""Aldi Nord: die Angebotsseite der Woche, ein Abruf je Lauf.

``aldi-nord.de/angebote.html`` ist eine Next.js-Seite. Im eingebetteten
``__NEXT_DATA__`` steckt unter ``props.pageProps.apiData`` ein weiterer
JSON-String mit allen Angeboten der Woche (``algoliaDataMap``) und ihrer
Einteilung in Aktionen (``categories``: „Aktion Mo.“, „Aktion Do.“ mit
Gruppen wie „Haltbare Produkte“ oder „Spielzeug“). Die Preise gelten
bundesweit im Aldi-Nord-Gebiet, eine PLZ braucht es nicht.

Ein Abruf liefert rund 300 Artikel, die Suchbegriffe werden nicht gebraucht.
Die Seite schaltet sonntags auf die neue Woche um – der Lauf am Sonntagabend
sieht also schon die Angebote ab Montag. robots.txt erlaubt die Seite.

Der Streichpreis ist bei Aldi meist eine UVP des Herstellers, kein früherer
Aldi-Preis. Er landet trotzdem in ``regulaerer_preis``; die Bewertung zieht
Historie und Referenzpreis ohnehin vor.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

from ..http import Abrufer
from ..modelle import Offer
from . import QuelleFehler

log = logging.getLogger(__name__)

ANGEBOTE = "https://www.aldi-nord.de/angebote.html"
PRODUKT_URL = "https://www.aldi-nord.de/produkt/{slug}.html"
BERLIN = ZoneInfo("Europe/Berlin")

_NEXT_DATA = re.compile(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
#: „2x125-g-Packung“, „0,5-L-Dose“, „2.000-g-Packung“, „z. B. 100 g“
_MENGE = re.compile(
    r"(?:(\d+)\s*x\s*)?(\d{1,3}(?:\.\d{3})+|\d+(?:,\d+)?)\s*-?\s*(kg|g|ml|l)\b", re.I
)
_EINHEIT = {"g": ("g", 1.0), "kg": ("g", 1000.0), "ml": ("ml", 1.0), "l": ("ml", 1000.0)}
_GRUNDPREIS_EINHEIT = {"kg": "kg", "liter": "l", "l": "l", "stück": "stk"}


def _tag(zeitstempel: int) -> date:
    return datetime.fromtimestamp(zeitstempel, BERLIN).date()


def parse_menge(verkaufseinheit: str | None) -> tuple[float, str] | None:
    """Packungsinhalt aus der Verkaufseinheit, in g bzw. ml."""
    if not verkaufseinheit:
        return None
    if verkaufseinheit.strip().casefold().startswith("kg-preis"):
        return 1000.0, "g"  # lose Ware, Preis je kg
    treffer = _MENGE.search(verkaufseinheit)
    if not treffer:
        return None
    anzahl, wert, einheit = treffer.groups()
    zahl = (
        float(wert.replace(".", "").replace(",", "."))
        if "." in wert
        else float(wert.replace(",", "."))
    )
    basis, faktor = _EINHEIT[einheit.casefold()]
    return round(zahl * faktor * int(anzahl or 1), 1), basis


def _marke(roh: str | None) -> str | None:
    if not roh or not roh.strip():
        return None
    roh = " ".join(roh.split())
    return roh.title() if roh.isupper() else roh


class AldiNord:
    name = "aldi_nord"

    def __init__(
        self, abrufer: Abrufer, markt: str = "Aldi Nord", ohne_gruppen: Sequence[str] = ()
    ) -> None:
        self._abrufer = abrufer
        self._markt = markt
        self._ohne = {g.casefold() for g in ohne_gruppen}

    def _seitendaten(self) -> dict:
        try:
            html = self._abrufer.get(ANGEBOTE)
        except httpx.HTTPStatusError as exc:
            raise QuelleFehler(f"Aldi Nord: HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise QuelleFehler(f"Aldi Nord nicht erreichbar: {exc}") from exc
        treffer = _NEXT_DATA.search(html)
        if not treffer:
            raise QuelleFehler("Aldi Nord: kein __NEXT_DATA__ – Seite umgebaut?")
        try:
            seite = json.loads(treffer.group(1))
            api = json.loads(seite["props"]["pageProps"]["apiData"])
            return next(e[1]["res"] for e in api if e[0] == "OFFER_GET")
        except (json.JSONDecodeError, KeyError, TypeError, StopIteration) as exc:
            raise QuelleFehler(f"Aldi Nord: Seitendaten unerwartet ({exc!r})") from exc

    def fetch(self, plz: str = "", suchbegriffe: Sequence[str] = ()) -> list[Offer]:
        res = self._seitendaten()
        artikel = res.get("algoliaDataMap")
        if not isinstance(artikel, dict):
            raise QuelleFehler("Aldi Nord: 'algoliaDataMap' fehlt – Format geändert?")
        gruppe: dict[str, str] = {}
        for aktion in res.get("categories") or []:
            for inhalt in aktion.get("content") or []:
                for pid in inhalt.get("productIds") or []:
                    gruppe.setdefault(str(pid), inhalt.get("title") or "")
        angebote = []
        for pid, roh in artikel.items():
            if gruppe.get(pid, "").casefold() in self._ohne:
                continue
            angebot = self.normalisiere(roh, gruppe.get(pid))
            if angebot is not None:
                angebote.append(angebot)
        log.info("Aldi Nord: %d Artikel, %d Angebote nach Filter", len(artikel), len(angebote))
        return angebote

    def normalisiere(self, roh: dict, gruppe: str | None = None) -> Offer | None:
        preis_roh = roh.get("currentPrice") or {}
        try:
            preis = float(preis_roh["priceValue"])
            von, bis = _tag(preis_roh["validFrom"]), _tag(preis_roh["validUntil"])
        except (KeyError, TypeError, ValueError):
            log.debug("Aldi Nord: Artikel %s ohne Preis/Gültigkeit", roh.get("objectID"))
            return None
        if roh.get("isAvailable") is False:
            return None

        menge = parse_menge(roh.get("salesUnit"))
        grundpreis = grundpreis_einheit = None
        basis = (preis_roh.get("basePrice") or [None])[0]
        if basis and basis.get("basePriceValue"):
            skala = (basis.get("basePriceScale") or "").split("(")[0].strip().casefold()
            if skala in _GRUNDPREIS_EINHEIT:
                grundpreis = float(basis["basePriceValue"])
                grundpreis_einheit = _GRUNDPREIS_EINHEIT[skala]
        if grundpreis is None and menge:
            grundpreis = round(preis / menge[0] * 1000, 2)
            grundpreis_einheit = "kg" if menge[1] == "g" else "l"

        streich = (preis_roh.get("strikePrice") or {}).get("strikePriceValue")
        slug = roh.get("productSlug")
        return Offer(
            id=f"{self.name}:{roh['objectID']}",
            quelle=self.name,
            markt=self._markt,
            produkt=" ".join((roh.get("name") or "").split()),
            marke=_marke(roh.get("brandName")),
            beschreibung=roh.get("salesUnit") or "",
            menge=menge[0] if menge else None,
            einheit=menge[1] if menge else None,
            preis=preis,
            grundpreis=grundpreis,
            grundpreis_einheit=grundpreis_einheit,
            regulaerer_preis=float(streich) if streich else None,
            gueltig_von=von,
            gueltig_bis=bis,
            # Die Aktionsgruppen („Haltbare Produkte“) sind zu grob fürs Matching
            kategorie=None,
            quelle_url=PRODUKT_URL.format(slug=slug) if slug else ANGEBOTE,
        )
