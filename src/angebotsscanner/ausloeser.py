"""Lauf auf Zuruf: ein kleiner HTTP-Endpunkt neben dem Zeitplan.

Der Essensplaner ruft ``POST /lauf`` auf, sobald der Einkaufszettel fertig
in Mealie steht, und bekommt nach dem Lauf die Zusammenfassung zurück (wie
sie auch an Home Assistant geht). So stehen die Angebote am Zettel, ohne
auf den nächsten geplanten Lauf zu warten.

Sicherheit, pragmatisch fürs Heimnetz:

* Nur aktiv, wenn ``ANGEBOTSSCANNER_AUSLOESER_TOKEN`` gesetzt ist; ohne
  Token startet kein Server.
* Der Port wird nicht veröffentlicht, nur das Docker-Netz erreicht ihn.
* Jede Anfrage braucht ``Authorization: Bearer <Token>`` (zeitkonstant
  verglichen). Der Token erscheint nie im Log.
* Läufe werden nie parallel ausgeführt: Der Handler legt einen Auftrag in
  die Warteschlange, die Hauptschleife des Dienstes arbeitet ihn ab.
"""

from __future__ import annotations

import hmac
import json
import logging
import queue
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger(__name__)

PORT = 8099
#: So lange wartet die Anfrage auf das Ende des Laufs.
WARTEN_S = 15 * 60


@dataclass
class Auftrag:
    fertig: threading.Event = field(default_factory=threading.Event)
    code: int | None = None
    zusammenfassung: dict | None = None


class Ausloeser:
    """Nimmt Aufträge über HTTP an; abgearbeitet werden sie in ``abarbeiten``."""

    def __init__(self, token: str, port: int = PORT, host: str = "0.0.0.0") -> None:
        if not token:
            raise ValueError("Token fehlt")
        self.token = token.encode()
        self.auftraege: queue.Queue[Auftrag] = queue.Queue(maxsize=3)
        self.server = ThreadingHTTPServer((host, port), self._handler())
        self.server.daemon_threads = True

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def starten(self) -> None:
        threading.Thread(target=self.server.serve_forever, name="ausloeser", daemon=True).start()
        log.info("Auslöser hört auf Port %d", self.port)

    def stoppen(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def warte(self, sekunden: float) -> Auftrag | None:
        """Bis zu ``sekunden`` auf einen Auftrag warten (ersetzt das Schlafen)."""
        try:
            return self.auftraege.get(timeout=sekunden)
        except queue.Empty:
            return None

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        ausloeser = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "angebotsscanner"

            def log_message(self, fmt: str, *args: object) -> None:  # kein Zugriffslog
                return

            def _antwort(self, status: int, daten: dict) -> None:
                roh = json.dumps(daten, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(roh)))
                self.end_headers()
                self.wfile.write(roh)

            def do_GET(self) -> None:  # noqa: N802
                self._antwort(404, {"fehler": "unbekannt"})

            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/lauf":
                    self._antwort(404, {"fehler": "unbekannt"})
                    return
                kopf = self.headers.get("Authorization", "")
                gegeben = kopf.removeprefix("Bearer ").strip().encode()
                if not kopf.startswith("Bearer ") or not hmac.compare_digest(
                    gegeben, ausloeser.token
                ):
                    log.warning("Auslöser: Anfrage ohne gültigen Token abgewiesen")
                    self._antwort(401, {"fehler": "nicht berechtigt"})
                    return
                auftrag = Auftrag()
                try:
                    ausloeser.auftraege.put_nowait(auftrag)
                except queue.Full:
                    self._antwort(429, {"fehler": "es warten schon Läufe"})
                    return
                log.info("Auslöser: Lauf angefordert")
                if not auftrag.fertig.wait(WARTEN_S):
                    self._antwort(504, {"fehler": "Lauf dauert zu lange"})
                    return
                self._antwort(
                    200 if auftrag.code == 0 else 500,
                    {"code": auftrag.code, "zusammenfassung": auftrag.zusammenfassung},
                )

        return Handler
