from datetime import date, datetime

import httpx

from angebotsscanner import ha
from angebotsscanner.mealie import MealieBericht
from angebotsscanner.modelle import Art, Ergebnis, Vorschlag


def vorschlag(**kw) -> Vorschlag:
    werte = dict(
        angebot_id="x:1",
        artikel="Gut & Günstig Pasta 500 g",
        slug="nudeln",
        menge=3,
        markt="EDEKA",
        preis=0.59,
        gueltig_von=date(2026, 11, 23),
        gueltig_bis=date(2026, 11, 28),
        grund="Grundeinkauf",
        art=Art.GEPLANT,
        score=1.0,
    )
    return Vorschlag(**(werte | kw))


def test_ersparnis_euro():
    assert ha.ersparnis_euro(vorschlag(regulaerer_preis=0.99)) == 1.2
    assert ha.ersparnis_euro(vorschlag(ersparnis_prozent=50, preis=1.0, menge=1)) == 1.0
    assert ha.ersparnis_euro(vorschlag()) is None


def test_zusammenfassung():
    ergebnis = Ergebnis(
        einkaufstag=date(2026, 11, 27),
        vorschlaege=[
            vorschlag(regulaerer_preis=0.99),
            vorschlag(artikel="Bio Passata 700 g", markt="Aldi Nord", menge=1, preis=0.95),
        ],
        fehler={"marktguru": "HTTP 403"},
    )
    m = ha.zusammenfassung(
        ergebnis, MealieBericht(markiert=["a"], angelegt=["b"]), datetime(2026, 11, 22, 19, 3)
    )
    assert m["anzahl"] == 2 and m["neu"] == 2
    assert m["ersparnis_euro"] == 1.2
    assert m["maerkte"] == {"Aldi Nord": 1, "EDEKA": 1}
    assert m["text"].splitlines()[0] == (
        "**2 Angebote** für den Einkauf am 27.11., rund 1,20 € gespart"
    )
    assert "- Bio Passata 700 g: Aldi Nord 0,95 € bis Sa 28.11." in m["text"]
    assert "⚠️ Ausgefallen: marktguru" in m["text"]


def test_melde_schluckt_fehler(caplog):
    def kaputt(request):
        return httpx.Response(500)

    def weg(request):
        raise httpx.ConnectError("weg")

    url = "http://ha/api/webhook/geheime-id"
    assert not ha.melde(url, {"status": "ok"}, httpx.MockTransport(kaputt))
    assert not ha.melde(url, {"status": "ok"}, httpx.MockTransport(weg))
    assert not ha.melde(None, {"status": "ok"})
    assert "geheime-id" not in caplog.text
    assert ha.melde(url, {"status": "ok"}, httpx.MockTransport(lambda r: httpx.Response(200)))
