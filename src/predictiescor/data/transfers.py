"""Transferuri: veniri, plecari si sumele lor.

Pagina de transferuri a fiecarui club de la Transfermarkt are doua tabele -
veniri si plecari - fiecare cu suma. E singura sursa care publica banii.

    https://www.transfermarkt.com/{slug}/transfers/verein/{id}/saison_id/{sezon}

Sumele apar in cateva forme, toate acoperite aici:

    "€1.00m"            1 000 000
    "€600k"               600 000
    "free transfer"             0   -- gratis, dar CUNOSCUT
    "Loan fee: €1.50m"  1 500 000   -- imprumut cu taxa
    "loan transfer"             0   -- imprumut fara taxa
    "?"                      None   -- suma nu e publica

Distinctia dintre 0 si `None` conteaza: "gratis" si "nu se stie" sunt lucruri
diferite, iar un bilant care le confunda minte. La calculul profitului,
transferurile cu suma necunoscuta se numara separat, nu ca zero.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

VENIRE, PLECARE = "venire", "plecare"

#: multiplicatorii din text: "m" = milioane, "k" = mii
_UNITS = {"m": 1_000_000.0, "k": 1_000.0, "bn": 1_000_000_000.0}

_AMOUNT = re.compile(r"([\d.,]+)\s*(bn|m|k)?", re.I)

#: texte care inseamna "gratis", nu "necunoscut"
_FREE = ("free transfer", "free", "transfer gratuit", "ablösefrei",
         "loan transfer", "end of loan", "loan")

#: texte care inseamna "nu se stie"
_UNKNOWN = ("?", "-", "", "draft", "n/a")


@dataclass(frozen=True)
class Transfer:
    """Un transfer, asa cum il publica sursa."""

    direction: str
    player_name: str
    position: str | None
    age: int | None
    other_club: str | None
    other_league: str | None
    fee_eur: float | None
    fee_text: str
    is_loan: bool

    @property
    def is_free(self) -> bool:
        return self.fee_eur == 0.0

    @property
    def is_unknown(self) -> bool:
        return self.fee_eur is None


def parse_fee(text: str) -> tuple[float | None, bool]:
    """Suma in euro si daca e imprumut. `None` cand suma nu e publica."""
    raw = " ".join(str(text or "").split())
    low = raw.lower()
    is_loan = "loan" in low

    if low in _UNKNOWN:
        return None, is_loan
    if any(low == marker or low.startswith(marker) and "fee" not in low
           for marker in _FREE):
        return 0.0, is_loan

    match = _AMOUNT.search(low.replace("loan fee:", "").replace("€", " "))
    if not match:
        return (0.0 if any(m in low for m in _FREE) else None), is_loan

    number = match.group(1).replace(",", "")
    # "1.00" e un numar zecimal, dar "1.500.000" ar fi separator de mii
    if number.count(".") > 1:
        number = number.replace(".", "")
    try:
        value = float(number)
    except ValueError:
        return None, is_loan
    return value * _UNITS.get(match.group(2) or "", 1.0), is_loan


def parse_transfers(html: str) -> list[Transfer]:
    """Cele doua tabele ale paginii, in randuri de transfer.

    Capul tabelului spune care e care: "Left" = de unde a venit (deci tabelul
    de VENIRI), "Joined" = unde a plecat.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    out: list[Transfer] = []

    for table in soup.select("table.items"):
        headers = [th.get_text(" ", strip=True).lower()
                   for th in table.select("thead th")]
        if "joined" in headers:
            direction = PLECARE
        elif "left" in headers:
            direction = VENIRE
        else:
            continue

        for row in table.select("tbody > tr"):
            cells = row.select("td")
            if len(cells) < 6:
                continue
            transfer = _parse_row(cells, direction)
            if transfer is not None:
                out.append(transfer)
    return out


def _parse_row(cells, direction: str) -> Transfer | None:
    """Un rand din tabel. Randurile de total sau goale intorc `None`."""
    def by_class(name: str) -> list:
        return [c for c in cells if name in (c.get("class") or [])]

    principale = by_class("hauptlink")
    if not principale:
        return None

    player = principale[0].get_text(" ", strip=True)
    if not player:
        return None

    # pozitia e in celula imediat urmatoare numelui
    position = None
    index = cells.index(principale[0])
    if index + 1 < len(cells):
        candidate = cells[index + 1].get_text(" ", strip=True)
        if candidate and not candidate.isdigit():
            position = candidate

    age = None
    for cell in cells:
        text = cell.get_text(strip=True)
        if text.isdigit() and 14 <= int(text) <= 45:
            age = int(text)
            break

    club = principale[1].get_text(" ", strip=True) if len(principale) > 1 else None
    league = None
    if club:
        pos_club = cells.index(principale[1])
        if pos_club + 1 < len(cells):
            league = cells[pos_club + 1].get_text(" ", strip=True) or None

    fee_cell = next((c for c in cells if "rechts" in (c.get("class") or [])), None)
    fee_text = fee_cell.get_text(" ", strip=True) if fee_cell else ""
    fee_eur, is_loan = parse_fee(fee_text)

    return Transfer(direction=direction, player_name=player, position=position,
                    age=age, other_club=club, other_league=league,
                    fee_eur=fee_eur, fee_text=fee_text, is_loan=is_loan)


def balance(transfers: list[Transfer]) -> dict[str, float | int]:
    """Bilantul unui club: cat a incasat, cat a platit, cat a ramas.

    Transferurile cu suma necunoscuta se numara separat. Puse pe zero, ar face
    un club care si-a vandut jucatorii pe sume nepublicate sa para ca i-a dat
    degeaba.
    """
    incasat = sum(t.fee_eur or 0.0 for t in transfers
                  if t.direction == PLECARE and t.fee_eur)
    platit = sum(t.fee_eur or 0.0 for t in transfers
                 if t.direction == VENIRE and t.fee_eur)
    return {
        "incasat": float(incasat),
        "platit": float(platit),
        "profit": float(incasat - platit),
        "veniri": sum(1 for t in transfers if t.direction == VENIRE),
        "plecari": sum(1 for t in transfers if t.direction == PLECARE),
        "gratis": sum(1 for t in transfers if t.is_free),
        "sume_nepublicate": sum(1 for t in transfers if t.is_unknown),
        "imprumuturi": sum(1 for t in transfers if t.is_loan),
    }
