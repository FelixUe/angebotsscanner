"""Mealie-Einkaufsliste als Bedarf lesen und Angebote hineinschreiben.

Der Essensplaner schreibt den Wochenzettel in die Liste „Wocheneinkauf“
(Zeilen mit ``extras.essensplaner_key``). Der Scanner liest daraus den
Bedarf und schreibt zurück:

* **Angebot zu einer Zeile des Zettels:** hinter die Notiz kommt
  ``· 🏷 EDEKA 0,59 € (−41 %) bis Sa 28.11.``. Alles ab ``🏷`` gehört dem
  Scanner, der Essensplaner lässt es bei seinen Updates stehen. Dazu
  ``angebotsscanner_*`` in ``extras``.
* **Angebot für Vorrat/Notvorrat ohne Zeile:** eine eigene Zeile mit
  ``extras.angebotsscanner_key``.

Bei jedem Lauf gilt: abgehakte Zeilen werden nie angefasst. Angebote, die
nicht mehr gelten oder nicht mehr vorgeschlagen werden, verschwinden – der
Hinweis aus der Notiz, die eigene Zeile ganz. Fällt eine Quelle aus, bleiben
deren noch gültige Hinweise stehen, statt grundlos zu verschwinden.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

import httpx

from . import text
from .modelle import Art, Bedarf, Ergebnis, Vorschlag

log = logging.getLogger(__name__)

ANGEBOT_ZEICHEN = "🏷"
EP = "essensplaner_"
X = "angebotsscanner_"
KEY = X + "key"
ANGEBOT = X + "angebot"
BIS = X + "bis"
MARKT = X + "markt"

LISTEN = "/api/households/shopping/lists"
ITEMS = "/api/households/shopping/items"
LABELS = "/api/groups/labels"

WOCHENTAGE = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

#: „(2 l)“, „(10 Stück)“ im Grundeinkauf
_KLAMMER = re.compile(r"\(([^)]*)\)")
_MENGE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([a-zA-Zäöü]+)?\s*$")
#: „Käse fürs Brot“, „Streukäse zum Überbacken“ → nur das Ding selbst
_ZWECK = re.compile(r"\s+(?:für|fürs|zum|zur)\s+.*$", re.I)
_ODER = re.compile(r"\s+(?:oder|und)\s+|\s*/\s*", re.I)
_EINHEITEN = {
    "g": "g",
    "kg": "kg",
    "ml": "ml",
    "l": "l",
    "stk": "stk",
    "stück": "stk",
    "dose": "Dose",
    "dosen": "Dose",
    "packung": "Packung",
    "pck": "Packung",
    "bund": "Bund",
    "glas": "Glas",
}


class MealieFehler(Exception):
    """Mealie nicht erreichbar, Zugang abgelehnt oder Liste fehlt."""


# ------------------------------------------------------------------ Lesen


@dataclass(slots=True)
class Zeile:
    """Eine Zeile des Essensplaner-Zettels, aufbereitet als Bedarf."""

    item: dict
    bedarf: Bedarf


def _zahl(wert: str | None) -> float | None:
    try:
        return float(str(wert).replace(",", "."))
    except (TypeError, ValueError):
        return None


def name_und_aliases(roh: str) -> tuple[str, list[str], float | None, str | None]:
    """„Kidneybohnen oder Kichererbsen“ → Name, Aliases; „Milch (2 l)“ → Menge."""
    menge = einheit = None
    for inhalt in _KLAMMER.findall(roh):
        if m := _MENGE.match(inhalt):
            menge = _zahl(m.group(1))
            einheit = _EINHEITEN.get((m.group(2) or "stk").casefold())
    sauber = _ZWECK.sub("", _KLAMMER.sub("", roh)).strip()
    teile = [t.strip() for t in _ODER.split(sauber) if t.strip()]
    if not teile:
        return roh.strip(), [], menge, einheit
    return teile[0], teile[1:], menge, einheit


def zeile_als_bedarf(item: dict) -> Bedarf | None:
    e = item.get("extras") or {}
    if not e.get(EP + "key"):
        return None
    if e.get(EP + "slug"):
        name = e.get(EP + "name") or e[EP + "slug"]
        aliases: list[str] = []
        menge = _zahl(e.get(EP + "menge"))
        einheit = _EINHEITEN.get((e.get(EP + "einheit") or "").casefold())
        if einheit is None:
            menge = None
        slug = text.slug(e[EP + "slug"])
        frisch = e.get(EP + "frisch") == "ja"
        gericht = e.get(EP + "gerichte") or None
    else:  # Grundeinkauf oder „nur diese Woche“
        name, aliases, menge, einheit = name_und_aliases(e.get(EP + "text") or item.get("note", ""))
        slug = text.slug(name)
        frisch = False
        gericht = "Grundeinkauf" if e.get(EP + "quelle") == "grund" else "diese Woche"
    if not slug:
        return None
    return Bedarf(
        slug=slug,
        name=name,
        aliases=aliases,
        art=Art.GEPLANT,
        frisch=frisch,
        gericht=gericht,
        menge=menge,
        einheit=einheit,
    )


def bedarf_aus_liste(items: Iterable[dict], einkaufstag: date) -> list[Zeile]:
    """Offene Zeilen des Essensplaners für diesen Einkauf."""
    zeilen = []
    for item in items:
        e = item.get("extras") or {}
        if item.get("checked") or not e.get(EP + "key"):
            continue
        if e.get(EP + "woche") != einkaufstag.isoformat():
            log.info("Mealie: %r gehört zu einem anderen Einkauf, übersprungen", item.get("note"))
            continue
        if (b := zeile_als_bedarf(item)) is not None:
            zeilen.append(Zeile(item, b))
    return zeilen


# ---------------------------------------------------------------- Schreiben


def _euro(wert: float) -> str:
    return f"{wert:.2f}".replace(".", ",") + " €"


def hinweis(v: Vorschlag, mit_artikel: bool = True) -> str:
    """``🏷 3× Gut & Günstig Pasta 500 g: EDEKA 0,59 € (−41 %) bis Sa 28.11.``"""
    teile = [ANGEBOT_ZEICHEN]
    if mit_artikel:
        teile.append(f"{v.menge}× {v.artikel}:" if v.menge > 1 else f"{v.artikel}:")
    teile.append(f"{v.markt} {_euro(v.preis)}")
    if v.ersparnis_prozent is not None and v.ersparnis_prozent > 0:
        teile.append(f"(−{v.ersparnis_prozent:.0f} %)")
    bis = v.gueltig_bis
    teile.append(f"bis {WOCHENTAGE[bis.weekday()]} {bis.day}.{bis.month}.")
    return " ".join(teile)


def eigener_teil(note: str | None) -> str:
    return (note or "").split(ANGEBOT_ZEICHEN, 1)[0].rstrip(" ·")


def _extras(v: Vorschlag) -> dict[str, str]:
    return {
        ANGEBOT: v.angebot_id,
        BIS: v.gueltig_bis.isoformat(),
        MARKT: v.markt,
        X + "preis": f"{v.preis:.2f}",
        X + "menge": str(v.menge),
        X + "url": v.quelle_url or "",
    }


def _ohne_scanner(extras: dict | None) -> dict:
    return {k: v for k, v in (extras or {}).items() if not k.startswith(X)}


@dataclass
class MealieBericht:
    markiert: list[str] = field(default_factory=list)
    angelegt: list[str] = field(default_factory=list)
    entfernt: list[str] = field(default_factory=list)
    geloescht: list[str] = field(default_factory=list)
    behalten: list[str] = field(default_factory=list)
    unveraendert: int = 0


class MealieListe:
    """Zugriff auf eine Mealie-Einkaufsliste über die REST-API (v3)."""

    def __init__(
        self,
        url: str,
        token: str,
        liste: str = "Wocheneinkauf",
        label: str | None = "Angebote",
        timeout_s: float = 20,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.liste_name = liste
        self.label_name = label
        self._client = httpx.Client(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=timeout_s,
            transport=transport,
        )
        self._liste: dict | None = None

    def schliessen(self) -> None:
        self._client.close()

    def _anfrage(self, methode: str, pfad: str, **kw) -> object:
        try:
            antwort = self._client.request(methode, pfad, **kw)
            antwort.raise_for_status()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in (401, 403):
                raise MealieFehler(f"Mealie lehnt das Token ab (HTTP {code})") from exc
            raise MealieFehler(f"Mealie {methode} {pfad}: HTTP {code}") from exc
        except httpx.HTTPError as exc:
            raise MealieFehler(f"Mealie nicht erreichbar: {exc}") from exc
        return antwort.json() if antwort.content else None

    def items(self) -> list[dict]:
        seite = self._anfrage("GET", LISTEN, params={"page": 1, "perPage": -1})
        for liste in seite.get("items", []):
            if liste.get("name", "").casefold() == self.liste_name.casefold():
                self._liste = self._anfrage("GET", f"{LISTEN}/{liste['id']}")
                return list(self._liste.get("listItems") or [])
        raise MealieFehler(
            f"Mealie-Liste {self.liste_name!r} fehlt – sie entsteht mit "
            "`essensplaner mealie-einkauf`"
        )

    def _label_id(self, dry_run: bool) -> str | None:
        if not self.label_name:
            return None
        seite = self._anfrage("GET", LABELS, params={"page": 1, "perPage": -1})
        for lb in seite.get("items", []):
            if lb["name"].casefold() == self.label_name.casefold():
                return lb["id"]
        if dry_run:
            return None
        return self._anfrage("POST", LABELS, json={"name": self.label_name})["id"]

    def schreibe(
        self,
        ergebnis: Ergebnis,
        zeilen: list[Zeile],
        items: list[dict],
        heute: date,
        ausgefallene_maerkte: set[str] = frozenset(),
        dry_run: bool = False,
    ) -> MealieBericht:
        """Hinweise und eigene Zeilen auf den Stand von ``ergebnis`` bringen."""
        bericht = MealieBericht()
        zu_slug: dict[str, list[dict]] = {}
        for z in zeilen:
            zu_slug.setdefault(z.bedarf.slug, []).append(z.item)

        # Was soll wo stehen?
        markieren: dict[str, Vorschlag] = {}  # item-id → Vorschlag
        eigene_soll: dict[str, Vorschlag] = {}  # angebotsscanner_key → Vorschlag
        for v in ergebnis.vorschlaege:
            treffer = [i for s in v.slugs or [v.slug] for i in zu_slug.get(s, [])]
            if treffer:
                for item in treffer:
                    markieren.setdefault(item["id"], v)
            elif v.art is not Art.GEPLANT:
                eigene_soll[f"{v.art.value}:{v.slug}"] = v

        def behalten(item: dict) -> bool:
            e = item.get("extras") or {}
            bis = e.get(BIS, "")
            return e.get(MARKT) in ausgefallene_maerkte and bis >= heute.isoformat()

        aendern: list[dict] = []
        loeschen: list[dict] = []
        eigene_ist: dict[str, dict] = {}
        for item in items:
            e = item.get("extras") or {}
            if item.get("checked"):
                continue
            if e.get(KEY):
                if e[KEY] in eigene_soll and e[KEY] not in eigene_ist:
                    eigene_ist[e[KEY]] = item
                elif behalten(item):
                    bericht.behalten.append(item.get("note", ""))
                else:
                    loeschen.append(item)
                continue
            v = markieren.get(item["id"])
            if v is not None:
                note = f"{eigener_teil(item.get('note'))} · {hinweis(v)}"
                extras = {**_ohne_scanner(e), **_extras(v)}
                if note == item.get("note") and extras == e:
                    bericht.unveraendert += 1
                else:
                    aendern.append({**item, "note": note, "extras": extras})
                    bericht.markiert.append(eigener_teil(item.get("note")))
            elif ANGEBOT in e or ANGEBOT_ZEICHEN in (item.get("note") or ""):
                if behalten(item):
                    bericht.behalten.append(item.get("note", ""))
                    continue
                aendern.append(
                    {**item, "note": eigener_teil(item.get("note")), "extras": _ohne_scanner(e)}
                )
                bericht.entfernt.append(eigener_teil(item.get("note")))

        anlegen: list[dict] = []
        label_id = None
        for key, v in eigene_soll.items():
            note = f"{v.grund}: {hinweis(v)}"
            extras = {KEY: key, **_extras(v)}
            item = eigene_ist.get(key)
            if item is None:
                if label_id is None:
                    label_id = self._label_id(dry_run)
                anlegen.append(
                    {
                        "shoppingListId": (self._liste or {}).get("id"),
                        "note": note,
                        "quantity": 0,
                        "labelId": label_id,
                        "extras": extras,
                    }
                )
                bericht.angelegt.append(note)
            elif item.get("note") == note and (item.get("extras") or {}) == extras:
                bericht.unveraendert += 1
            else:
                aendern.append({**item, "note": note, "extras": extras})
                bericht.markiert.append(note)
        bericht.geloescht = [i.get("note", "") for i in loeschen]

        if dry_run:
            return bericht
        if loeschen:
            self._anfrage("DELETE", ITEMS, params=[("ids", i["id"]) for i in loeschen])
        for daten in anlegen:
            self._anfrage("POST", ITEMS, json=daten)
        if aendern:
            self._anfrage("PUT", ITEMS, json=aendern)
        log.info(
            "Mealie: %d markiert, %d angelegt, %d Hinweise entfernt, %d gelöscht",
            len(bericht.markiert),
            len(bericht.angelegt),
            len(bericht.entfernt),
            len(bericht.geloescht),
        )
        return bericht
