"""Mealie-Anbindung gegen eine Einkaufsliste im Speicher, ohne Netz."""

from __future__ import annotations

import copy
import itertools
import json
from datetime import date
from functools import partial

import httpx
import pytest

from angebotsscanner import cli
from angebotsscanner.config import Config
from angebotsscanner.mealie import (
    ANGEBOT,
    BIS,
    KEY,
    MARKT,
    MealieFehler,
    MealieListe,
    bedarf_aus_liste,
    eigener_teil,
    hinweis,
    name_und_aliases,
    zeile_als_bedarf,
)
from angebotsscanner.modelle import Art, Vorschlag
from angebotsscanner.quellen.aldi_nord import AldiNord
from angebotsscanner.scanner import scanne

from .conftest import HEUTE, ROOT

EINKAUF = date(2026, 11, 27)
WOCHE = EINKAUF.isoformat()


def _ep(key: str, text: str, quelle: str = "grund", **mehr: str) -> dict:
    return {
        "essensplaner_key": key,
        "essensplaner_woche": WOCHE,
        "essensplaner_quelle": quelle,
        "essensplaner_text": text,
        **mehr,
    }


class FakeMealie:
    """Die paar Endpunkte von Mealie v3, die der Scanner nutzt."""

    def __init__(self, token: str = "geheim") -> None:
        self.token = token
        self.ids = (f"id{n}" for n in itertools.count(1))
        self.labels: list[dict] = []
        self.liste = {"id": "liste1", "name": "Wocheneinkauf"}
        self.items: dict[str, dict] = {}
        self.schreibend: list[str] = []

    def item(self, note: str, extras: dict | None = None, checked: bool = False) -> dict:
        iid = next(self.ids)
        self.items[iid] = {
            "id": iid,
            "shoppingListId": "liste1",
            "note": note,
            "quantity": 0,
            "checked": checked,
            "labelId": None,
            "extras": extras or {},
        }
        return self.items[iid]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"detail": "nope"})
        pfad, methode = request.url.path, request.method
        if methode != "GET":
            self.schreibend.append(f"{methode} {pfad}")
        if pfad == "/api/households/shopping/lists":
            return httpx.Response(200, json={"items": [self.liste]})
        if pfad == "/api/households/shopping/lists/liste1":
            items = [copy.deepcopy(i) for i in self.items.values()]
            return httpx.Response(200, json={**self.liste, "listItems": items})
        if pfad == "/api/groups/labels":
            if methode == "GET":
                return httpx.Response(200, json={"items": self.labels})
            label = {"id": next(self.ids), **json.loads(request.content)}
            self.labels.append(label)
            return httpx.Response(201, json=label)
        if pfad == "/api/households/shopping/items":
            if methode == "POST":
                daten = json.loads(request.content)
                iid = next(self.ids)
                self.items[iid] = {"id": iid, "checked": False, **daten}
                return httpx.Response(201, json={"createdItems": [self.items[iid]]})
            if methode == "PUT":
                for neu in json.loads(request.content):
                    self.items[neu["id"]] = neu
                return httpx.Response(200, json={"updatedItems": []})
            if methode == "DELETE":
                for iid in request.url.params.get_list("ids"):
                    del self.items[iid]
                return httpx.Response(200, json={})
        return httpx.Response(404)


@pytest.fixture
def mealie() -> FakeMealie:
    m = FakeMealie()
    m.item(
        "800 g Tomaten · für Shakshuka",
        _ep(
            "frisch:tomate",
            "800 g Tomaten",
            "frisch",
            essensplaner_slug="tomate",
            essensplaner_name="Tomaten",
            essensplaner_menge="800",
            essensplaner_einheit="g",
            essensplaner_frisch="ja",
            essensplaner_gerichte="Shakshuka",
        ),
    )
    m.item("Butter", _ep("grund:Butter", "Butter"))
    m.item("Milch (2 l)", _ep("grund:Milch (2 l)", "Milch (2 l)"))
    m.item("Nudeln", _ep("grund:Nudeln", "Nudeln"))
    m.item("Salat oder Gurke", _ep("grund:Salat oder Gurke", "Salat oder Gurke"))
    m.item("Kaffee", _ep("grund:Kaffee", "Kaffee"), checked=True)
    m.item("Grillkohle")
    return m


