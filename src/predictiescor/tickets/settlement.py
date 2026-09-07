"""Verificarea biletelor dupa ce meciurile s-au jucat.

Fiecare picior este decontat comparand selectia cu ce s-a intamplat efectiv.
Cand datele necesare lipsesc (de exemplu cornerele unui meci pentru care nu am
apucat sa descarcam statisticile), piciorul este marcat **anulat**, nu ghicit -
un bilet cu picioare anulate nu contamineaza statisticile de acuratete.

Dar anularea aia e provizorie. Statisticile LPF apar la cateva ore dupa meci,
iar decontarea ruleaza imediat ce se fluiera finalul, deci pontul pe cartonase
prinde mereu baza goala. Marcat "anulat" pentru totdeauna, un pariu care de
fapt a pierdut dispare din acuratete si o face sa arate mai bine decat e. Asa
au ramas anulate doua bilete de pe 6 septembrie, desi Petrolul - Corvinul avea
10 cartonase si Otelul - Rapid trei: amandoua pierdusera.

Deci se face distinctia intre anularile care se pot repara (`RETRYABLE_VOIDS`
- datele lipseau ATUNCI) si cele care nu se pot (selectie neinterpretabila,
piata nesuportata). Primele sunt reluate la fiecare decontare, iar biletele
lor sunt rejudecate.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import pandas as pd

log = logging.getLogger(__name__)

WON, LOST, VOID, OPEN = "castigat", "pierdut", "anulat", "deschis"

#: Motivele de anulare care se pot schimba singure: datele lipseau la
#: momentul decontarii, dar pot aparea mai tarziu. Restul motivelor - o
#: selectie neinterpretabila, o piata nesuportata - n-au cum sa se repare,
#: deci reluarea lor ar fi munca in gol la fiecare rulare.
RETRYABLE_VOIDS = (
    "statistici indisponibile pentru acest meci",
    "nu avem date pe jucători pentru acest meci",
)


@dataclass
class MatchFacts:
    """Tot ce s-a intamplat intr-un meci, cat stim noi."""

    match_id: int
    home_goals: int
    away_goals: int
    home_stats: dict[str, float]
    away_stats: dict[str, float]
    scorers: set[str]
    has_player_data: bool

    @property
    def total_goals(self) -> int:
        return self.home_goals + self.away_goals

    def stat_total(self, column: str) -> float | None:
        home = self.home_stats.get(column)
        away = self.away_stats.get(column)
        if home is None or away is None:
            return None
        return float(home) + float(away)


def load_match_facts(db, match_id: int) -> MatchFacts | None:
    """Citeste rezultatul, statisticile si marcatorii unui meci jucat."""
    with db.connect() as conn:
        match = conn.execute(
            "SELECT * FROM matches WHERE match_id = ?", (match_id,)).fetchone()
        if match is None or match["home_goals"] is None:
            return None

        stats = conn.execute(
            "SELECT * FROM match_team_stats WHERE match_id = ?",
            (match_id,)).fetchall()
        home_stats = {k: r[k] for r in stats if r["is_home"] == 1
                      for k in r.keys()}
        away_stats = {k: r[k] for r in stats if r["is_home"] == 0
                      for k in r.keys()}

        scorer_rows = conn.execute(
            "SELECT p.name AS name, ps.goals AS goals, ps.team_id AS team_id"
            " FROM player_match_stats ps"
            " JOIN players p ON p.player_id = ps.player_id"
            " WHERE ps.match_id = ?", (match_id,)).fetchall()

    scorers = {str(r["name"]) for r in scorer_rows if (r["goals"] or 0) > 0}

    # Avem incredere in marcatori DOAR daca reconstruiesc scorul FIECAREI
    # echipe. Autogolurile sunt publicate cu aceeasi iconita ca golurile
    # obisnuite, pe partea jucatorului care le-a inscris: totalul ramane
    # corect, dar un gol apare la echipa gresita si un jucator ar fi trecut
    # gresit ca marcator. Cand nu se potriveste, tratam piata de marcatori ca
    # nedecidabila (anulata) in loc sa o decontam gresit.
    credited = {"home": 0.0, "away": 0.0}
    for row in scorer_rows:
        side = "home" if int(row["team_id"]) == int(match["home_team_id"]) \
            else "away"
        credited[side] += float(row["goals"] or 0)

    trustworthy = (
        bool(scorer_rows)
        and credited["home"] == float(match["home_goals"])
        and credited["away"] == float(match["away_goals"])
    )

    return MatchFacts(
        match_id=match_id,
        home_goals=int(match["home_goals"]),
        away_goals=int(match["away_goals"]),
        home_stats=home_stats,
        away_stats=away_stats,
        scorers=scorers,
        has_player_data=trustworthy,
    )


# ----------------------------------------------------------------------
#: coloana din match_team_stats pentru fiecare piata de statistici
STAT_COLUMN = {"cornere": "corners", "cartonașe": "yellow_cards"}

_LINE_RE = re.compile(r"(peste|sub)\s+([\d.]+)")


def settle_leg(market: str, selection: str, facts: MatchFacts) -> tuple[str, str]:
    """Intoarce (rezultat, descrierea a ce s-a intamplat)."""
    home, away = facts.home_goals, facts.away_goals
    actual_score = f"{home}-{away}"

    if market == "1X2":
        winner = "gazda" if home > away else "oaspete" if away > home else "egal"
        return (WON if selection == winner else LOST), actual_score

    if market == "șansă dublă":
        winner = "gazda" if home > away else "oaspete" if away > home else "egal"
        allowed = {"1X": {"gazda", "egal"}, "X2": {"oaspete", "egal"},
                   "12": {"gazda", "oaspete"}}.get(selection, set())
        return (WON if winner in allowed else LOST), actual_score

    if market == "total goluri":
        m = _LINE_RE.search(selection)
        if not m:
            return VOID, "selecție neinterpretabilă"
        side, line = m.group(1), float(m.group(2))
        total = facts.total_goals
        won = total > line if side == "peste" else total < line
        return (WON if won else LOST), f"{total} goluri"

    if market == "ambele marchează":
        both = home > 0 and away > 0
        won = both if selection == "da" else not both
        return (WON if won else LOST), actual_score

    if market == "echipa marchează":
        scored = home > 0 if selection == "gazda" else away > 0
        return (WON if scored else LOST), actual_score

    if market in STAT_COLUMN:
        column = STAT_COLUMN[market]
        total = facts.stat_total(column)
        if total is None:
            return VOID, "statistici indisponibile pentru acest meci"
        m = _LINE_RE.search(selection)
        if not m:
            return VOID, "selecție neinterpretabilă"
        side, line = m.group(1), float(m.group(2))
        won = total > line if side == "peste" else total < line
        return (WON if won else LOST), f"{total:.0f} {market}"

    if market == "marcatori":
        if not facts.has_player_data:
            return VOID, "nu avem date pe jucători pentru acest meci"
        name = selection.split("::", 1)[-1].strip()
        won = any(_same_player(name, scorer) for scorer in facts.scorers)
        return (WON if won else LOST), (
            "a marcat" if won else "nu a marcat")

    return VOID, f"piață nesuportată: {market}"


def _same_player(a: str, b: str) -> bool:
    """Compara doua nume de jucatori tolerand diferentele de scriere."""
    from ..utils.teamnames import strip_diacritics

    norm_a = strip_diacritics(a).lower().replace(".", " ").split()
    norm_b = strip_diacritics(b).lower().replace(".", " ").split()
    if not norm_a or not norm_b:
        return False
    # numele de familie trebuie sa coincida
    if norm_a[-1] != norm_b[-1]:
        return False
    # daca ambele au prenume, verificam macar initiala
    if len(norm_a) > 1 and len(norm_b) > 1:
        return norm_a[0][0] == norm_b[0][0]
    return True


# ----------------------------------------------------------------------
def settle_open_legs(db, *, match_day: str | None = None) -> int:
    """Deconteaza TOATE ponturile deschise ale caror meciuri s-au jucat.

    Independent de starea biletului. Fara asta, un pont de pe un bilet inchis
    devreme (pentru ca alt pont ratase deja) ramanea "deschis" pentru
    totdeauna: interogarea cauta doar bilete cu starea `deschis`, iar biletul
    nu mai era. Efectul nu era doar cosmetic - acele ponturi nu intrau in
    datele de invatare, deci calibrarea pierdea sistematic exact ponturile de
    pe biletele pierdute.
    """
    # Pe langa ponturile deschise, se reiau si cele anulate din lipsa de date:
    # intre timp statisticile pot fi ajuns in baza. Daca tot lipsesc,
    # `settle_leg` le anuleaza din nou si nu se pierde nimic.
    marks = ", ".join("?" * len(RETRYABLE_VOIDS))
    where = (f"(l.result = ? OR (l.result = ? AND l.actual_value IN ({marks})))"
             " AND m.home_goals IS NOT NULL")
    params: list[Any] = [OPEN, VOID, *RETRYABLE_VOIDS]
    if match_day:
        where += " AND t.match_day = ?"
        params.append(match_day)

    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT l.leg_id, l.match_id, l.market, l.selection"
            f" FROM ticket_legs l"
            f" JOIN tickets t ON t.ticket_id = l.ticket_id"
            f" JOIN matches m ON m.match_id = l.match_id"
            f" WHERE {where}", params,
        ).fetchall()

    if not rows:
        return 0

    facts_cache: dict[int, MatchFacts | None] = {}
    updates: list[tuple[str, str, int]] = []
    for row in rows:
        match_id = int(row["match_id"])
        if match_id not in facts_cache:
            facts_cache[match_id] = load_match_facts(db, match_id)
        facts = facts_cache[match_id]
        if facts is None:
            continue
        outcome, actual = settle_leg(
            str(row["market"]), str(row["selection"]), facts)
        updates.append((outcome, actual, int(row["leg_id"])))

    if updates:
        with db.connect() as conn:
            conn.executemany(
                "UPDATE ticket_legs SET result = ?, actual_value = ?"
                " WHERE leg_id = ?", updates)
    log.info("ponturi decontate: %d", len(updates))
    return len(updates)


def _tickets_with_retryable_voids(db, match_day: str | None = None) -> set[int]:
    """Biletele care au macar un pont anulat din lipsa de date.

    Se citesc INAINTE de decontarea ponturilor: dupa ea, piciorul nu mai e
    anulat, iar biletul - inchis de mult ca "anulat" sau "castigat" - n-ar mai
    fi gasit de interogarea care cauta doar bilete deschise.
    """
    marks = ", ".join("?" * len(RETRYABLE_VOIDS))
    sql = (f"SELECT DISTINCT l.ticket_id FROM ticket_legs l"
           f" JOIN tickets t ON t.ticket_id = l.ticket_id"
           f" WHERE l.result = ? AND l.actual_value IN ({marks})")
    params: list[Any] = [VOID, *RETRYABLE_VOIDS]
    if match_day:
        sql += " AND t.match_day = ?"
        params.append(match_day)
    with db.connect() as conn:
        return {int(r["ticket_id"]) for r in conn.execute(sql, params)}


def resettle_matches(db, match_ids: Iterable[int]) -> dict[str, int]:
    """Judeca din nou ponturile de pe meciurile date, de la zero.

    Decontarea obisnuita nu se mai uita la un pont odata decis - si bine face,
    altfel ar rescrie istoria la fiecare rulare. Dar cand se repara **datele**
    pe care s-a judecat, verdictul trebuie refacut: LPF scrisese 0 cornere la
    FCSB - UTA Arad, asa ca „sub 8.5 cornere" iesise castigat; meciul avusese
    noua.

    Se cheama la mana, dupa o reparare de date, nu la fiecare actualizare.
    """
    ids = [int(m) for m in match_ids]
    if not ids:
        return {"decontate": 0, "in_asteptare": 0, "ponturi": 0}

    marks = ", ".join("?" * len(ids))
    with db.connect() as conn:
        tickets = [int(r["ticket_id"]) for r in conn.execute(
            f"SELECT DISTINCT ticket_id FROM ticket_legs"
            f" WHERE match_id IN ({marks})", ids)]
        if not tickets:
            return {"decontate": 0, "in_asteptare": 0, "ponturi": 0}
        conn.execute(
            f"UPDATE ticket_legs SET result = ?, actual_value = NULL"
            f" WHERE match_id IN ({marks})", [OPEN, *ids])
        # Biletul se redeschide ca sa fie rejudecat; ce se stie despre
        # celelalte picioare ale lui ramane pe loc.
        conn.execute(
            f"UPDATE tickets SET status = ?, payout = NULL, profit = NULL,"
            f" settled_at = NULL, legs_won = 0"
            f" WHERE ticket_id IN ({', '.join('?' * len(tickets))})",
            [OPEN, *tickets])
    log.info("redeconez %d bilete de pe %d meciuri", len(tickets), len(ids))
    return settle_tickets(db)


def settle_tickets(db, *, match_day: str | None = None) -> dict[str, int]:
    """Deconteaza ponturile jucate, apoi decide biletele inca deschise."""
    revizuite = _tickets_with_retryable_voids(db, match_day)
    legs_settled = settle_open_legs(db, match_day=match_day)

    where = "t.status = ?"
    params: list[Any] = [OPEN]
    if revizuite:
        # Un bilet inchis ca "anulat" (sau castigat, cu piciorul anulat scos
        # din cota) trebuie rejudecat daca intre timp s-a aflat rezultatul.
        where = (f"({where} OR t.ticket_id IN "
                 f"({', '.join('?' * len(revizuite))}))")
        params.extend(sorted(revizuite))
    if match_day:
        where += " AND t.match_day = ?"
        params.append(match_day)

    with db.connect() as conn:
        tickets = conn.execute(
            f"SELECT * FROM tickets t WHERE {where} ORDER BY t.match_day", params
        ).fetchall()

    settled = skipped = 0
    for ticket in tickets:
        ticket_id = int(ticket["ticket_id"])
        with db.connect() as conn:
            legs = conn.execute(
                "SELECT * FROM ticket_legs WHERE ticket_id = ?",
                (ticket_id,)).fetchall()
        if not legs:
            continue

        decided = [leg for leg in legs if str(leg["result"]) != OPEN]
        pending = len(legs) - len(decided)
        won = sum(1 for leg in decided if str(leg["result"]) == WON)
        lost = sum(1 for leg in decided if str(leg["result"]) == LOST)
        void = sum(1 for leg in decided if str(leg["result"]) == VOID)

        if lost > 0:
            # Un singur pont ratat pierde tot biletul: nu are rost sa mai
            # asteptam restul meciurilor, rezultatul e deja decis.
            status, payout = "pierdut", 0.0
        elif pending > 0:
            status, payout = OPEN, None
        elif void == len(decided):
            status, payout = "anulat", float(ticket["stake"])
        else:
            status = "castigat"
            # picioarele anulate ies din calcul, ca la casele de pariuri
            odds = 1.0
            for leg in decided:
                if str(leg["result"]) == WON:
                    odds *= float(leg["offered_odds"] or leg["fair_odds"])
            payout = float(ticket["stake"]) * odds

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with db.connect() as conn:
            if status == OPEN:
                # biletul ramane deschis, dar aratam cate ponturi au iesit
                conn.execute("UPDATE tickets SET legs_won=? WHERE ticket_id=?",
                             (won, ticket_id))
            else:
                conn.execute(
                    "UPDATE tickets SET status=?, legs_won=?, payout=?,"
                    " profit=?, settled_at=? WHERE ticket_id=?",
                    (status, won, payout,
                     payout - float(ticket["stake"]), now, ticket_id),
                )

        if status == OPEN:
            skipped += 1
        else:
            settled += 1

    log.info("bilete decontate: %d (amanate: %d), ponturi noi: %d",
             settled, skipped, legs_settled)
    return {"decontate": settled, "in_asteptare": skipped,
            "ponturi": legs_settled}


# ----------------------------------------------------------------------
def tickets_frame(db, *, match_day: str | None = None,
                  status: str | None = None, limit: int = 200) -> pd.DataFrame:
    where, params = [], []
    if match_day:
        where.append("match_day = ?")
        params.append(match_day)
    if status:
        where.append("status = ?")
        params.append(status)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    params.append(limit)
    with db.connect() as conn:
        return pd.read_sql_query(
            f"SELECT * FROM tickets {clause} ORDER BY match_day DESC,"
            f" ticket_id DESC LIMIT ?", conn, params=params)


def legs_frame(db, ticket_id: int) -> pd.DataFrame:
    sql = """
        SELECT l.*, ht.name AS home_team, at.name AS away_team, m.utc_date,
               m.home_goals, m.away_goals
        FROM ticket_legs l
        JOIN matches m ON m.match_id = l.match_id
        JOIN teams ht ON ht.team_id = m.home_team_id
        JOIN teams at ON at.team_id = m.away_team_id
        WHERE l.ticket_id = ?
        ORDER BY m.utc_date
    """
    with db.connect() as conn:
        return pd.read_sql_query(sql, conn, params=[ticket_id])


def legs_for_tickets(db, ticket_ids: Iterable[int]) -> pd.DataFrame:
    """Ponturile mai multor bilete deodata.

    Pagina de istoric afisa cate un bilet, ales dintr-un `selectbox`, si cerea
    o interogare pentru fiecare. Ca sa le poata arata pe toate sub forma de
    carduri, ii trebuie totul dintr-o data.
    """
    ids = [int(i) for i in ticket_ids]
    if not ids:
        return pd.DataFrame()
    marks = ",".join("?" * len(ids))
    sql = f"""
        SELECT l.*, ht.name AS home_team, at.name AS away_team, m.utc_date,
               m.home_goals, m.away_goals
        FROM ticket_legs l
        JOIN matches m ON m.match_id = l.match_id
        JOIN teams ht ON ht.team_id = m.home_team_id
        JOIN teams at ON at.team_id = m.away_team_id
        WHERE l.ticket_id IN ({marks})
        ORDER BY l.ticket_id, m.utc_date
    """
    with db.connect() as conn:
        return pd.read_sql_query(sql, conn, params=ids)


def all_legs_frame(db, *, settled_only: bool = True) -> pd.DataFrame:
    clause = "WHERE l.result != 'deschis'" if settled_only else ""
    sql = f"""
        SELECT l.*, t.strategy, t.match_day, t.status AS ticket_status
        FROM ticket_legs l
        JOIN tickets t ON t.ticket_id = l.ticket_id
        {clause}
    """
    with db.connect() as conn:
        return pd.read_sql_query(sql, conn)
