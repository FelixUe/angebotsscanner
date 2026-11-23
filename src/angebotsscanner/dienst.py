"""Dauerbetrieb: der Scanner als Container-Prozess mit eigenem Zeitplan.

``angebotsscanner dienst`` prüft jede Minute, ob ein Lauf aus
``betrieb.laeufe`` fällig ist, und führt dann denselben Lauf aus wie
``angebotsscanner mealie``. Verpasste Läufe (NUC aus, Rollout) werden bis
``nachholen_stunden`` später nachgeholt, fehlgeschlagene bis zu ``versuche``
Mal im Abstand von ``wiederholen_minuten``.

Der Zustand liegt in ``<datenverzeichnis>/dienst.json``: wann der Prozess
zuletzt gelebt hat (Puls), welcher Termin zuletzt bearbeitet wurde und mit
welchem Ergebnis. ``angebotsscanner gesund`` liest ihn für den Docker-
Healthcheck: ungesund, wenn der Puls älter als fünf Minuten ist oder der
letzte Lauf endgültig fehlgeschlagen ist.
"""

from __future__ import annotations

import json
import logging
import signal
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import WOCHENTAGE, Betrieb

log = logging.getLogger(__name__)

PULS_MAX = timedelta(minutes=5)
TAKT_S = 60


@dataclass
class Zustand:
    puls: str | None = None
    termin: str | None = None
    versuche: int = 0
    letzter_lauf: str | None = None
    ergebnis: str | None = None  # "ok" | "fehler"

    @classmethod
    def lade(cls, pfad: Path) -> Zustand:
        try:
            return cls(**json.loads(pfad.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return cls()

    def speichere(self, pfad: Path) -> None:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        tmp = pfad.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(pfad)


def parse_lauf(eintrag: str) -> tuple[int, int, int]:
    """„sonntag 19:00“ / „So 19:00“ → (Wochentag 0–6, Stunde, Minute)."""
    try:
        tag, uhr = eintrag.split()
        stunde, minute = (int(x) for x in uhr.split(":"))
    except ValueError as exc:
        raise ValueError(f"Lauf {eintrag!r}: erwartet „Wochentag HH:MM“") from exc
    tag = tag.casefold().rstrip(".")
    treffer = [i for i, t in enumerate(WOCHENTAGE) if t.startswith(tag[:2])]
    if len(tag) < 2 or len(treffer) != 1 or not (0 <= stunde < 24 and 0 <= minute < 60):
        raise ValueError(f"Lauf {eintrag!r}: erwartet „Wochentag HH:MM“")
    return treffer[0], stunde, minute


def letzter_termin(jetzt: datetime, laeufe: list[str]) -> datetime | None:
    """Der jüngste geplante Termin, der nicht in der Zukunft liegt."""
    termine = []
    for tag, stunde, minute in map(parse_lauf, laeufe):
        t = jetzt.replace(hour=stunde, minute=minute, second=0, microsecond=0)
        t -= timedelta(days=(jetzt.weekday() - tag) % 7)
        if t > jetzt:
            t -= timedelta(days=7)
        termine.append(t)
    return max(termine, default=None)


def faellig(jetzt: datetime, betrieb: Betrieb, zustand: Zustand) -> datetime | None:
    """Der Termin, der jetzt laufen soll – oder None."""
    termin = letzter_termin(jetzt, betrieb.laeufe)
    if termin is None or jetzt - termin > timedelta(hours=betrieb.nachholen_stunden):
        return None
    if zustand.termin != termin.isoformat():
        return termin
    if zustand.ergebnis != "fehler" or zustand.versuche >= betrieb.versuche:
        return None
    zuletzt = datetime.fromisoformat(zustand.letzter_lauf) if zustand.letzter_lauf else None
    if zuletzt is None or jetzt - zuletzt >= timedelta(minutes=betrieb.wiederholen_minuten):
        return termin
    return None


def takt(jetzt: datetime, betrieb: Betrieb, pfad: Path, lauf: Callable[[], int]) -> Zustand:
    """Ein Schlag: Puls schreiben, bei Fälligkeit einmal laufen."""
    zustand = Zustand.lade(pfad)
    zustand.puls = jetzt.isoformat()
    termin = faellig(jetzt, betrieb, zustand)
    if termin is not None:
        if zustand.termin != termin.isoformat():
            zustand.termin, zustand.versuche = termin.isoformat(), 0
        zustand.versuche += 1
        log.info("Lauf für Termin %s (Versuch %d)", termin, zustand.versuche)
        try:
            code = lauf()
        except Exception:
            log.exception("Lauf abgebrochen")
            code = 1
        zustand.ergebnis = "ok" if code == 0 else "fehler"
        zustand.letzter_lauf = jetzt.isoformat()
        if code != 0:
            log.error("Lauf fehlgeschlagen (Code %d)", code)
    zustand.speichere(pfad)
    return zustand


def dienst(betrieb: Betrieb, pfad: Path, lauf: Callable[[], int]) -> int:
    zone = ZoneInfo(betrieb.zeitzone)
    for eintrag in betrieb.laeufe:
        parse_lauf(eintrag)  # Tippfehler sofort melden, nicht erst am Sonntag

    def beenden(signum: int, _frame: object) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, beenden)
    log.info("Dienst läuft: %s (%s)", ", ".join(betrieb.laeufe), betrieb.zeitzone)
    while True:
        takt(datetime.now(zone), betrieb, pfad, lauf)
        time.sleep(TAKT_S)


def gesund(betrieb: Betrieb, pfad: Path, jetzt: datetime | None = None) -> tuple[bool, str]:
    jetzt = jetzt or datetime.now(ZoneInfo(betrieb.zeitzone))
    zustand = Zustand.lade(pfad)
    if not zustand.puls:
        return False, "kein Puls – läuft der Dienst?"
    alter = jetzt - datetime.fromisoformat(zustand.puls)
    if alter > PULS_MAX:
        return False, f"letzter Puls vor {alter}"
    if zustand.ergebnis == "fehler" and zustand.versuche >= betrieb.versuche:
        return False, f"Lauf {zustand.termin} {zustand.versuche}× fehlgeschlagen"
    return True, f"ok, letzter Termin {zustand.termin or '–'}: {zustand.ergebnis or '–'}"
