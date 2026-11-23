"""Kommandozeile.

    angebotsscanner scan --bedarf bedarf.json      # Vorschläge als JSON
    angebotsscanner mealie [--dry-run]             # Bedarf aus Mealie, Angebote hinein
    angebotsscanner angebote butter kaffee         # normalisierte Angebote ansehen

Logs gehen nach stderr, das Ergebnis nach stdout.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

from pydantic import TypeAdapter

from . import __version__
from .config import Config, lade_config, lade_vorrat
from .historie import Historie
from .http import Abrufer
from .mealie import MealieFehler, MealieListe, bedarf_aus_liste
from .modelle import Bedarf
from .quellen import OfferSource
from .quellen.aldi_nord import AldiNord
from .quellen.marktguru import Marktguru
from .scanner import naechster_einkaufstag, scanne, vorrat_als_bedarf

log = logging.getLogger("angebotsscanner")


def baue_quellen(config: Config, abrufer: Abrufer) -> list[OfferSource]:
    quellen: list[OfferSource] = []
    mg = config.quelle("marktguru")
    if mg.aktiv and config.maerkte_fuer("marktguru"):
        quellen.append(
            Marktguru(abrufer, config.maerkte_fuer("marktguru"), config.apps, mg.max_begriffe)
        )
    aldi = config.quelle("aldi_nord")
    for markt in config.maerkte_fuer("aldi_nord"):
        if aldi.aktiv:
            quellen.append(AldiNord(abrufer, markt.name, aldi.ohne_gruppen))
    bekannt = {"marktguru", "aldi_nord"}
    for m in config.maerkte:
        if m.quelle not in bekannt:
            log.warning("Markt %s: Quelle %s gibt es (noch) nicht", m.name, m.quelle)
    if not quellen:
        log.warning("Keine Quelle aktiv – in config.yaml unter quellen: aktiv setzen")
    return quellen


def _abrufer(config: Config) -> Abrufer:
    return Abrufer(
        config.http.user_agent,
        config.datenverzeichnis / "cache.sqlite",
        cache_stunden=config.http.cache_stunden,
        timeout_s=config.http.timeout_s,
        abstand_s=config.quelle("marktguru").abstand_s,
    )


def _lade_bedarf(pfad: Path | None) -> list[Bedarf]:
    if pfad is None:
        return []
    return TypeAdapter(list[Bedarf]).validate_json(pfad.read_bytes())


def cmd_scan(args: argparse.Namespace) -> int:
    config = lade_config(args.config)
    bedarf = _lade_bedarf(args.bedarf) + vorrat_als_bedarf(lade_vorrat(args.vorrat))
    abrufer = _abrufer(config)
    historie = Historie(config.datenverzeichnis / "preise.sqlite")
    try:
        ergebnis = scanne(bedarf, baue_quellen(config, abrufer), config, historie, args.heute)
    finally:
        abrufer.schliessen()
        historie.schliessen()
    print(ergebnis.model_dump_json(indent=2))
    return 1 if ergebnis.fehler and not ergebnis.vorschlaege else 0


def cmd_mealie(args: argparse.Namespace) -> int:
    """Bedarf aus der Mealie-Liste lesen, Angebote suchen und hineinschreiben."""
    config = lade_config(args.config)
    url = os.environ.get("MEALIE_URL", "").strip()
    token = os.environ.get("MEALIE_TOKEN", "").strip()
    if not url or not token:
        log.error("MEALIE_URL und MEALIE_TOKEN müssen gesetzt sein")
        return 2
    liste = MealieListe(url, token, config.mealie.liste, config.mealie.label)
    abrufer = _abrufer(config)
    historie = Historie(config.datenverzeichnis / "preise.sqlite")
    try:
        items = liste.items()
        tag = naechster_einkaufstag(args.heute, config.einkauf.wochentag_index)
        zeilen = bedarf_aus_liste(items, tag)
        if not zeilen:
            log.warning("Mealie: keine offenen Zeilen des Essensplaners für den %s", tag)
        bedarf = [z.bedarf for z in zeilen] + vorrat_als_bedarf(lade_vorrat(args.vorrat))
        quellen = baue_quellen(config, abrufer)
        ergebnis = scanne(bedarf, quellen, config, historie, args.heute)
        if quellen and len(ergebnis.fehler) == len(quellen):
            log.error("Alle Quellen ausgefallen – die Liste bleibt, wie sie ist")
            return 1
        ausgefallen = {m.name for q in ergebnis.fehler for m in config.maerkte_fuer(q)}
        bericht = liste.schreibe(
            ergebnis, zeilen, items, args.heute, ausgefallen, dry_run=args.dry_run
        )
    except MealieFehler as exc:
        log.error("%s", exc)
        return 1
    finally:
        abrufer.schliessen()
        historie.schliessen()
        liste.schliessen()

    vorsilbe = "Würde " if args.dry_run else ""
    print(f"Einkauf {tag}: {len(zeilen)} Zeilen gelesen, {len(ergebnis.vorschlaege)} Angebote")
    for titel, eintraege in (
        ("markieren", bericht.markiert),
        ("neu anlegen", bericht.angelegt),
        ("Hinweis entfernen", bericht.entfernt),
        ("löschen", bericht.geloescht),
    ):
        print(f"{vorsilbe}{titel}: {len(eintraege)}")
        for e in eintraege:
            print(f"  {e}")
    if bericht.behalten:
        print(f"Behalten (Quelle ausgefallen): {len(bericht.behalten)}")
    for quelle, fehler in ergebnis.fehler.items():
        print(f"Quelle {quelle} ausgefallen: {fehler}")
    return 0


def cmd_angebote(args: argparse.Namespace) -> int:
    config = lade_config(args.config)
    abrufer = _abrufer(config)
    try:
        angebote = [
            a
            for q in baue_quellen(config, abrufer)
            for a in q.fetch(config.standort.plz, args.begriffe)
        ]
    finally:
        abrufer.schliessen()
    print(json.dumps([a.model_dump(mode="json") for a in angebote], ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="angebotsscanner")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", type=Path, help="Pfad zur config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="befehl", required=True)

    scan = sub.add_parser("scan", help="Angebote mit Bedarf und Vorrat abgleichen")
    scan.add_argument("--bedarf", type=Path, help="JSON-Liste der geplanten Zutaten")
    scan.add_argument("--vorrat", type=Path, help="Pfad zur vorrat.yaml")
    scan.add_argument("--heute", type=date.fromisoformat, default=date.today())
    scan.set_defaults(fn=cmd_scan)

    mea = sub.add_parser("mealie", help="Bedarf aus Mealie lesen, Angebote hineinschreiben")
    mea.add_argument("--vorrat", type=Path, help="Pfad zur vorrat.yaml")
    mea.add_argument("--heute", type=date.fromisoformat, default=date.today())
    mea.add_argument("--dry-run", action="store_true", help="Nur anzeigen, nichts schreiben")
    mea.set_defaults(fn=cmd_mealie)

    ang = sub.add_parser("angebote", help="Normalisierte Angebote zu Suchbegriffen")
    ang.add_argument("begriffe", nargs="+")
    ang.set_defaults(fn=cmd_angebote)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # httpx loggt jede URL samt PLZ; das gehört nur in die Fehlersuche
    logging.getLogger("httpx").setLevel(logging.DEBUG if args.verbose else logging.WARNING)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
