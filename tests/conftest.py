"""Gemeinsame Fixtures. Keine Live-Calls: Marktguru antwortet aus tests/fixtures.

Die Suchantworten stammen aus echten Abfragen (Woche 23.–28.11.2026),
gekürzt auf die genutzten Felder und ohne Tracking-URLs. Die Startseite ist
eine Attrappe mit Platzhalter-Schlüsseln.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest
import yaml

from angebotsscanner.config import Config
from angebotsscanner.historie import Historie
from angebotsscanner.http import Abrufer
from angebotsscanner.quellen.marktguru import Marktguru

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parent.parent
#: Montag der Fixture-Woche; Einkaufstag ist dann Freitag, der 27.11.
HEUTE = date(2026, 11, 23)


class FakeMarktguru:
    """Ein MockTransport für Marktguru und Aldi Nord, der mitzählt, was abgefragt wurde."""

    def __init__(self, schluessel_ok: bool = True, kaputt: bool = False) -> None:
        self.anfragen: list[httpx.Request] = []
        self.schluessel_ok = schluessel_ok
        self.kaputt = kaputt
        self.aldi_kaputt = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.anfragen.append(request)
        if request.url.host == "www.aldi-nord.de":
            if self.aldi_kaputt:
                return httpx.Response(403, text="gesperrt")
            html = (FIXTURES / "aldi_nord" / "angebote.html").read_text(encoding="utf-8")
            return httpx.Response(200, text=html)
        if request.url.host == "www.marktguru.de":
            html = (FIXTURES / "marktguru" / "startseite.html").read_text(encoding="utf-8")
            return httpx.Response(200, text=html)
        if self.kaputt:
            return httpx.Response(500, text="kaputt")
        if not self.schluessel_ok or request.headers.get("x-apikey") != "TEST-API-KEY":
            return httpx.Response(401, json={"error": "unauthorized"})
        q = request.url.params["q"].casefold()
        pfad = FIXTURES / "marktguru" / f"suche_{q}.json"
        if not pfad.exists():
            return httpx.Response(200, json={"totalResults": 0, "results": []})
        return httpx.Response(200, text=pfad.read_text(encoding="utf-8"))

    @property
    def suchen(self) -> list[str]:
        return [r.url.params["q"] for r in self.anfragen if r.url.host == "api.marktguru.de"]


@pytest.fixture
def config(tmp_path: Path) -> Config:
    roh = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    roh["standort"]["plz"] = "20095"
    roh["quellen"]["marktguru"]["aktiv"] = True
    roh["datenverzeichnis"] = str(tmp_path / "daten")
    return Config.model_validate(roh)


@pytest.fixture
def fake() -> FakeMarktguru:
    return FakeMarktguru()


@pytest.fixture
def abrufer(fake: FakeMarktguru):
    a = Abrufer("test-agent", None, abstand_s=0, transport=httpx.MockTransport(fake))
    yield a
    a.schliessen()


@pytest.fixture
def marktguru(abrufer: Abrufer, config: Config) -> Marktguru:
    return Marktguru(abrufer, config.maerkte_fuer("marktguru"), config.apps)


@pytest.fixture
def historie(tmp_path: Path):
    h = Historie(tmp_path / "preise.sqlite")
    yield h
    h.schliessen()
