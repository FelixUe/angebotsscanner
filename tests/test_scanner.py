import json
from datetime import date
from pathlib import Path

import yaml
from pydantic import TypeAdapter

from angebotsscanner import cli
from angebotsscanner.config import Config, Vorrat
from angebotsscanner.historie import Historie
from angebotsscanner.modelle import Art, Bedarf, Offer
from angebotsscanner.quellen import QuelleFehler
from angebotsscanner.quellen.marktguru import Marktguru
from angebotsscanner.scanner import naechster_einkaufstag, scanne, vorrat_als_bedarf, zusammenfassen

from .conftest import HEUTE, ROOT


def beispiel_bedarf() -> list[Bedarf]:
    geplant = TypeAdapter(list[Bedarf]).validate_json((ROOT / "bedarf.example.json").read_bytes())
    vorrat = Vorrat.model_validate(yaml.safe_load((ROOT / "vorrat.example.yaml").read_text()))
    return geplant + vorrat_als_bedarf(vorrat)


def test_einkaufstag() -> None:
    assert naechster_einkaufstag(date(2026, 11, 23), 4) == date(2026, 11, 27)
    assert naechster_einkaufstag(date(2026, 11, 27), 4) == date(2026, 11, 27)
    assert naechster_einkaufstag(date(2026, 11, 29), 4) == date(2026, 12, 4)


def test_zusammenfassen_wirft_spaetere_frischware_raus() -> None:
    b = zusammenfassen(beispiel_bedarf(), vorlauf_wochen=2)
    tomate = next(x for x in b if x.slug == "tomate")
    assert tomate.menge == 800  # die 1000 g für die Woche danach fallen weg
    assert tomate.gericht == "Shakshuka (Sa)"


def test_zusammenfassen_uebernimmt_menge_und_referenz_vom_vorrat() -> None:
    grund = Bedarf(slug="nudeln", name="Nudeln", gericht="Grundeinkauf")
    vorrat = Bedarf(
        slug="nudeln",
        name="Nudeln",
        art=Art.VORRAT,
        gericht="Vorrat",
        menge=2,
        einheit="Packung",
        referenz_grundpreis=2.0,
    )
    (b,) = zusammenfassen([grund, vorrat], vorlauf_wochen=2)
    assert (b.art, b.menge, b.einheit, b.referenz_grundpreis) == (Art.GEPLANT, 2, "Packung", 2.0)
    assert b.gericht == "Grundeinkauf + Vorrat"


def test_scan_gegen_fixtures(config: Config, marktguru: Marktguru, historie: Historie) -> None:
    e = scanne(beispiel_bedarf(), [marktguru], config, historie, HEUTE)
    assert e.einkaufstag == date(2026, 11, 27)
    assert not e.fehler
    v = {x.slug: x for x in e.vorschlaege}

    # Günstigster Kilopreis gewinnt, nicht die größte Rabattangabe
    assert v["tomate"].artikel == "Gut & Günstig Rispentomaten 1 kg"
    assert v["tomate"].menge == 1

    # Geplante Spaghetti und Vorrat Nudeln fallen auf dasselbe Angebot
    assert v["spaghetti"].artikel == "Gut & Günstig Pasta 500 g"
    assert v["spaghetti"].grund == "Spaghetti Bolognese vegetarisch (Mo) + Vorrat"
    assert v["spaghetti"].menge == 3
    assert v["spaghetti"].art is Art.GEPLANT
    assert (v["spaghetti"].ersparnis_prozent, v["spaghetti"].ersparnis_basis) == (41.0, "referenz")

    # Weidebutter, keine Margarine
    assert v["butter"].artikel == "Edeka Herzstücke Weidebutter 250 g"
    assert "nudeln" not in v  # steckt im Spaghetti-Eintrag
    assert len({x.angebot_id for x in e.vorschlaege}) == len(e.vorschlaege)


def test_zweiter_lauf_ist_idempotent(config, marktguru, historie) -> None:
    erster = scanne(beispiel_bedarf(), [marktguru], config, historie, HEUTE)
    zweiter = scanne(beispiel_bedarf(), [marktguru], config, historie, HEUTE)
    assert erster.vorschlaege == zweiter.vorschlaege


def test_ausgefallene_quelle_blockiert_die_anderen_nicht(config, marktguru, historie) -> None:
    class Kaputt:
        name = "kaputt"

        def fetch(self, plz, suchbegriffe=()) -> list[Offer]:
            raise QuelleFehler("Format geändert")

    e = scanne(beispiel_bedarf(), [Kaputt(), marktguru], config, historie, HEUTE)
    assert e.fehler == {"kaputt": "Format geändert"}
    assert e.vorschlaege


def test_abgelaufen_am_einkaufstag(config, marktguru, historie) -> None:
    """Nächste Woche gilt kein Angebot aus den Fixtures mehr."""
    e = scanne(beispiel_bedarf(), [marktguru], config, historie, date(2026, 11, 30))
    assert e.vorschlaege == []


def test_cli_scan(tmp_path: Path, config: Config, monkeypatch, capsys, abrufer) -> None:
    pfad = tmp_path / "config.yaml"
    pfad.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8")
    monkeypatch.setattr(cli, "_abrufer", lambda _c: abrufer)
    rc = cli.main(
        [
            "--config",
            str(pfad),
            "scan",
            "--bedarf",
            str(ROOT / "bedarf.example.json"),
            "--vorrat",
            str(ROOT / "vorrat.example.yaml"),
            "--heute",
            "2026-11-23",
        ]
    )
    assert rc == 0
    ausgabe = json.loads(capsys.readouterr().out)
    assert ausgabe["einkaufstag"] == "2026-11-27"
    assert {v["markt"] for v in ausgabe["vorschlaege"]} <= {"EDEKA", "Penny", "Aldi Nord"}
    assert (tmp_path / "daten" / "preise.sqlite").exists()