@pytest.fixture
def liste(mealie: FakeMealie):
    ml = MealieListe("http://mealie:9000", "geheim", transport=httpx.MockTransport(mealie))
    yield ml
    ml.schliessen()


def _lauf(liste, mealie, config, marktguru, abrufer, historie, heute=HEUTE, **kw):
    items = liste.items()
    zeilen = bedarf_aus_liste(items, EINKAUF)
    quellen = [marktguru, AldiNord(abrufer, "Aldi Nord", config.quelle("aldi_nord").ohne_gruppen)]
    ergebnis = scanne([z.bedarf for z in zeilen], quellen, config, historie, heute)
    return liste.schreibe(ergebnis, zeilen, items, heute, **kw)


def _note(mealie: FakeMealie, anfang: str) -> str:
    return next(i["note"] for i in mealie.items.values() if i["note"].startswith(anfang))


# ------------------------------------------------------------------ Lesen


@pytest.mark.parametrize(
    ("roh", "erwartet"),
    [
        ("Butter", ("Butter", [], None, None)),
        ("Milch (2 l)", ("Milch", [], 2.0, "l")),
        ("Eier (10 Stück)", ("Eier", [], 10.0, "stk")),
        ("Kidneybohnen oder Kichererbsen", ("Kidneybohnen", ["Kichererbsen"], None, None)),
        ("Käse fürs Brot", ("Käse", [], None, None)),
        ("Streukäse zum Überbacken", ("Streukäse", [], None, None)),
        ("Gehackte Tomaten (Dose)", ("Gehackte Tomaten", [], None, None)),
        ("Senf und Ketchup", ("Senf", ["Ketchup"], None, None)),
    ],
)
def test_name_und_aliases(roh, erwartet) -> None:
    assert name_und_aliases(roh) == erwartet


def test_zeilen_werden_zum_bedarf(mealie: FakeMealie) -> None:
    zeilen = bedarf_aus_liste(mealie.items.values(), EINKAUF)
    nach_slug = {z.bedarf.slug: z.bedarf for z in zeilen}
    # Abgehakter Kaffee und die Grillkohle von Hand fehlen
    assert set(nach_slug) == {"tomate", "butter", "milch", "nudeln", "salat"}
    tomate = nach_slug["tomate"]
    assert (tomate.menge, tomate.einheit, tomate.frisch) == (800, "g", True)
    assert tomate.gericht == "Shakshuka"
    assert nach_slug["salat"].aliases == ["Gurke"]
    assert nach_slug["milch"].gericht == "Grundeinkauf"
    assert all(b.art is Art.GEPLANT for b in nach_slug.values())


def test_andere_woche_zaehlt_nicht(mealie: FakeMealie) -> None:
    assert bedarf_aus_liste(mealie.items.values(), date(2026, 12, 4)) == []


def test_unbekannte_einheit_heisst_ohne_menge() -> None:
    item = {
        "extras": _ep(
            "frisch:salz",
            "1 Prise Salz",
            essensplaner_slug="salz",
            essensplaner_menge="1",
            essensplaner_einheit="prise",
        )
    }
    b = zeile_als_bedarf(item)
    assert b is not None and b.menge is None and b.einheit is None


# -------------------------------------------------------------- Schreiben


def test_hinweis_text() -> None:
    v = Vorschlag(
        angebot_id="x",
        artikel="Gut & Günstig Pasta 500 g",
        slug="nudeln",
        menge=3,
        markt="EDEKA",
        preis=0.59,
        ersparnis_prozent=41.0,
        gueltig_von=date(2026, 11, 23),
        gueltig_bis=date(2026, 11, 28),
        grund="Grundeinkauf",
        art=Art.GEPLANT,
        score=1.0,
    )
    assert hinweis(v) == "🏷 3× Gut & Günstig Pasta 500 g: EDEKA 0,59 € (−41 %) bis Sa 28.11."
    assert eigener_teil("Nudeln · " + hinweis(v)) == "Nudeln"


