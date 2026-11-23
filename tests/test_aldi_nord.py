"""Aldi-Nord-Adapter gegen die gekürzte Angebotsseite (Woche 23.–28.11.2026)."""

from __future__ import annotations

from datetime import date

import pytest

from angebotsscanner.config import Config
from angebotsscanner.historie import Historie
from angebotsscanner.http import Abrufer
from angebotsscanner.modelle import Art, Bedarf
from angebotsscanner.quellen import QuelleFehler
from angebotsscanner.quellen.aldi_nord import AldiNord, parse_menge
from angebotsscanner.scanner import scanne

from .conftest import HEUTE, FakeMarktguru


@pytest.fixture
def aldi(abrufer: Abrufer, config: Config) -> AldiNord:
    return AldiNord(abrufer, "Aldi Nord", config.quelle("aldi_nord").ohne_gruppen)


@pytest.mark.parametrize(
    ("einheit", "erwartet"),
    [
        ("250-g-Packung", (250.0, "g")),
        ("1-L-Packung", (1000.0, "ml")),
        ("0,5-L-Dose", (500.0, "ml")),
        ("1,5-kg-Beutel", (1500.0, "g")),
        ("2.000-g-Packung", (2000.0, "g")),
        ("2x125-g-Packung", (250.0, "g")),
        ("kg-Preis", (1000.0, "g")),
        ("Stück", None),
        (None, None),
    ],
)
def test_parse_menge(einheit, erwartet) -> None:
    assert parse_menge(einheit) == erwartet


def test_ein_abruf_fuer_alles(aldi: AldiNord, fake: FakeMarktguru) -> None:
    angebote = aldi.fetch("20095", ["butter", "milch", "reis"])
    assert len([r for r in fake.anfragen if r.url.host == "www.aldi-nord.de"]) == 1
    assert len(angebote) > 100
    assert {a.markt for a in angebote} == {"Aldi Nord"}
    assert len({a.id for a in angebote}) == len(angebote)


def test_normalisierung_milch(aldi: AldiNord) -> None:
    milch = next(a for a in aldi.fetch() if a.id == "aldi_nord:1008594")
    assert milch.produkt == "Haltbare Milch"
    assert milch.marke == "Bärenmarke"
    assert (milch.menge, milch.einheit) == (1000.0, "ml")
    assert milch.preis == 0.88
    assert (milch.grundpreis, milch.grundpreis_einheit) == (0.88, "l")
    assert milch.regulaerer_preis == 1.49
    assert milch.gueltig_von == date(2026, 11, 23)
    assert milch.gilt_am(date(2026, 11, 27))
    assert milch.kategorie is None
    assert milch.quelle_url.startswith("https://www.aldi-nord.de/produkt/")


def test_nonfood_gruppen_fallen_weg(aldi: AldiNord, abrufer: Abrufer) -> None:
    alle = AldiNord(abrufer, "Aldi Nord").fetch()
    gefiltert = aldi.fetch()
    assert len(gefiltert) < len(alle)
    assert not any("spielzeug" in (a.quelle_url or "") for a in gefiltert)


def test_sperre_wird_gemeldet(aldi: AldiNord, fake: FakeMarktguru) -> None:
    fake.aldi_kaputt = True
    with pytest.raises(QuelleFehler, match="HTTP 403"):
        aldi.fetch()


def test_seite_umgebaut(abrufer: Abrufer, fake: FakeMarktguru, monkeypatch) -> None:
    monkeypatch.setattr(abrufer, "get", lambda *a, **k: "<html>neu</html>")
    with pytest.raises(QuelleFehler, match="__NEXT_DATA__"):
        AldiNord(abrufer).fetch()


def test_kaergarden_ist_keine_butter(aldi: AldiNord, config: Config, historie: Historie) -> None:
    butter = Bedarf(
        slug="butter",
        name="Butter",
        art=Art.GEPLANT,
        frisch=True,
        menge=250,
        einheit="g",
        gericht="Grundeinkauf",
    )
    ergebnis = scanne([butter], [aldi], config, historie, HEUTE)
    assert not any("rgården" in v.artikel for v in ergebnis.vorschlaege)


def test_haltbare_milch_als_notvorrat(aldi: AldiNord, config: Config, historie: Historie) -> None:
    hmilch = Bedarf(
        slug="h-milch",
        name="H-Milch",
        aliases=["Haltbare Milch"],
        art=Art.NOTVORRAT,
        menge=6,
        einheit="l",
        gericht="Notvorrat",
    )
    ergebnis = scanne([hmilch], [aldi], config, historie, HEUTE)
    (vorschlag,) = ergebnis.vorschlaege
    assert vorschlag.markt == "Aldi Nord"
    assert vorschlag.preis == 0.88
    assert vorschlag.menge == 6
