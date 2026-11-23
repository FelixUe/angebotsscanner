from datetime import date

import pytest

from angebotsscanner.config import Config
from angebotsscanner.matching import Urteil, beurteile, ist_ausgeschlossen
from angebotsscanner.modelle import Bedarf, Offer


def angebot(produkt: str, kategorie: str | None, marke: str | None = None, **extra) -> Offer:
    return Offer(
        id=f"test:{produkt}",
        quelle="test",
        markt="EDEKA",
        produkt=produkt,
        marke=marke,
        kategorie=kategorie,
        preis=1.0,
        gueltig_von=date(2026, 11, 23),
        gueltig_bis=date(2026, 11, 28),
        **extra,
    )


BUTTER = Bedarf(slug="butter", name="Butter")
KAFFEE = Bedarf(slug="kaffee", name="Kaffee")
PASSATA = Bedarf(slug="passata", name="Passata")
NUDELN = Bedarf(slug="spaghetti", name="Nudeln", aliases=["Spaghetti"])


@pytest.mark.parametrize(
    ("bedarf", "produkt", "kategorie", "marke", "urteil"),
    [
        (BUTTER, "Weidebutter", "Butter", None, Urteil.SICHER),
        (BUTTER, "Original Irische Butter", "Butter", "Kerrygold", Urteil.SICHER),
        (BUTTER, "mit Butter XXL", "Butter", "Rama", Urteil.NEIN),  # Regel „nicht“
        (BUTTER, "Sonnenblumen-Margarine", "Butter", None, Urteil.NEIN),
        (BUTTER, "Butter Spekulatius", "Kekse", "Bahlsen", Urteil.NEIN),
        (BUTTER, "Butter Brezel", "Backwaren", None, Urteil.UNKLAR),
        (BUTTER, "Reine Buttermilch", "Milch", "Müller", Urteil.NEIN),
        (KAFFEE, "Feine Milde", "Kaffee", "Tchibo", Urteil.SICHER),  # Kategorie = Artikel
        (KAFFEE, "Kaffeepads", "Kaffee", "Senseo", Urteil.NEIN),
        (KAFFEE, "Chilled Coffee", "Kaffee", "Starbucks", Urteil.NEIN),
        (NUDELN, "Collezione", "Nudeln", "Barilla", Urteil.SICHER),
        (NUDELN, "Instandnudeln", "Instantgerichte", "Nongshim", Urteil.NEIN),
        # Synonym im Namen, aber fremde Kategorie
        (PASSATA, "Passierte Tomaten", "Konserven", None, Urteil.UNKLAR),
        (PASSATA, "Passierte Tomaten", None, None, Urteil.SICHER),  # Quelle ohne Kategorie
        (PASSATA, "Rispentomaten", "Tomaten", None, Urteil.NEIN),
    ],
)
def test_beurteile(
    config: Config, bedarf: Bedarf, produkt: str, kategorie: str | None, marke, urteil: Urteil
) -> None:
    assert beurteile(bedarf, angebot(produkt, kategorie, marke), config).urteil is urteil


def test_regel_greift_auch_ueber_den_namen(config: Config) -> None:
    """Der Planer-Slug heißt „spaghetti“, die Regel steht unter „nudeln“."""
    t = beurteile(NUDELN, angebot("Pasta", "Nudeln"), config)
    assert t.urteil is Urteil.SICHER


def test_ausschluesse(config: Config) -> None:
    assert ist_ausgeschlossen(angebot("Gorgonzola", "Käse"), config.ausschluesse)
    assert ist_ausgeschlossen(
        angebot("Käseauswahl", "Käse", beschreibung="mit Blauschimmel"), config.ausschluesse
    )
    assert ist_ausgeschlossen(angebot("Salbeibutter", "Butter"), config.ausschluesse)
    assert ist_ausgeschlossen(angebot("Fenchelknollen", "Gemüse"), config.ausschluesse)
    assert not ist_ausgeschlossen(angebot("Gouda", "Käse"), config.ausschluesse)
