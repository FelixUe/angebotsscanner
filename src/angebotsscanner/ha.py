"""Zusammenfassung eines Laufs an Home Assistant.

Der Scanner schickt nach jedem Lauf ein JSON an einen Webhook von Home
Assistant (``HA_WEBHOOK_URL``, z. B. ``http://172.30.0.1:8123/api/webhook/…``).
Ein Webhook braucht kein Token; Home Assistant nimmt ihn standardmäßig nur
aus dem lokalen Netz an. Was HA daraus macht – Sensor, Benachrichtigung,
Dashboard –, steht in der HA-Konfiguration, nicht hier.

Die Meldung ist eine Zugabe: Ist HA nicht erreichbar, steht das im Log, der
Lauf gilt trotzdem als erfolgreich.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

import httpx

from .mealie import MealieBericht, hinweis
from .modelle import Ergebnis, Vorschlag

log = logging.getLogger(__name__)


def ersparnis_euro(v: Vorschlag) -> float | None:
    """Ungefähre Ersparnis in Euro für die vorgeschlagene Menge."""
    if v.regulaerer_preis and v.regulaerer_preis > v.preis:
        return round((v.regulaerer_preis - v.preis) * v.menge, 2)
    p = v.ersparnis_prozent
    if p is not None and 0 < p < 100:
        return round(v.preis * p / (100 - p) * v.menge, 2)
    return None


def _euro(wert: float) -> str:
    return f"{wert:.2f} €".replace(".", ",")


def zusammenfassung(
    ergebnis: Ergebnis, bericht: MealieBericht, jetzt: datetime | None = None
) -> dict:
    """Die Meldung an HA: Zahlen für den Sensor, Liste und Text für die Karte."""
    jetzt = jetzt or datetime.now().astimezone()
    vorschlaege = sorted(ergebnis.vorschlaege, key=lambda v: (v.markt, v.artikel))
    summe = round(sum(e for v in vorschlaege if (e := ersparnis_euro(v))), 2)
    nach_markt: dict[str, int] = {}
    for v in vorschlaege:
        nach_markt[v.markt] = nach_markt.get(v.markt, 0) + 1

    zeilen = [f"- {hinweis(v).removeprefix('🏷 ')}" for v in vorschlaege]
    kopf = f"**{len(vorschlaege)} Angebote** für den Einkauf am {ergebnis.einkaufstag:%d.%m.}" + (
        f", rund {_euro(summe)} gespart" if summe else ""
    )
    text = "\n".join([kopf, "", *zeilen])
    if ergebnis.fehler:
        text += "\n\n⚠️ Ausgefallen: " + ", ".join(sorted(ergebnis.fehler))

    return {
        "status": "ok",
        "zeit": jetzt.isoformat(timespec="seconds"),
        "einkaufstag": ergebnis.einkaufstag.isoformat(),
        "anzahl": len(vorschlaege),
        "neu": len(bericht.markiert) + len(bericht.angelegt),
        "ersparnis_euro": summe,
        "maerkte": nach_markt,
        "fehler": dict(ergebnis.fehler),
        "angebote": [
            {
                "artikel": v.artikel,
                "menge": v.menge,
                "markt": v.markt,
                "preis": v.preis,
                "ersparnis_prozent": v.ersparnis_prozent,
                "bis": v.gueltig_bis.isoformat(),
                "fuer": v.grund,
            }
            for v in vorschlaege
        ],
        "text": text,
    }


def fehlermeldung(meldung: str, einkaufstag: date | None = None) -> dict:
    return {
        "status": "fehler",
        "zeit": datetime.now().astimezone().isoformat(timespec="seconds"),
        "einkaufstag": einkaufstag.isoformat() if einkaufstag else None,
        "meldung": meldung,
        "text": f"⚠️ Angebotsscanner: {meldung}",
    }


def melde(url: str | None, daten: dict, transport: httpx.BaseTransport | None = None) -> bool:
    """Schickt die Meldung; Fehler landen im Log, nie als Ausnahme."""
    if not url:
        return False
    try:
        with httpx.Client(timeout=10, transport=transport) as client:
            client.post(url, json=daten).raise_for_status()
    except httpx.HTTPStatusError as exc:
        # Ohne URL im Log: Die Webhook-ID ist das einzige Geheimnis
        log.warning("Home Assistant: HTTP %d", exc.response.status_code)
        return False
    except httpx.HTTPError as exc:
        log.warning("Home Assistant nicht erreichbar: %s", type(exc).__name__)
        return False
    log.info("Home Assistant: Zusammenfassung gemeldet (%s)", daten.get("status"))
    return True
