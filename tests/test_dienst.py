from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from angebotsscanner.config import Betrieb
from angebotsscanner.dienst import Zustand, gesund, letzter_termin, parse_lauf, takt

B = ZoneInfo("Europe/Berlin")
BETRIEB = Betrieb()  # So + Mi 19:00, 12 h nachholen, 3 Versuche à 30 min


def zeit(tag: int, stunde: int, minute: int = 0) -> datetime:
    # 22.11.2026 ist ein Sonntag
    return datetime(2026, 11, tag, stunde, minute, tzinfo=B)


def test_parse_lauf():
    assert parse_lauf("sonntag 19:00") == (6, 19, 0)
    assert parse_lauf("Mi 7:30") == (2, 7, 30)
    for falsch in ["sonntag", "xy 19:00", "so 25:00", "m 19:00"]:
        with pytest.raises(ValueError):
            parse_lauf(falsch)


def test_letzter_termin():
    laeufe = BETRIEB.laeufe
    assert letzter_termin(zeit(22, 18, 59), laeufe) == zeit(18, 19)
    assert letzter_termin(zeit(22, 19), laeufe) == zeit(22, 19)
    assert letzter_termin(zeit(24, 12), laeufe) == zeit(22, 19)
    assert letzter_termin(zeit(25, 20), laeufe) == zeit(25, 19)


def test_laeuft_einmal_je_termin(tmp_path):
    pfad = tmp_path / "dienst.json"
    laeufe = []
    lauf = lambda: laeufe.append(1) or 0  # noqa: E731
    takt(zeit(22, 18, 59), BETRIEB, pfad, lauf)
    assert not laeufe  # Mittwoch davor liegt mehr als 12 h zurück
    takt(zeit(22, 19), BETRIEB, pfad, lauf)
    takt(zeit(22, 19, 1), BETRIEB, pfad, lauf)
    takt(zeit(23, 6), BETRIEB, pfad, lauf)
    assert len(laeufe) == 1
    takt(zeit(25, 19), BETRIEB, pfad, lauf)
    assert len(laeufe) == 2
    assert Zustand.lade(pfad).ergebnis == "ok"


def test_holt_verpassten_lauf_nach(tmp_path):
    pfad = tmp_path / "dienst.json"
    laeufe = []
    takt(zeit(23, 6), BETRIEB, pfad, lambda: laeufe.append(1) or 0)
    assert laeufe == [1]
    takt(zeit(23, 8), BETRIEB, tmp_path / "anders.json", lambda: laeufe.append(2) or 0)
    assert laeufe == [1]  # 13 h nach dem Termin: zu spät


def test_wiederholt_fehler_mit_abstand_und_grenze(tmp_path):
    pfad = tmp_path / "dienst.json"
    versuche = []

    def lauf():
        versuche.append(1)
        raise RuntimeError("Mealie weg")

    t = zeit(22, 19)
    for minute in range(0, 200, 1):
        takt(t + timedelta(minutes=minute), BETRIEB, pfad, lauf)
    assert len(versuche) == 3
    ok, text = gesund(BETRIEB, pfad, t + timedelta(minutes=200))
    assert not ok and "3×" in text


def test_gesund(tmp_path):
    pfad = tmp_path / "dienst.json"
    assert not gesund(BETRIEB, pfad, zeit(22, 12))[0]
    takt(zeit(22, 12), BETRIEB, pfad, lambda: 0)
    assert gesund(BETRIEB, pfad, zeit(22, 12, 4))[0]
    assert not gesund(BETRIEB, pfad, zeit(22, 12, 10))[0]
    # ein Fehler allein macht noch nicht ungesund, es kommt ja ein neuer Versuch
    takt(zeit(22, 19), BETRIEB, pfad, lambda: 1)
    assert gesund(BETRIEB, pfad, zeit(22, 19, 1))[0]


def test_plz_und_daten_aus_der_umgebung(monkeypatch, tmp_path):
    from pathlib import Path

    from angebotsscanner.config import lade_config

    monkeypatch.setenv("ANGEBOTSSCANNER_PLZ", "20095")
    monkeypatch.setenv("ANGEBOTSSCANNER_DATEN", str(tmp_path))
    config = lade_config(Path(__file__).parents[1] / "config.example.yaml")
    assert config.standort.plz == "20095"
    assert config.datenverzeichnis == tmp_path
    assert config.betrieb.laeufe == ["sonntag 19:00", "mittwoch 19:00"]
