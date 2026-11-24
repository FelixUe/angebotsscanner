import json
import threading
import urllib.error
import urllib.request

import pytest

from angebotsscanner.ausloeser import Ausloeser
from angebotsscanner.dienst import auftrag_ausfuehren

TOKEN = "geheim-123"


@pytest.fixture
def ausloeser():
    a = Ausloeser(TOKEN, port=0, host="127.0.0.1")
    a.starten()
    yield a
    a.stoppen()


def anfrage(a: Ausloeser, token: str | None, pfad: str = "/lauf"):
    req = urllib.request.Request(f"http://127.0.0.1:{a.port}{pfad}", method="POST", data=b"")
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_ohne_token_kein_server():
    with pytest.raises(ValueError):
        Ausloeser("")


@pytest.mark.parametrize("token", [None, "falsch", ""])
def test_falscher_token_abgewiesen(ausloeser, token):
    assert anfrage(ausloeser, token)[0] == 401
    assert ausloeser.auftraege.empty()


def test_unbekannter_pfad(ausloeser):
    assert anfrage(ausloeser, TOKEN, "/anderes")[0] == 404


def test_lauf_auf_zuruf_liefert_zusammenfassung(ausloeser):
    antwort = {}
    t = threading.Thread(target=lambda: antwort.update(r=anfrage(ausloeser, TOKEN)))
    t.start()
    auftrag = ausloeser.warte(5)
    assert auftrag is not None
    auftrag_ausfuehren(auftrag, lambda: (0, {"status": "ok", "anzahl": 6}))
    t.join(5)
    assert antwort["r"] == (200, {"code": 0, "zusammenfassung": {"status": "ok", "anzahl": 6}})


def test_abbruch_im_lauf_meldet_fehler(ausloeser):
    antwort = {}
    t = threading.Thread(target=lambda: antwort.update(r=anfrage(ausloeser, TOKEN)))
    t.start()
    auftrag = ausloeser.warte(5)

    def kaputt():
        raise RuntimeError("weg")

    auftrag_ausfuehren(auftrag, kaputt)
    t.join(5)
    assert antwort["r"] == (500, {"code": 1, "zusammenfassung": None})


def test_warte_ohne_auftrag(ausloeser):
    assert ausloeser.warte(0.01) is None
