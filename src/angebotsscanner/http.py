"""Höflicher HTTP-Zugriff: fester User-Agent, Mindestabstand, Cache.

Jede Quelle holt ihre Daten hierüber. Antworten liegen für
``cache_stunden`` in SQLite, damit ein zweiter Lauf am selben Abend die
Märkte nicht noch einmal belastet.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)


class Abrufer:
    def __init__(
        self,
        user_agent: str,
        cache_pfad: Path | None,
        cache_stunden: float = 6.0,
        timeout_s: float = 20.0,
        abstand_s: float = 2.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "de-DE"},
            timeout=timeout_s,
            follow_redirects=True,
            transport=transport,
        )
        self._abstand = abstand_s
        self._letzter = 0.0
        self._ttl = cache_stunden * 3600
        self._db: sqlite3.Connection | None = None
        if cache_pfad is not None:
            cache_pfad.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(cache_pfad)
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS cache "
                "(schluessel TEXT PRIMARY KEY, zeit REAL, inhalt TEXT)"
            )

    def _aus_cache(self, schluessel: str) -> str | None:
        if self._db is None or self._ttl <= 0:
            return None
        zeile = self._db.execute(
            "SELECT zeit, inhalt FROM cache WHERE schluessel = ?", (schluessel,)
        ).fetchone()
        if zeile and time.time() - zeile[0] < self._ttl:
            return zeile[1]
        return None

    def _in_cache(self, schluessel: str, inhalt: str) -> None:
        if self._db is None:
            return
        self._db.execute(
            "INSERT OR REPLACE INTO cache VALUES (?, ?, ?)", (schluessel, time.time(), inhalt)
        )
        self._db.commit()

    def verwerfe(self, url: str) -> None:
        if self._db is not None:
            self._db.execute("DELETE FROM cache WHERE schluessel LIKE ?", (url + "%",))
            self._db.commit()

    def get(
        self,
        url: str,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        cache: bool = True,
    ) -> str:
        """GET mit Cache. Wirft ``httpx.HTTPError`` bei Netz- oder Statusfehlern."""
        anfrage = self._client.build_request("GET", url, params=params, headers=headers)
        schluessel = str(anfrage.url)
        if cache and (treffer := self._aus_cache(schluessel)) is not None:
            return treffer
        warten = self._abstand - (time.monotonic() - self._letzter)
        if warten > 0:
            time.sleep(warten)
        try:
            antwort = self._client.send(anfrage)
        finally:
            self._letzter = time.monotonic()
        log.debug("GET %s → %s", anfrage.url.copy_with(query=None), antwort.status_code)
        antwort.raise_for_status()
        if cache:
            self._in_cache(schluessel, antwort.text)
        return antwort.text

    def schliessen(self) -> None:
        self._client.close()
        if self._db is not None:
            self._db.close()
