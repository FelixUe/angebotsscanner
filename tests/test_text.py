import pytest

from angebotsscanner import text


@pytest.mark.parametrize(
    ("produkt", "begriff", "erwartet"),
    [
        ("Original Irische Butter", "Butter", True),
        ("Weidebutter", "Butter", True),  # Grundwort eines Kompositums
        ("Butterkäse", "Butter", False),  # Wortanfang zählt nicht
        ("Rispentomaten", "Tomaten", True),
        ("Mini-Pflaumentomaten", "Tomate", True),  # Plural, Bindestrich
        ("Tomatenkonzentrat", "Tomaten", False),
        ("Haltbare Landmilch", "Milch", True),
        ("Kaffeepads", "Kaffee", False),
        ("Idee Kaffee Classic", "Kaffee", True),
        ("Passierte Tomaten", "passierte Tomaten", True),
        ("Tomaten passiert", "passierte Tomaten", False),
        ("Käse am Stück", "Kaese", True),
    ],
)
def test_enthaelt_begriff(produkt: str, begriff: str, erwartet: bool) -> None:
    assert text.enthaelt_begriff(produkt, begriff) is erwartet


def test_normalisiere_umlaute_und_zeichen() -> None:
    assert text.normalisiere("Bergkäse, gerieben – 200 g!") == "bergkaese gerieben 200 g"


def test_teilstring_fuer_ausschluesse() -> None:
    assert text.enthaelt_teil("Salbeibutter", "Salbei")
    assert not text.enthaelt_teil("Butter", "Salbei")


def test_skandinavische_zeichen() -> None:
    assert text.normalisiere("Kærgården") == "kaergarden"
