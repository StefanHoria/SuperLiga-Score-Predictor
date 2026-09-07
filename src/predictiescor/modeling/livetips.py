"""Ponturi live, din ce s-a intamplat pana in minutul asta.

Ideea e simpla si se poate spune intr-o propozitie: **ce s-a intamplat deja e
sigur, iar restul meciului se estimeaza din ritmul de pana acum amestecat cu
ce se astepta inainte de start.** Trei cornere in 39 de minute nu inseamna nici
ca meciul va avea sase (ritmul observat, luat orbeste), nici ca va avea noua si
jumatate (asteptarea de dinainte, ca si cum nu s-ar fi jucat nimic).

Amestecul se face cu o actualizare Gamma-Poisson. Ritmul dinainte de meci e o
distributie Gamma cu media `lambda` si o "greutate" de `a` meciuri; ce s-a
vazut pana acum aduce `k` evenimente in `m/90` dintr-un meci. Media de dupa:

    ritm_pe_90 = (a * lambda + k) / (a + m / 90)

Cu `a = 1`, un meci intreg de dovezi il muta la jumatatea drumului. La minutul
45 observatia are jumatate din greutatea asteptarii; spre final aproape ca o
inlocuieste - ceea ce si trebuie, fiindca atunci a mai ramas putin de estimat.

Restul meciului se numara cu Poisson, nu cu Negative Binomial. Dispersia
masurata in `secondary.py` descrie imprastierea unui meci INTREG, in mare parte
din diferentele dintre meciuri (arbitru, miza, teren) - lucruri care, la
minutul 39, s-au intamplat deja si nu se mai repeta pe rest. Aplicata pe
ramasita, ar largi degeaba intervalul. E acelasi rationament pentru care
`inplay.py` nu aplica corectia Dixon-Coles pe restul de meci.

**Ce nu stie modelul asta.** Ritmul e presupus constant pe tot meciul. Pentru
cornere e o aproximatie rezonabila; pentru cartonase nu prea - ele se aduna
spre final, cand jocul se rupe si scorul strange nervii. Nu avem minutele
cartonaselor in baza, deci nu putem masura profilul; pana atunci, proiectia pe
cartonase e mai degraba prudenta decat exacta, iar „peste" e subestimat. Scrie
si pe pagina.

**Si ce nu sunt ponturile astea.** Nu sunt „valoare". `disagreement.py` a
masurat, pe 1004 meciuri, ca acolo unde modelul se desparte de piata gresim mai
des noi. Aici nici macar nu comparam cu o cota live - nu avem asa ceva. E doar
parerea modelului, cu cota corecta alaturi, ca sa ai cu ce compara oferta casei.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.stats import poisson

from .inplay import FULL_TIME, InPlayState, live_markets

log = logging.getLogger(__name__)

#: Cate meciuri de "dovada" cantareste asteptarea de dinainte de start. Cu 1,
#: un meci intreg de observatii o muta la jumatate.
PRIOR_MATCHES = 1.0

#: Sub atat, pontul nu merita aratat; peste, e o certitudine, nu un pont - un
#: „peste 0.5 goluri" la 2-0 are probabilitatea 1 si nu spune nimic.
MIN_PROBABILITY = 0.65
MAX_PROBABILITY = 0.97

#: Statisticile pe care le proiectam, cu numele pietei si liniile obisnuite.
PROJECTED_STATS = {
    "corners": ("cornere", (7.5, 8.5, 9.5, 10.5, 11.5)),
    "yellow_cards": ("cartonașe", (2.5, 3.5, 4.5, 5.5)),
}


@dataclass(frozen=True)
class Tip:
    """Un pont propus, cu tot ce trebuie ca sa poata fi judecat."""

    market: str
    selection: str
    description: str
    probability: float
    prematch: float | None
    reason: str

    @property
    def fair_odds(self) -> float:
        return 1.0 / self.probability if self.probability > 0 else float("inf")

    @property
    def movement(self) -> float:
        """Cat a mutat meciul probabilitatea fata de dinainte de start."""
        return 0.0 if self.prematch is None else self.probability - self.prematch


# ----------------------------------------------------------------------
def projected_total(prior_total: float, observed: float, minute: int,
                    *, prior_matches: float = PRIOR_MATCHES) -> float:
    """Cate evenimente va avea meciul in total, dupa ce a avut `observed`."""
    return observed + remaining_mean(prior_total, observed, minute,
                                     prior_matches=prior_matches)


def remaining_mean(prior_total: float, observed: float, minute: int,
                   *, prior_matches: float = PRIOR_MATCHES) -> float:
    """Cate evenimente mai vin pana la final.

    Zero dupa minutul 90: ce s-a jucat, s-a jucat.
    """
    played = min(max(int(minute), 0), FULL_TIME) / FULL_TIME
    left = 1.0 - played
    if left <= 0:
        return 0.0
    rate = (prior_matches * prior_total + observed) / (prior_matches + played)
    return max(0.0, rate * left)


def over_under_from_now(observed: float, remaining: float, line: float
                        ) -> dict[str, float]:
    """Peste/sub pe TOTALUL final, stiind ce s-a intamplat deja.

    Ce s-a marcat nu se mai poate lua inapoi, deci linia se muta: „peste 8.5
    cornere" cu 3 deja date inseamna „inca cel putin 6".
    """
    needed = line - observed
    if needed < 0:                      # deja trecut de linie
        return {"peste": 1.0, "sub": 0.0}
    if remaining <= 0:                  # meciul s-a terminat
        return {"peste": 0.0, "sub": 1.0}
    # P(X > needed) cu X ~ Poisson(remaining), needed fiind fractionar
    over = float(poisson.sf(np.floor(needed), remaining))
    return {"peste": over, "sub": 1.0 - over}


# ----------------------------------------------------------------------
def _prematch_totals(pipeline, home: str, away: str) -> dict[str, float]:
    """Cate cornere/cartonase se asteptau inainte de start."""
    out: dict[str, float] = {}
    secondary = getattr(pipeline, "secondary", None)
    if secondary is None or not getattr(secondary, "models", None):
        return out
    for stat in PROJECTED_STATS:
        model = secondary.models.get(stat)
        if model is None:
            continue
        try:
            home_mean, away_mean = model.expected_match(home, away)
        except Exception as exc:  # noqa: BLE001
            log.debug("nu am asteptarea pentru %s: %s", stat, exc)
            continue
        out[stat] = float(home_mean + away_mean)
    return out


def _prematch_markets(pipeline, home: str, away: str) -> dict[str, Any]:
    try:
        return pipeline.predict(home, away).markets
    except Exception as exc:  # noqa: BLE001
        log.debug("nu am predictia de dinainte de meci: %s", exc)
        return {}


def _goal_tips(pipeline, state: InPlayState) -> list[Tip]:
    """Ponturi pe goluri, din matricea recalculata pentru minutul curent."""
    live = live_markets(pipeline, state)["piete"]
    before = _prematch_markets(pipeline, state.home_team, state.away_team)
    left = max(0, FULL_TIME - state.minute)

    tips: list[Tip] = []
    for key, book in live.items():
        if not isinstance(book, dict):
            continue
        pretty = {"1X2": "rezultat final", "sansa dubla": "șansă dublă",
                  "ambele marcheaza": "ambele marchează"}.get(key, key)
        # Toate liniile de goluri sunt aceeasi piata: altfel „sub 1.5", „sub
        # 2.5" si „peste 0.5" ar ocupa trei locuri spunand acelasi lucru.
        family = "total goluri" if key.startswith("total goluri") else pretty
        for selection, probability in book.items():
            tips.append(Tip(
                market=family,
                selection=(f"{selection} {key.split()[-1]}"
                           if family == "total goluri" else str(selection)),
                description=(f"{key}: {selection}" if family == "total goluri"
                             else f"{pretty}: {selection}"),
                probability=float(probability),
                prematch=_lookup(before, key, selection),
                reason=(f"la {state.score} în minutul {state.minute}, "
                        f"{left} minute rămase"),
            ))
    return tips


def _lookup(markets: dict[str, Any], key: str, selection: Any) -> float | None:
    book = markets.get(key)
    if not isinstance(book, dict):
        return None
    value = book.get(selection)
    return None if value is None else float(value)


def _stat_tips(pipeline, state: InPlayState,
               stats: dict[str, tuple[float, float]]) -> list[Tip]:
    """Ponturi pe cornere si cartonase, proiectate din ritmul de pana acum."""
    priors = _prematch_totals(pipeline, state.home_team, state.away_team)
    tips: list[Tip] = []

    for stat, (market, lines) in PROJECTED_STATS.items():
        pair, prior = stats.get(stat), priors.get(stat)
        if pair is None or prior is None:
            continue
        observed = float(pair[0] + pair[1])
        remaining = remaining_mean(prior, observed, state.minute)
        total = observed + remaining
        pace = "peste" if total > prior else "sub"
        reason = (f"{observed:.0f} în {state.minute} minute — ritm {pace} "
                  f"așteptare; proiecție {total:.1f} la final "
                  f"(se aștepta {prior:.1f})")

        for line in lines:
            book = over_under_from_now(observed, remaining, line)
            before = _prior_over_under(pipeline, state, stat, line)
            for selection, probability in book.items():
                # `before` e probabilitatea lui „peste"; pentru „sub" e restul.
                # Comparate gresit, un pont linistit parea ca a mutat piata cu
                # 66 de puncte cand o mutase cu 24.
                prematch = None if before is None else (
                    before if selection == "peste" else 1.0 - before)
                tips.append(Tip(
                    market=market,
                    selection=f"{selection} {line}",
                    description=f"{market}: {selection} {line}",
                    probability=float(probability),
                    prematch=prematch,
                    reason=reason,
                ))
    return tips


def _prior_over_under(pipeline, state: InPlayState, stat: str,
                      line: float) -> float | None:
    """Cat de probabil parea „peste linie" inainte de start."""
    secondary = getattr(pipeline, "secondary", None)
    model = getattr(secondary, "models", {}).get(stat) if secondary else None
    if model is None:
        return None
    try:
        return float(model.match_total_over_under(
            state.home_team, state.away_team, line)["peste"])
    except Exception:  # noqa: BLE001
        return None


