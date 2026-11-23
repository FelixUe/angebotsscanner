from datetime import date

import httpx
import pytest

from angebotsscanner.http import Abrufer
from angebotsscanner.quellen import QuelleFehler
from angebotsscanner.quellen.marktguru import Marktguru

from .conftest import FakeMarktguru


def test_nur_konfigurierte_maerkte(marktguru: Marktguru) -> None:
    angebote = marktguru.fetch("20095", ["Butter"])
    assert angebote
    assert {a.markt for a in angebote} == {"EDEKA", "Penny"}  # kein REWE, Lidl, Handelshof


def test_normalisierung(marktguru: Marktguru) -> None:
    angebote = {a.id: a for a in marktguru.fetch("20095", ["Butter"])}
    rama = angebote["marktguru:25818207"]
    assert rama.markt == "Penny"
    assert rama.marke == "Rama"
    assert rama.produkt == "mit Butter XXL"
    assert (rama.menge, rama.einheit) == (400, "g")
    assert (rama.preis, rama.grundpreis, rama.grundpreis_einheit) == (1.99, 4.97, "kg")
    # 2026-11-22T23:00:00Z ist in Berlin schon Montag
    assert (rama.gueltig_von, rama.gueltig_bis) == (date(2026, 11, 23), date(2026, 11, 28))
    assert rama.kategorie == "Butter"
    assert rama.quelle_url == "https://www.marktguru.de/offers/25818207"


def test_streichpreis_wird_regulaerer_preis(marktguru: Marktguru) -> None:
    tomaten = [a for a in marktguru.fetch("20095", ["Tomaten"]) if a.regulaerer_preis]
    assert any(a.preis == 1.49 and a.regulaerer_preis == 1.99 for a in tomaten)


def test_app_pflicht_und_app_preis(abrufer: Abrufer, config) -> None:
    ohne = Marktguru(abrufer, config.maerkte_fuer("marktguru"), apps=[])
    kroenung = next(a for a in ohne.fetch("20095", ["Kaffee"]) if a.produkt == "Krönung")
    assert kroenung.app_pflicht == "penny"  # requiresLoyalityMembership

    mit = Marktguru(abrufer, config.maerkte_fuer("marktguru"), apps=["edeka"])
    idee = next(a for a in mit.fetch("20095", ["Kaffee"]) if a.id == "marktguru:25844272")
    # „HINWEIS: MIT EDEKA APP 6.49 €“ statt 6,99 €
    assert idee.preis == 6.49
    assert idee.app_pflicht == "edeka"


def test_suchbegriffe_einmalig_und_begrenzt(abrufer: Abrufer, config, fake: FakeMarktguru) -> None:
    mg = Marktguru(abrufer, config.maerkte_fuer("marktguru"), max_begriffe=2)
    mg.fetch("20095", ["Butter", "Butter", " ", "Milch", "Kaffee"])
    assert fake.suchen == ["Butter", "Milch"]


def test_schluessel_werden_einmal_neu_geholt() -> None:
    fake = FakeMarktguru(schluessel_ok=False)
    abrufer = Abrufer("test", None, abstand_s=0, transport=httpx.MockTransport(fake))
    mg = Marktguru(abrufer, [], [])
    with pytest.raises(QuelleFehler, match="Schlüssel"):
        mg.fetch("20095", ["Butter"])
    assert len(fake.suchen) == 2


def test_serverfehler_wird_quellefehler() -> None:
    abrufer = Abrufer(
        "test", None, abstand_s=0, transport=httpx.MockTransport(FakeMarktguru(kaputt=True))
    )
    with pytest.raises(QuelleFehler, match="HTTP 500"):
        Marktguru(abrufer, [], []).fetch("20095", ["Butter"])
