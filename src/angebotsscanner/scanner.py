"""Ein Lauf: Bedarf sammeln, Angebote holen, zuordnen, bewerten.

Fällt eine Quelle aus, wird der Fehler geloggt und im Ergebnis vermerkt;
die übrigen Quellen laufen weiter.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, timedelta

from . import matching
from .bewertung import Bewertet, bestes_je_bedarf, bewerte
from .config import Config, Vorrat
from .historie import Historie
from .modelle import Art, Bedarf, Ergebnis, Offer, Unklar, Vorschlag
from .quellen import OfferSource, QuelleFehler

log = logging.getLogger(__name__)

_ART_RANG = {Art.GEPLANT: 0, Art.VORRAT: 1, Art.NOTVORRAT: 2}


def naechster_einkaufstag(heute: date, wochentag: int) -> date:
    return heute + timedelta(days=(wochentag - heute.weekday()) % 7)


def vorrat_als_bedarf(vorrat: Vorrat) -> list[Bedarf]:
    bedarfe = []
    for a in vorrat.artikel:
        if a.bestand is not None and a.bestand >= a.zielmenge:
            continue
        if a.bestand is not None:
            menge = a.zielmenge - a.bestand
        else:
            # Bestand unbekannt: so viel, wie zwischen Minimum und Ziel liegt
            menge = max(a.zielmenge - a.mindestbestand, 1)
        bedarfe.append(
            Bedarf(
                slug=a.slug,
                name=a.name,
                aliases=a.aliases,
                art=Art.NOTVORRAT if a.notvorrat else Art.VORRAT,
                frisch=not a.haltbar,
                gericht="Notvorrat" if a.notvorrat else "Vorrat",
                menge=menge,
                einheit=a.einheit,
                knapp=a.bestand is not None and a.bestand < a.mindestbestand,
                referenz_grundpreis=a.referenz_grundpreis,
            )
        )
    return bedarfe


def zusammenfassen(bedarfe: Sequence[Bedarf], vorlauf_wochen: int) -> list[Bedarf]:
    """Gleiche Artikel zusammenlegen; Frischware späterer Wochen fällt weg."""
    gesammelt: dict[str, Bedarf] = {}
    for b in bedarfe:
        if b.woche >= vorlauf_wochen or (b.frisch and b.woche > 0):
            continue
        alt = gesammelt.get(b.slug)
        if alt is None:
            gesammelt[b.slug] = b.model_copy()
            continue
        menge = alt.menge
        if alt.menge is not None and b.menge is not None and alt.einheit == b.einheit:
            menge = alt.menge + b.menge
        gerichte = [g for g in (alt.gericht, b.gericht) if g]
        gesammelt[b.slug] = alt.model_copy(
            update={
                "aliases": list(dict.fromkeys([*alt.aliases, *b.aliases])),
                "art": min(alt.art, b.art, key=_ART_RANG.__getitem__),
                "frisch": alt.frisch or b.frisch,
                "woche": min(alt.woche, b.woche),
                "menge": menge,
                "gericht": " + ".join(dict.fromkeys(gerichte)) or None,
                "knapp": alt.knapp or b.knapp,
            }
        )
    return list(gesammelt.values())


def _vorfiltern(angebote: list[Offer], tag: date, config: Config) -> list[Offer]:
    apps = {a.casefold() for a in config.apps}
    gesehen: set[tuple] = set()
    ergebnis = []
    for a in angebote:
        if not a.gilt_am(tag):
            continue
        if a.app_pflicht and a.app_pflicht not in apps:
            continue
        if matching.ist_ausgeschlossen(a, config.ausschluesse):
            continue
        # Regionale Doppel (EDEKA und EDEKA Frischemarkt mit demselben Angebot)
        doppel = (a.markt, a.marke, a.produkt.casefold(), a.preis, a.grundpreis)
        if doppel in gesehen:
            continue
        gesehen.add(doppel)
        ergebnis.append(a)
    return ergebnis


def _als_vorschlag(gruppe: list[Bewertet]) -> Vorschlag:
    """Mehrere Bedarfe, die auf dasselbe Angebot fallen, werden ein Eintrag."""
    gruppe = sorted(gruppe, key=lambda x: x.score, reverse=True)
    top = gruppe[0]
    a = top.treffer.angebot
    # Die aussagekräftigste Ersparnis der Gruppe (geplante Zutaten haben oft keine)
    e = max(
        (x.ersparnis for x in gruppe), key=lambda e: e.prozent if e.prozent is not None else -1e9
    )
    gruende = dict.fromkeys(x.treffer.bedarf.gericht or x.treffer.bedarf.name for x in gruppe)
    return Vorschlag(
        angebot_id=a.id,
        artikel=a.anzeigename,
        slug=top.treffer.bedarf.slug,
        menge=min(sum(x.menge for x in gruppe), 10),
        markt=a.markt,
        preis=a.preis,
        grundpreis=a.grundpreis,
        grundpreis_einheit=a.grundpreis_einheit,
        regulaerer_preis=a.regulaerer_preis,
        ersparnis_prozent=e.prozent,
        ersparnis_basis=e.basis,
        gueltig_von=a.gueltig_von,
        gueltig_bis=a.gueltig_bis,
        grund=" + ".join(gruende),
        art=min((x.treffer.bedarf.art for x in gruppe), key=_ART_RANG.__getitem__),
        quelle_url=a.quelle_url,
        score=top.score,
    )


def scanne(
    bedarfe: Sequence[Bedarf],
    quellen: Sequence[OfferSource],
    config: Config,
    historie: Historie,
    heute: date,
) -> Ergebnis:
    tag = naechster_einkaufstag(heute, config.einkauf.wochentag_index)
    bedarf = zusammenfassen(bedarfe, config.einkauf.vorlauf_wochen)
    suchbegriffe = list(dict.fromkeys(matching.suchbegriff(b, config) for b in bedarf))
    ergebnis = Ergebnis(einkaufstag=tag)

    angebote: list[Offer] = []
    for quelle in quellen:
        try:
            neu = quelle.fetch(config.standort.plz, suchbegriffe)
        except QuelleFehler as exc:
            log.error("Quelle %s ausgefallen: %s", quelle.name, exc)
            ergebnis.fehler[quelle.name] = str(exc)
            continue
        except Exception as exc:  # ein Adapterfehler darf den Lauf nicht beenden
            log.exception("Quelle %s: unerwarteter Fehler", quelle.name)
            ergebnis.fehler[quelle.name] = f"{type(exc).__name__}: {exc}"
            continue
        angebote.extend(neu)

    angebote = _vorfiltern(angebote, tag, config)
    sicher, unklar = matching.zuordnen(bedarf, angebote, config)

    # Historie zuerst lesen (nur frühere Wochen), dann die neuen Treffer erfassen
    bewertete = [x for t in sicher if (x := bewerte(t, historie, config, vor=tag)) is not None]
    for t in sicher:
        historie.erfasse(t.bedarf.slug, t.angebot, heute)
    historie.speichern()

    gruppen: dict[str, list[Bewertet]] = {}
    for x in bestes_je_bedarf(bewertete):
        gruppen.setdefault(x.treffer.angebot.id, []).append(x)
    ergebnis.vorschlaege = sorted(
        (_als_vorschlag(g) for g in gruppen.values()), key=lambda v: (-v.score, v.artikel)
    )
    ergebnis.unklar = [
        Unklar(
            slug=t.bedarf.slug,
            angebot_id=t.angebot.id,
            artikel=t.angebot.anzeigename,
            markt=t.angebot.markt,
            grund=t.grund,
        )
        for t in unklar
    ]
    log.info(
        "Einkauf %s: %d Bedarfe, %d Angebote, %d Vorschläge, %d unklar",
        tag,
        len(bedarf),
        len(angebote),
        len(ergebnis.vorschlaege),
        len(ergebnis.unklar),
    )
    return ergebnis