def test_lauf_markiert_zeilen_und_laesst_fremdes_stehen(
    liste, mealie, config, marktguru, abrufer, historie
) -> None:
    vorher_hand = copy.deepcopy(next(i for i in mealie.items.values() if i["note"] == "Grillkohle"))
    vorher_kaffee = copy.deepcopy(next(i for i in mealie.items.values() if i["note"] == "Kaffee"))

    bericht = _lauf(liste, mealie, config, marktguru, abrufer, historie)

    assert _note(mealie, "800 g Tomaten").startswith("800 g Tomaten · für Shakshuka · 🏷 ")
    assert "Rispentomaten" in _note(mealie, "800 g Tomaten")
    assert "🏷 " in _note(mealie, "Butter")
    assert "🏷 " in _note(mealie, "Nudeln")
    # „oder“ macht Gurke zum Alias – die Mini-Gurken von Aldi passen
    assert "Gurken" in _note(mealie, "Salat oder Gurke")
    assert mealie.items[vorher_hand["id"]] == vorher_hand
    assert mealie.items[vorher_kaffee["id"]] == vorher_kaffee
    tomate = next(i for i in mealie.items.values() if i["note"].startswith("800 g Tomaten"))
    assert tomate["extras"]["essensplaner_key"] == "frisch:tomate"
    assert tomate["extras"][ANGEBOT].startswith(("marktguru:", "aldi_nord:"))
    assert tomate["extras"][BIS] >= WOCHE
    assert len(bericht.markiert) >= 3


def test_zweiter_lauf_schreibt_nichts(liste, mealie, config, marktguru, abrufer, historie) -> None:
    _lauf(liste, mealie, config, marktguru, abrufer, historie)
    mealie.schreibend.clear()
    bericht = _lauf(liste, mealie, config, marktguru, abrufer, historie)
    assert (bericht.markiert, bericht.angelegt, bericht.entfernt, bericht.geloescht) == (
        [],
        [],
        [],
        [],
    )
    assert mealie.schreibend == []


def test_vorrat_ohne_zeile_bekommt_eigene_zeile(
    liste, mealie, config, marktguru, abrufer, historie
) -> None:
    from angebotsscanner.config import lade_vorrat
    from angebotsscanner.scanner import vorrat_als_bedarf

    items = liste.items()
    zeilen = bedarf_aus_liste(items, EINKAUF)
    vorrat = vorrat_als_bedarf(lade_vorrat(ROOT / "vorrat.example.yaml"))
    aldi = AldiNord(abrufer, "Aldi Nord", config.quelle("aldi_nord").ohne_gruppen)
    ergebnis = scanne(
        [z.bedarf for z in zeilen] + vorrat, [marktguru, aldi], config, historie, HEUTE
    )
    bericht = liste.schreibe(ergebnis, zeilen, items, HEUTE)

    eigene = [i for i in mealie.items.values() if (i["extras"] or {}).get(KEY)]
    assert eigene, "Passata aus dem Vorrat hat keine Zeile im Zettel"
    passata = next(i for i in eigene if i["extras"][KEY] == "vorrat:passata")
    assert passata["note"].startswith("Vorrat: 🏷 ")
    assert passata["quantity"] == 0
    assert [lb["name"] for lb in mealie.labels] == ["Angebote"]
    assert passata["labelId"] == mealie.labels[0]["id"]
    # Nudeln stehen im Grundeinkauf: der Vorrat hängt sich an diese Zeile
    assert not any(i["extras"][KEY] == "vorrat:nudeln" for i in eigene)
    # … und bringt Menge und Referenzpreis mit
    assert "🏷 2× Gut & Günstig Pasta 500 g: EDEKA 0,59 € (−41 %)" in _note(mealie, "Nudeln")
    assert bericht.angelegt


