"""Textnormalisierung und Wortvergleich für deutsche Produktnamen.

Prospekttexte sind uneinheitlich: Umlaute mal ausgeschrieben, Komposita
mal zusammen, mal mit Bindestrich, Plural mal ja, mal nein. Hier wird
alles auf eine Form gebracht, gegen die sich Regeln schreiben lassen.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

#: „Kærgården“ heißt im deutschen Handel „Kaergarden“, daher å → a
_UMLAUTE = str.maketrans(
    {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "ß": "ss",
        "é": "e",
        "è": "e",
        "æ": "ae",
        "ø": "oe",
        "å": "a",
    }
)
_NICHT_ALNUM = re.compile(r"[^a-z0-9]+")
#: Ab dieser Ähnlichkeit (0–100) gelten zwei Wörter als gleich (Tippfehler, Plural).
AEHNLICH = 90


def normalisiere(wert: str | None) -> str:
    if not wert:
        return ""
    klein = wert.casefold().translate(_UMLAUTE)
    return " ".join(_NICHT_ALNUM.sub(" ", klein).split())


def woerter(wert: str | None) -> list[str]:
    return normalisiere(wert).split()


def slug(wert: str) -> str:
    return normalisiere(wert).replace(" ", "_")


def stamm(wort: str) -> str:
    """Grobe Pluralkappung: Tomaten/Tomate → tomat, Bohnen → bohn."""
    for endung in ("en", "n", "e", "s"):
        if len(wort) > 4 and wort.endswith(endung):
            return wort[: -len(endung)]
    return wort


def _wort_passt(wort: str, begriff: str) -> bool:
    """Ein Wort des Produkts trifft einen einwortigen Begriff.

    Gilt bei Gleichheit, als Grundwort eines Kompositums (Rispen*tomaten*,
    Weide*butter*) oder bei hoher Ähnlichkeit. Ein Wortanfang zählt nicht:
    *Butter*käse ist keine Butter, *Tomaten*mark keine Tomate.
    """
    w, b = stamm(wort), stamm(begriff)
    if w == b:
        return True
    if len(w) - len(b) >= 3 and w.endswith(b):
        return True
    return len(b) >= 4 and fuzz.ratio(w, b) >= AEHNLICH


def enthaelt_begriff(produkt: str, begriff: str) -> bool:
    """Kommt der Begriff (ein oder mehrere Wörter) im Produkttext vor?

    Mehrwortige Begriffe müssen als zusammenhängende Wortfolge vorkommen,
    das letzte Wort darf dabei Grundwort eines Kompositums sein.
    """
    p = woerter(produkt)
    b = woerter(begriff)
    if not p or not b:
        return False
    if len(b) == 1:
        return any(_wort_passt(w, b[0]) for w in p)
    for i in range(len(p) - len(b) + 1):
        fenster = p[i : i + len(b)]
        if all(stamm(x) == stamm(y) for x, y in zip(fenster[:-1], b[:-1], strict=True)) and (
            _wort_passt(fenster[-1], b[-1])
        ):
            return True
    return False


def enthaelt_teil(text: str, teil: str) -> bool:
    """Teilstring nach Normalisierung – für Ausschlüsse (Salbei*butter*)."""
    t = normalisiere(teil)
    return bool(t) and t in normalisiere(text)
