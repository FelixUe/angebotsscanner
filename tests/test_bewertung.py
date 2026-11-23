from datetime import date

from angebotsscanner.bewertung import bewerte, ersparnis, haltbarkeit, packungen
from angebotsscanner.config import Config
from angebotsscanner.historie import Historie
from angebotsscanner.matching import Treffer, Urteil
from angebotsscanner.modelle import Art, Bedarf, Offer

FREITAG = date(2026, 11, 27)


def angebot(
    grundpreis: float, preis: float = 1.0, regulaer: float | None = None, woche: int = 0, **kw
) -> Offer:
    von = date(2026, 11, 23 - 7 * woche)
    return Offer(
        id=kw.pop("id", f"test:{grundpreis}:{woche}"),
        quelle="test",
        markt="EDEKA",
        produkt="Pasta",
        menge=500,
        einheit="g",
        preis=preis,
        grundpreis=grundpreis,
        grundpreis_einheit="kg",
        regulaerer_preis=regulaer,
        gueltig_von=von,
        gueltig_bis=date.fromordinal(von.toordinal() + 5),
        **kw,
    )


NUDELN = Bedarf(slug="nudeln", name="Nudeln", art=Art.VORRAT, menge=2, einheit="Packung")


def test_historie_schlaegt_streichpreis(config: Config, historie: Historie) -> None:
    for w, gp in ((1, 2.0), (2, 2.2), (3, 1.8)):
        historie.erfasse("nudeln", angebot(gp, woche=w), date(2026, 11, 1))
    e = ersparnis(NUDELN, angebot(1.2, regulaer=5.0), historie, config, FREITAG)
    assert e.basis == "historie"
    assert e.prozent == 40.0  # 1,20 gegen Median 2,00
    assert e.tiefstpreis


def test_aktuelle_woche_zaehlt_nicht_zur_historie(config: Config, historie: Historie) -> None:
    jetzt = angebot(1.2)
    historie.erfasse("nudeln", jetzt, date(2026, 11, 23))
    historie.erfasse("nudeln", jetzt, date(2026, 11, 23))  # idempotent
    assert historie.statistik("nudeln", "kg", FREITAG) is None


def test_duenne_historie_faellt_auf_streichpreis_zurueck(
    config: Config, historie: Historie
) -> None:
    historie.erfasse("nudeln", angebot(1.0, woche=1), date(2026, 11, 16))
    e = ersparnis(NUDELN, angebot(1.5, preis=0.75, regulaer=1.0), historie, config, FREITAG)
    assert (e.basis, e.prozent) == ("streichpreis", 25.0)


def test_referenz_vor_streichpreis(config: Config, historie: Historie) -> None:
    """Der Prospekt sagt −22 %, der eigene Kilopreis sagt: teurer als sonst."""
    kaffee = Bedarf(slug="kaffee", name="Kaffee", art=Art.VORRAT, referenz_grundpreis=12.0)
    e = ersparnis(kaffee, angebot(13.98, preis=6.99, regulaer=8.99), historie, config, FREITAG)
    assert e.basis == "referenz"
    assert e.prozent < 0


def test_vorrat_braucht_ersparnis(config: Config, historie: Historie) -> None:
    ohne = Treffer(NUDELN, angebot(1.9), Urteil.SICHER, "")
    assert bewerte(ohne, historie, config, FREITAG) is None
    mit_ref = NUDELN.model_copy(update={"referenz_grundpreis": 2.0})
    knapp = Treffer(mit_ref, angebot(1.7), Urteil.SICHER, "")  # 15 % < 20 %
    assert bewerte(knapp, historie, config, FREITAG) is None
    gut = Treffer(mit_ref, angebot(1.2), Urteil.SICHER, "")  # 40 %
    x = bewerte(gut, historie, config, FREITAG)
    assert x is not None and x.menge == 2


def test_geplant_auch_ohne_ersparnisangabe(config: Config, historie: Historie) -> None:
    geplant = Bedarf(slug="nudeln", name="Nudeln", menge=1200, einheit="g")
    x = bewerte(Treffer(geplant, angebot(1.9), Urteil.SICHER, ""), historie, config, FREITAG)
    assert x is not None
    assert x.menge == 3  # 1200 g in 500-g-Packungen


def test_frischware_nur_fuer_diesen_einkauf() -> None:
    assert haltbarkeit(Bedarf(slug="t", name="Tomaten", frisch=True)) == 1
    assert haltbarkeit(Bedarf(slug="t", name="Tomaten", frisch=True, woche=1)) == 0
    assert haltbarkeit(Bedarf(slug="k", name="Käse", frisch=True, art=Art.VORRAT)) == 0


def test_packungen() -> None:
    a = angebot(2.0)  # 500 g
    assert packungen(Bedarf(slug="x", name="x", menge=1, einheit="kg"), a) == 2
    assert packungen(Bedarf(slug="x", name="x", menge=2, einheit="l"), a) == 1  # passt nicht
    assert packungen(Bedarf(slug="x", name="x", menge=3, einheit="Dose"), a) == 3
    assert packungen(Bedarf(slug="x", name="x"), a) == 1