def test_abgelaufenes_verschwindet(liste, mealie, config, marktguru, abrufer, historie) -> None:
    alt = mealie.item(
        "Reis · 🏷 Penny 0,99 € bis Sa 21.11.",
        {**_ep("grund:Reis", "Reis"), ANGEBOT: "marktguru:1", BIS: "2026-11-21", MARKT: "Penny"},
    )
    eigen = mealie.item(
        "Vorrat: 🏷 Penny Kidneybohnen", {KEY: "notvorrat:kidneybohnen", BIS: "2026-11-21"}
    )
    eigen_hak = mealie.item(
        "Vorrat: 🏷 Penny Reis", {KEY: "vorrat:reis", BIS: "2026-11-21"}, checked=True
    )

    bericht = _lauf(liste, mealie, config, marktguru, abrufer, historie)

    assert mealie.items[alt["id"]]["note"] == "Reis"
    assert ANGEBOT not in mealie.items[alt["id"]]["extras"]
    assert mealie.items[alt["id"]]["extras"]["essensplaner_key"] == "grund:Reis"
    assert eigen["id"] not in mealie.items
    assert eigen_hak["id"] in mealie.items
    assert "Reis" in bericht.entfernt


def test_ausgefallene_quelle_behaelt_gueltige_hinweise(
    liste, mealie, config, marktguru, abrufer, historie
) -> None:
    noch_gut = mealie.item(
        "Reis · 🏷 Aldi Nord 0,99 € bis Sa 28.11.",
        {
            **_ep("grund:Reis", "Reis"),
            ANGEBOT: "aldi_nord:1",
            BIS: "2026-11-28",
            MARKT: "Aldi Nord",
        },
    )
    bericht = _lauf(
        liste,
        mealie,
        config,
        marktguru,
        abrufer,
        historie,
        ausgefallene_maerkte={"Aldi Nord"},
    )
    assert mealie.items[noch_gut["id"]]["note"].endswith("bis Sa 28.11.")
    assert bericht.behalten


def test_dry_run_schreibt_nichts(liste, mealie, config, marktguru, abrufer, historie) -> None:
    bericht = _lauf(liste, mealie, config, marktguru, abrufer, historie, dry_run=True)
    assert bericht.markiert
    assert mealie.schreibend == []


def test_falsches_token(mealie: FakeMealie) -> None:
    ml = MealieListe("http://mealie:9000", "falsch", transport=httpx.MockTransport(mealie))
    with pytest.raises(MealieFehler, match="Token"):
        ml.items()


def test_fehlende_liste(mealie: FakeMealie) -> None:
    ml = MealieListe("http://m", "geheim", "Gibts nicht", transport=httpx.MockTransport(mealie))
    with pytest.raises(MealieFehler, match="mealie-einkauf"):
        ml.items()


# -------------------------------------------------------------------- CLI


def test_cli_mealie(tmp_path, config: Config, mealie, abrufer, monkeypatch, capsys) -> None:
    import yaml

    pfad = tmp_path / "config.yaml"
    pfad.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8")
    monkeypatch.setenv("MEALIE_URL", "http://mealie:9000")
    monkeypatch.setenv("MEALIE_TOKEN", "geheim")
    monkeypatch.setattr(cli, "_abrufer", lambda _c: abrufer)
    monkeypatch.setattr(
        cli, "MealieListe", partial(MealieListe, transport=httpx.MockTransport(mealie))
    )
    rc = cli.main(["--config", str(pfad), "mealie", "--heute", "2026-11-23"])
    assert rc == 0
    ausgabe = capsys.readouterr().out
    assert "Einkauf 2026-11-27: 5 Zeilen gelesen" in ausgabe
    assert "🏷 " in _note(mealie, "Butter")


def test_cli_mealie_ohne_zugang(tmp_path, config: Config, monkeypatch) -> None:
    import yaml

    pfad = tmp_path / "config.yaml"
    pfad.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8")
    monkeypatch.delenv("MEALIE_URL", raising=False)
    assert cli.main(["--config", str(pfad), "mealie"]) == 2
