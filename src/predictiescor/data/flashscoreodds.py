"""Cote pre-meci de la Flashscore.

Golul pe care il umple: football-data publica cotele ODATA cu rezultatul, deci
pentru meciurile viitoare nu aveam practic nimic (2 din 186). Fara cote inainte
de meci nu se poate face nici detectie reala de valoare, nici comparatie cu
piata, nici masurarea fata de linia de inchidere.

Cum am gasit sursa: pagina de meci nu contine cotele, dar cere la randul ei un
endpoint GraphQL cu interogare persistata. Adresa, verificata direct:

    https://global.ds.lsapp.eu/odds/pq_graphql
        ?_hash=ope2&eventId=<id>&bookmakerId=<n>
        &betType=HOME_DRAW_AWAY&betScope=FULL_TIME

Raspunde 200 fara niciun antet special - nici token, nici Referer. Intoarce si
cota curenta, si **cota de deschidere**, ceea ce pe viitor permite masurarea
fata de linia de inchidere.

Masurat pe 31 august 2026:

    meciuri viitoare cu cote     9 din 10  (cel ratat era peste 12 zile)
    meciuri deja jucate          8 din 8   (deci se poate completa si in urma)
    case de pariuri              5         Betano, Unibet, Superbet, MrBit, Getsbet
    marja pe casa                0.9% - 4.9%   (football-data avea 8.4%)
    piete                        1X2, peste/sub (7 linii), ambele marcheaza,
                                 sansa dubla, handicap asiatic, scor corect

DOUA PRECAUTII IMPORTANTE:

1. `_hash` e identificatorul unei interogari persistate si **se poate schimba
   cand Flashscore isi actualizeaza site-ul**. Incercam mai multe variante
   cunoscute; daca toate cad, ridicam o eroare explicita in loc sa scriem
   tacut zero randuri.

2. Nu scriem peste cotele existente. football-data foloseste deja numele `avg`
   si `max`, iar pe ele se sprijina backtestul; agregatele noastre se numesc
   `fs_avg` si `fs_max`, iar fiecare casa isi pastreaza numele ei.

Termeni de utilizare: cotele sunt afisate public pe pagina meciului. Endpointul
nu e documentat, iar conditiile Flashscore nu permit explicit accesul automat.
Cererile se fac rar, cu pauza intre ele, si sursa se poate opri complet din
`.env` (`FLASHSCORE_ODDS=false`).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlencode

log = logging.getLogger(__name__)

ODDS_URL = "https://global.ds.lsapp.eu/odds/pq_graphql"

#: Identificatorii interogarilor persistate. Primul e cel verificat; ceilalti
#: raman ca plasa de siguranta daca site-ul se actualizeaza.
ODDS_HASHES = ("ope2",)
MENU_HASHES = ("pobtm",)

#: tipul de pariu la Flashscore -> numele pietei la noi
BET_TYPES: dict[str, str] = {
    "HOME_DRAW_AWAY": "1x2",
    "OVER_UNDER": "ou",
    "BOTH_TEAMS_TO_SCORE": "btts",
    "DOUBLE_CHANCE": "dc",
}

#: casele din Romania, in ordinea in care le-am vazut listate
DEFAULT_BOOKMAKERS: tuple[tuple[int, str], ...] = (
    (516, "betano"), (592, "superbet"), (965, "getsbet"),
    (623, "unibet"), (817, "mrbit"),
)

#: prefixele agregatelor noastre, ca sa nu se ciocneasca cu football-data
AVERAGE = "fs_avg"
BEST = "fs_max"


class OddsFormatChanged(RuntimeError):
    """Endpointul nu mai raspunde in forma asteptata."""


@dataclass(frozen=True)
class OddsRow:
    """O cota: piata, selectia si pretul, plus cota de deschidere daca exista."""

    market: str
    selection: str
    price: float
    opening: float | None = None


def _price(node: Any) -> tuple[float, float | None] | None:
    """Pretul curent si cel de deschidere dintr-un nod de cota."""
    if not isinstance(node, dict):
        return None
    try:
        value = float(node["value"])
    except (KeyError, TypeError, ValueError):
        return None
    if value <= 1.0:
        return None
    try:
        opening = float(node["opening"])
    except (KeyError, TypeError, ValueError):
        opening = None
    return value, opening


def _rows(market: str, pairs: Iterable[tuple[str, Any]]) -> list[OddsRow]:
    out: list[OddsRow] = []
    for selection, node in pairs:
        priced = _price(node)
        if priced:
            out.append(OddsRow(market, selection, priced[0], priced[1]))
    return out


# ----------------------------------------------------------------------
def parse_odds(payload: dict[str, Any], bet_type: str) -> list[OddsRow]:
    """Raspunsul brut al endpointului, in randuri de cote.

    Intoarce lista goala cand meciul pur si simplu nu are cote la casa ceruta;
    ridica `OddsFormatChanged` doar cand raspunsul nu mai are forma asteptata.
    """
    if not isinstance(payload, dict):
        raise OddsFormatChanged("raspuns care nu e JSON obiect")
    if payload.get("errors"):
        raise OddsFormatChanged(str(payload["errors"])[:200])

    data = payload.get("data")
    if data is None:
        raise OddsFormatChanged("lipseste cheia 'data'")
    node = data.get("findPrematchOddsForBookmaker")
    if node is None:
        return []

    if bet_type == "HOME_DRAW_AWAY":
        return _rows("1x2", [("home", node.get("home")),
                             ("draw", node.get("draw")),
                             ("away", node.get("away"))])

    if bet_type == "BOTH_TEAMS_TO_SCORE":
        return _rows("btts", [("yes", node.get("yes")), ("no", node.get("no"))])

    if bet_type == "DOUBLE_CHANCE":
        # numele campurilor sunt homeOrDraw / awayOrDraw / noDraw - verificate
        # direct in raspuns, nu ghicite
        return _rows("dc", [("1x", node.get("homeOrDraw")),
                            ("x2", node.get("awayOrDraw")),
                            ("12", node.get("noDraw"))])

    if bet_type == "OVER_UNDER":
        out: list[OddsRow] = []
        for chance in node.get("opportunities") or []:
            handicap = (chance.get("handicap") or {}).get("value")
            if handicap in (None, ""):
                continue
            market = f"ou_{handicap}"
            out.extend(_rows(market, [("over", chance.get("over")),
                                      ("under", chance.get("under"))]))
        return out

    raise OddsFormatChanged(f"tip de pariu necunoscut: {bet_type}")


def aggregate(by_bookmaker: dict[str, list[OddsRow]]) -> list[tuple[str, OddsRow]]:
    """Media si cel mai bun pret, peste toate casele.

    `fs_max` e ce ar obtine un parior care alege casa cea mai buna pentru
    fiecare rezultat; `fs_avg` e reperul mai onest pentru probabilitati,
    fiindca cel mai bun pret exagereaza sistematic.
    """
    collected: dict[tuple[str, str], list[float]] = {}
    for rows in by_bookmaker.values():
        for row in rows:
            collected.setdefault((row.market, row.selection), []).append(row.price)

    out: list[tuple[str, OddsRow]] = []
    for (market, selection), prices in collected.items():
        out.append((AVERAGE, OddsRow(market, selection,
                                     sum(prices) / len(prices))))
        out.append((BEST, OddsRow(market, selection, max(prices))))
    return out


def implied_margin(rows: Iterable[OddsRow], market: str = "1x2") -> float | None:
    """Marja casei pe o piata: cat depaseste suma probabilitatilor implicite."""
    prices = [r.price for r in rows if r.market == market and r.price > 0]
    if len(prices) < 2:
        return None
    return sum(1.0 / p for p in prices) - 1.0


# ----------------------------------------------------------------------
def odds_query(event_id: str, bookmaker_id: int, bet_type: str,
               query_hash: str = ODDS_HASHES[0]) -> str:
    return f"{ODDS_URL}?" + urlencode({
        "_hash": query_hash, "eventId": event_id,
        "bookmakerId": bookmaker_id, "betType": bet_type,
        "betScope": "FULL_TIME",
    })


def menu_query(event_id: str, *, project_id: int = 9, geo: str = "RO",
               subdivision: str = "ROBV",
               query_hash: str = MENU_HASHES[0]) -> str:
    """Meniul de case pentru un meci.

    `geoIpSubdivisionCode` nu e optional: fara el endpointul raspunde 400.
    """
    return f"{ODDS_URL}?" + urlencode({
        "_hash": query_hash, "eventId": event_id,
        "projectId": project_id, "geoIpCode": geo,
        "geoIpSubdivisionCode": subdivision,
    })


def parse_bookmakers(payload: dict[str, Any]) -> list[tuple[int, str]]:
    """Casele listate pentru un meci, din raspunsul de meniu."""
    data = (payload or {}).get("data") or {}
    node = next(iter(data.values()), None)
    if not isinstance(node, dict):
        return []
    listed = (node.get("settings") or {}).get("bookmakers") or []
    out: list[tuple[int, str]] = []
    for entry in listed:
        book = (entry or {}).get("bookmaker") or {}
        ident, name = book.get("id"), book.get("name")
        if ident is None or not name:
            continue
        out.append((int(ident), _slug(str(name))))
    return out


def _slug(name: str) -> str:
    """Numele casei, in forma scurta folosita in coloana `bookmaker`."""
    low = name.lower().replace(".ro", "").replace(" ", "")
    for known in ("betano", "superbet", "getsbet", "unibet", "mrbit",
                  "bet365", "betfair", "pinnacle"):
        if known in low:
            return known
    return "".join(ch for ch in low if ch.isalnum())[:20] or "necunoscut"


def decode(body: str) -> dict[str, Any]:
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise OddsFormatChanged(f"raspuns care nu e JSON: {exc}") from exc