# ----------------------------------------------------------------------
def live_tips(pipeline, state: InPlayState,
              stats: dict[str, tuple[float, float]] | None = None,
              *, minimum: float = MIN_PROBABILITY,
              maximum: float = MAX_PROBABILITY,
              limit: int = 6) -> list[Tip]:
    """Ponturile pe care le sugereaza situatia din teren.

    Se pastreaza doar cele suficient de probabile ca sa merite spuse, dar nu
    atat de sigure incat sa nu mai fie un pont. Ordinea e data de **cat a mutat
    meciul probabilitatea** fata de dinainte de start: acolo e informatia
    adusa de minutele jucate, nu in faptul ca favorita ramane favorita.
    """
    if state.minute >= FULL_TIME:
        return []

    tips = _goal_tips(pipeline, state) + _stat_tips(pipeline, state,
                                                    stats or {})
    kept = [t for t in tips if minimum <= t.probability <= maximum]

    # Un singur pont pe piata: doua linii vecine spun aproape acelasi lucru.
    best: dict[str, Tip] = {}
    for tip in kept:
        current = best.get(tip.market)
        if current is None or abs(tip.movement) > abs(current.movement):
            best[tip.market] = tip

    ordered = sorted(best.values(),
                     key=lambda t: (abs(t.movement), t.probability),
                     reverse=True)
    return ordered[:limit]
