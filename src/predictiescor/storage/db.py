"""Acces la baza de date SQLite: upsert-uri idempotente + interogari pentru modele."""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

import pandas as pd

from ..config import get_config
from ..utils.teamnames import canonical_team
from .schema import DDL, SCHEMA_VERSION

log = logging.getLogger(__name__)

# Coloanele de statistici de echipa, in ordinea din schema
TEAM_STAT_COLUMNS = [
    "shots", "shots_on_target", "shots_off_target", "shots_blocked",
    "shots_inside_box", "shots_outside_box", "corners", "offsides", "fouls",
    "yellow_cards", "red_cards", "possession", "passes", "passes_accurate",
    "pass_accuracy", "saves", "xg", "xg_estimated", "xga",
    "big_chances", "tackles",
    "attacks", "interceptions", "duels_won", "dribbles", "free_kicks",
    "passes_into_box",
]

PLAYER_STAT_COLUMNS = [
    "minutes", "is_starter", "position", "rating", "goals", "assists", "shots",
    "shots_on_target", "passes", "key_passes", "pass_accuracy", "tackles",
    "interceptions", "duels_total", "duels_won", "dribbles_att", "dribbles_succ",
    "fouls_drawn", "fouls_committed", "yellow_cards", "red_cards",
    "penalties_scored", "penalties_missed", "saves", "goals_conceded",
]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """Wrapper subtire peste sqlite3, cu upsert-uri specifice domeniului."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else get_config().db_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ------------------------------------------------------------------
    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(DDL)
            self._migrate(conn)
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),),
            )

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Adauga coloanele aparute dupa ce baza a fost creata.

        `CREATE TABLE IF NOT EXISTS` nu modifica un tabel existent, deci fara
        pasul asta o bază veche ar ramane fara coloanele noi (de exemplu cele
        aduse de LPF) si scrierile ar esua.
        """
        expected = {
            "match_team_stats": TEAM_STAT_COLUMNS,
            "player_match_stats": PLAYER_STAT_COLUMNS,
            "odds": ("opening", "updated_at"),
        }
        for table, columns in expected.items():
            existing = {row["name"] for row in
                        conn.execute(f"PRAGMA table_info({table})")}
            if not existing:
                continue
            for column in columns:
                if column not in existing:
                    kind = ("TEXT" if column in ("position", "updated_at")
                            else "REAL")
                    conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
                    log.info("migrare: am adaugat %s.%s", table, column)

    # ------------------------------------------------------------------
    # Echipe
    # ------------------------------------------------------------------
    def upsert_team(
        self,
        name: str,
        *,
        source: str | None = None,
        ext_id: str | None = None,
        **fields: Any,
    ) -> int:
        """Insereaza/actualizeaza o echipa dupa numele canonic si intoarce team_id."""
        canon = canonical_team(name)
        if not canon:
            raise ValueError("nume de echipa gol")

        with self.connect() as conn:
            cur = conn.execute("SELECT team_id FROM teams WHERE name = ?", (canon,))
            row = cur.fetchone()
            if row:
                team_id = int(row["team_id"])
                sets, vals = [], []
                for k in ("short_name", "city", "founded", "venue", "logo_url"):
                    v = fields.get(k)
                    if v not in (None, ""):
                        sets.append(f"{k} = ?")
                        vals.append(v)
                if sets:
                    vals.append(team_id)
                    conn.execute(
                        f"UPDATE teams SET {', '.join(sets)} WHERE team_id = ?", vals
                    )
            else:
                cur = conn.execute(
                    "INSERT INTO teams(name, short_name, city, founded, venue, logo_url)"
                    " VALUES (?,?,?,?,?,?)",
                    (
                        canon,
                        fields.get("short_name"),
                        fields.get("city"),
                        fields.get("founded"),
                        fields.get("venue"),
                        fields.get("logo_url"),
                    ),
                )
                team_id = int(cur.lastrowid)

            if source and ext_id:
                conn.execute(
                    "INSERT INTO team_external_ids(team_id, source, ext_id, ext_name)"
                    " VALUES (?,?,?,?)"
                    " ON CONFLICT(source, ext_id)"
                    " DO UPDATE SET team_id = excluded.team_id",
                    (team_id, source, str(ext_id), name),
                )
        return team_id

    def team_id_by_name(self, name: str) -> int | None:
        canon = canonical_team(name)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT team_id FROM teams WHERE name = ?", (canon,)
            ).fetchone()
        return int(row["team_id"]) if row else None

    def teams(self) -> pd.DataFrame:
        with self.connect() as conn:
            return pd.read_sql_query("SELECT * FROM teams ORDER BY name", conn)

    def merge_teams(self, source_name: str, target_name: str) -> int:
        """Muta tot ce tine de `source_name` pe `target_name` si sterge sursa.

        Necesar cand un provider introduce o varianta de nume pe care
        normalizatorul nu o cunostea inca (ex. "FC Rapid 1923" vs "Rapid
        Bucuresti"). Intoarce numarul de randuri mutate.
        """
        # Sursa se cauta dupa numele LITERAL din tabel: dupa ce normalizatorul
        # invata varianta, `canonical_team(source_name)` ar da chiar tinta si
        # fuziunea ar deveni un no-op.
        with self.connect() as conn:
            row = conn.execute(
                "SELECT team_id FROM teams WHERE name = ?", (source_name,)
            ).fetchone()
        src = int(row["team_id"]) if row else None
        dst = self.team_id_by_name(target_name)
        if src is None or dst is None or src == dst:
            return 0

        moved = 0
        with self.connect() as conn:
            for sql in (
                "UPDATE OR IGNORE matches SET home_team_id=? WHERE home_team_id=?",
                "UPDATE OR IGNORE matches SET away_team_id=? WHERE away_team_id=?",
                "UPDATE OR IGNORE match_team_stats SET team_id=? WHERE team_id=?",
                "UPDATE OR IGNORE player_match_stats SET team_id=? WHERE team_id=?",
                "UPDATE OR IGNORE player_season_stats SET team_id=? WHERE team_id=?",
                "UPDATE OR IGNORE players SET team_id=? WHERE team_id=?",
                "UPDATE OR IGNORE standings SET team_id=? WHERE team_id=?",
                "UPDATE OR IGNORE team_external_ids SET team_id=? WHERE team_id=?",
            ):
                moved += conn.execute(sql, (dst, src)).rowcount
            conn.execute("DELETE FROM team_external_ids WHERE team_id=?", (src,))
            conn.execute("DELETE FROM teams WHERE team_id=?", (src,))
        log.info("am unit '%s' in '%s' (%d randuri)", source_name, target_name, moved)
        return moved

    def orphan_teams(self) -> pd.DataFrame:
        """Echipe fara niciun meci - de obicei duplicate de nume nemapate."""
        sql = """
            SELECT t.team_id, t.name,
                   (SELECT GROUP_CONCAT(source || ':' || ext_name, ' | ')
                      FROM team_external_ids e WHERE e.team_id = t.team_id) AS surse
            FROM teams t
            WHERE NOT EXISTS (SELECT 1 FROM matches m
                              WHERE m.home_team_id = t.team_id
                                 OR m.away_team_id = t.team_id)
            ORDER BY t.name
        """
        with self.connect() as conn:
            return pd.read_sql_query(sql, conn)

    def deduplicate(self) -> dict[str, int]:
        """Reparare de intretinere, idempotenta.

        1. Uneste echipele al caror nume stocat nu mai e forma canonica -
           apare cand normalizatorul invata o varianta noua DUPA ce datele
           au fost deja scrise (ex. "(7.) FCV Farul").
        2. Uneste meciurile duplicate ramase in urma pasului 1: acelasi
           sezon, aceleasi echipe, la cel mult 3 zile distanta.

        Se poate rula oricand, de oricate ori.
        """
        merged_teams = merged_matches = 0

        # --- 1. echipe cu nume ne-canonic ---
        for name in self.teams()["name"].tolist():
            canon = canonical_team(name)
            if canon and canon != name:
                if self.team_id_by_name(canon) is None:
                    # tinta nu exista inca: redenumim pe loc
                    with self.connect() as conn:
                        conn.execute("UPDATE teams SET name=? WHERE name=?",
                                     (canon, name))
                else:
                    self.merge_teams(name, canon)
                merged_teams += 1

        # --- 2. meciuri duplicate ---
        sql = """
            SELECT a.match_id AS keep_id, b.match_id AS drop_id
            FROM matches a
            JOIN matches b
              ON a.season = b.season
             AND a.home_team_id = b.home_team_id
             AND a.away_team_id = b.away_team_id
             AND a.match_id < b.match_id
             AND abs(julianday(substr(a.utc_date, 1, 10))
                   - julianday(substr(b.utc_date, 1, 10))) <= 3
        """
        with self.connect() as conn:
            pairs = [(int(r["keep_id"]), int(r["drop_id"]))
                     for r in conn.execute(sql).fetchall()]

        for keep_id, drop_id in pairs:
            if not self._match_exists(drop_id):
                continue
            self._absorb_match(keep_id, drop_id)
            merged_matches += 1

        log.info("deduplicare: %d echipe, %d meciuri", merged_teams, merged_matches)
        return {"echipe_unite": merged_teams, "meciuri_unite": merged_matches}

    def _match_exists(self, match_id: int) -> bool:
        with self.connect() as conn:
            return conn.execute(
                "SELECT 1 FROM matches WHERE match_id=?", (match_id,)
            ).fetchone() is not None

    def _absorb_match(self, keep_id: int, drop_id: int) -> None:
        """Muta informatia din meciul `drop_id` in `keep_id`, apoi il sterge."""
        with self.connect() as conn:
            keep = conn.execute("SELECT * FROM matches WHERE match_id=?",
                                (keep_id,)).fetchone()
            drop = conn.execute("SELECT * FROM matches WHERE match_id=?",
                                (drop_id,)).fetchone()
            if keep is None or drop is None:
                return

            # completam campurile goale din meciul pastrat
            updates = {}
            for col in ("home_goals", "away_goals", "ht_home_goals",
                        "ht_away_goals", "stage", "round", "venue", "referee"):
                if keep[col] is None and drop[col] is not None:
                    updates[col] = drop[col]
            if keep["status"] != "finished" and drop["status"] == "finished":
                updates["status"] = "finished"
            if updates:
                sets = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(f"UPDATE matches SET {sets} WHERE match_id = ?",
                             [*updates.values(), keep_id])

            # mutam tot ce atarna de meciul care dispare
            for table in ("match_team_stats", "player_match_stats", "odds",
                          "predictions", "match_external_ids"):
                conn.execute(
                    f"UPDATE OR IGNORE {table} SET match_id=? WHERE match_id=?",
                    (keep_id, drop_id))
            conn.execute("DELETE FROM matches WHERE match_id=?", (drop_id,))

    def player_season_stats_df(self, season: int | None = None) -> pd.DataFrame:
        params: list[Any] = []
        clause = ""
        if season is not None:
            clause = "WHERE s.season = ?"
            params.append(season)
        sql = f"""
            SELECT s.*, p.name AS player, p.position, p.nationality,
                   t.name AS team
            FROM player_season_stats s
            JOIN players p ON p.player_id = s.player_id
            LEFT JOIN teams t ON t.team_id = s.team_id
            {clause}
            ORDER BY s.goals DESC, s.minutes DESC
        """
        with self.connect() as conn:
            return pd.read_sql_query(sql, conn, params=params)

    # ------------------------------------------------------------------
    # Meciuri
    # ------------------------------------------------------------------
    def upsert_match(
        self,
        *,
        season: int,
        utc_date: str,
        home: str,
        away: str,
        home_goals: int | None = None,
        away_goals: int | None = None,
        ht_home_goals: int | None = None,
        ht_away_goals: int | None = None,
        status: str = "scheduled",
        stage: str | None = None,
        round_: str | None = None,
        venue: str | None = None,
        referee: str | None = None,
        source: str | None = None,
        ext_id: str | None = None,
    ) -> int:
        """Insereaza sau imbogateste un meci; uneste automat inregistrarile
        venite de la provideri diferiti pentru acelasi meci."""
        home_id = self.upsert_team(home)
        away_id = self.upsert_team(away)
        date_key = str(utc_date)[:10]

        with self.connect() as conn:
            match_id: int | None = None

            # 1) identificare prin ID extern (cel mai sigur)
            if source and ext_id:
                row = conn.execute(
                    "SELECT match_id FROM match_external_ids"
                    " WHERE source=? AND ext_id=?",
                    (source, str(ext_id)),
                ).fetchone()
                if row:
                    match_id = int(row["match_id"])

            # 2) identificare prin (sezon, echipe, zi) - uneste providerii
            if match_id is None:
                row = conn.execute(
                    "SELECT match_id FROM matches WHERE season=? AND home_team_id=?"
                    " AND away_team_id=? AND substr(utc_date,1,10)=?",
                    (season, home_id, away_id, date_key),
                ).fetchone()
                if row:
                    match_id = int(row["match_id"])

            # 3) acelasi cuplu de echipe in sezon, la +/- 3 zile (reprogramari)
            if match_id is None:
                row = conn.execute(
                    "SELECT match_id FROM matches WHERE season=? AND home_team_id=?"
                    " AND away_team_id=?"
                    " AND abs(julianday(substr(utc_date,1,10)) - julianday(?)) <= 3",
                    (season, home_id, away_id, date_key),
                ).fetchone()
                if row:
                    match_id = int(row["match_id"])

            if match_id is None:
                cur = conn.execute(
                    "INSERT INTO matches(season, stage, round, utc_date, home_team_id,"
                    " away_team_id, home_goals, away_goals, ht_home_goals,"
                    " ht_away_goals, status, venue, referee)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (season, stage, round_, utc_date, home_id, away_id, home_goals,
                     away_goals, ht_home_goals, ht_away_goals, status, venue, referee),
                )
                match_id = int(cur.lastrowid)
            else:
                # completam doar campurile care aduc informatie noua
                existing = conn.execute(
                    "SELECT * FROM matches WHERE match_id=?", (match_id,)
                ).fetchone()
                updates: dict[str, Any] = {}
                candidate = {
                    "home_goals": home_goals, "away_goals": away_goals,
                    "ht_home_goals": ht_home_goals, "ht_away_goals": ht_away_goals,
                    "stage": stage, "round": round_, "venue": venue,
                    "referee": referee,
                }
                for key, value in candidate.items():
                    if value is not None and existing[key] is None:
                        updates[key] = value
                if home_goals is not None and existing["status"] != "finished":
                    updates["status"] = "finished"
                elif status == "finished" and existing["status"] != "finished":
                    updates["status"] = status
                if updates:
                    sets = ", ".join(f"{k} = ?" for k in updates)
                    conn.execute(
                        f"UPDATE matches SET {sets} WHERE match_id = ?",
                        [*updates.values(), match_id],
                    )

            if source and ext_id:
                conn.execute(
                    "INSERT OR REPLACE INTO match_external_ids"
                    "(match_id, source, ext_id) VALUES (?,?,?)",
                    (match_id, source, str(ext_id)),
                )
        return int(match_id)

    def match_id_by_external(self, source: str, ext_id: str) -> int | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT match_id FROM match_external_ids WHERE source=? AND ext_id=?",
                (source, str(ext_id)),
            ).fetchone()
        return int(row["match_id"]) if row else None

    # ------------------------------------------------------------------
    # Statistici de echipa
    # ------------------------------------------------------------------
    def upsert_team_stats(
        self,
        match_id: int,
        team_id: int,
        is_home: bool,
        stats: dict[str, Any],
        source: str = "",
    ) -> None:
        clean = {k: stats.get(k) for k in TEAM_STAT_COLUMNS}
        cols = ["match_id", "team_id", "is_home", *TEAM_STAT_COLUMNS, "source"]
        vals = [match_id, team_id, int(is_home), *clean.values(), source]
        # COALESCE: un provider fara valoare nu sterge valoarea altui provider
        updates = ", ".join(
            f"{c} = COALESCE(excluded.{c}, match_team_stats.{c})"
            for c in TEAM_STAT_COLUMNS
        )
        placeholders = ", ".join(["?"] * len(cols))
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO match_team_stats({', '.join(cols)})"
                f" VALUES ({placeholders})"
                f" ON CONFLICT(match_id, team_id) DO UPDATE SET {updates}",
                vals,
            )

    # ------------------------------------------------------------------
    # Jucatori
    # ------------------------------------------------------------------
    def upsert_player(
        self,
        name: str,
        team_id: int | None,
        *,
        source: str | None = None,
        ext_id: str | None = None,
        **fields: Any,
    ) -> int:
        name = str(name).strip()
        with self.connect() as conn:
            if source and ext_id:
                row = conn.execute(
                    "SELECT player_id FROM player_external_ids"
                    " WHERE source=? AND ext_id=?",
                    (source, str(ext_id)),
                ).fetchone()
                if row:
                    player_id = int(row["player_id"])
                    # Reimprospatam numele si echipa: un jucator identificat
                    # prin ID extern poate fi transferat, iar parserul poate
                    # sa fi invatat sa curete numele mai bine intre timp.
                    # OR IGNORE protejeaza constrangerea UNIQUE(name, team_id).
                    if team_id:
                        conn.execute(
                            "UPDATE OR IGNORE players SET team_id=?"
                            " WHERE player_id=?", (team_id, player_id))
                    if name:
                        conn.execute(
                            "UPDATE OR IGNORE players SET name=?"
                            " WHERE player_id=?", (name, player_id))
                    position = fields.get("position")
                    if position:
                        conn.execute(
                            "UPDATE players SET position=? WHERE player_id=?",
                            (position, player_id))
                    return player_id

            row = conn.execute(
                "SELECT player_id FROM players WHERE name=?"
                " AND (team_id = ? OR team_id IS NULL)",
                (name, team_id),
            ).fetchone()
            if row:
                player_id = int(row["player_id"])
            else:
                cur = conn.execute(
                    "INSERT INTO players(name, team_id, position, birth_date,"
                    " nationality, height_cm, photo_url) VALUES (?,?,?,?,?,?,?)",
                    (name, team_id, fields.get("position"), fields.get("birth_date"),
                     fields.get("nationality"), fields.get("height_cm"),
                     fields.get("photo_url")),
                )
                player_id = int(cur.lastrowid)

            if source and ext_id:
                conn.execute(
                    "INSERT OR REPLACE INTO player_external_ids"
                    "(player_id, source, ext_id) VALUES (?,?,?)",
                    (player_id, source, str(ext_id)),
                )
        return player_id

    def upsert_player_stats(
        self,
        match_id: int,
        player_id: int,
        team_id: int,
        stats: dict[str, Any],
        source: str = "",
    ) -> None:
        clean = {k: stats.get(k) for k in PLAYER_STAT_COLUMNS}
        cols = ["match_id", "player_id", "team_id", *PLAYER_STAT_COLUMNS, "source"]
        vals = [match_id, player_id, team_id, *clean.values(), source]
        updates = ", ".join(
            f"{c} = COALESCE(excluded.{c}, player_match_stats.{c})"
            for c in PLAYER_STAT_COLUMNS
        )
        placeholders = ", ".join(["?"] * len(cols))
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO player_match_stats({', '.join(cols)})"
                f" VALUES ({placeholders})"
                f" ON CONFLICT(match_id, player_id) DO UPDATE SET {updates}",
                vals,
            )

    # ------------------------------------------------------------------
    # Cote
    # ------------------------------------------------------------------
    def upsert_odds(self, rows: Iterable[tuple]) -> int:
        """Scrie cote. Randurile pot avea 5 elemente (fara deschidere) sau 6.

        Pretul se actualizeaza la fiecare rulare - el e "cota de acum". Dar
        **deschiderea se scrie o singura data**: e prin definitie prima cota
        vazuta, iar suprascrierea ei ar sterge tocmai diferenta care ne
        intereseaza. `COALESCE` pastreaza ce era deja.
        """
        rows = [tuple(r) + (None,) * (6 - len(r)) for r in rows]
        if not rows:
            return 0
        now = _utcnow()
        with self.connect() as conn:
            conn.executemany(
                "INSERT INTO odds(match_id, bookmaker, market, selection,"
                " price, opening, updated_at) VALUES (?,?,?,?,?,?,'" + now + "')"
                " ON CONFLICT(match_id, bookmaker, market, selection)"
                " DO UPDATE SET price = excluded.price,"
                "   opening = COALESCE(odds.opening, excluded.opening),"
                "   updated_at = excluded.updated_at",
                rows,
            )
        return len(rows)

    # ------------------------------------------------------------------
    # Clasament
    # ------------------------------------------------------------------
    def upsert_standing(self, season: int, stage: str, team_id: int,
                        **fields: Any) -> None:
        cols = ["season", "stage", "team_id", "rank", "played", "wins", "draws",
                "losses", "goals_for", "goals_against", "points", "form"]
        vals = [season, stage, team_id] + [fields.get(c) for c in cols[3:]]
        placeholders = ", ".join(["?"] * len(cols))
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols[3:])
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO standings({', '.join(cols)}) VALUES ({placeholders})"
                f" ON CONFLICT(season, stage, team_id) DO UPDATE SET {updates}",
                vals,
            )

    def standings_df(self, season: int | None = None) -> pd.DataFrame:
        clause, params = "", []
        if season is not None:
            clause = "WHERE s.season = ?"
            params.append(season)
        sql = f"""
            SELECT s.*, t.name AS team
            FROM standings s JOIN teams t ON t.team_id = s.team_id
            {clause}
            ORDER BY s.season DESC, s.rank
        """
        with self.connect() as conn:
            return pd.read_sql_query(sql, conn, params=params)

    # ------------------------------------------------------------------
    # Interogari pentru modele / dashboard
    # ------------------------------------------------------------------
    def matches_df(
        self,
        *,
        finished_only: bool = False,
        season: int | None = None,
        with_stats: bool = False,
    ) -> pd.DataFrame:
        where, params = [], []
        if finished_only:
            where.append("m.status = 'finished' AND m.home_goals IS NOT NULL")
        if season is not None:
            where.append("m.season = ?")
            params.append(season)
        clause = ("WHERE " + " AND ".join(where)) if where else ""

        sql = f"""
            SELECT m.match_id, m.season, m.stage, m.round, m.utc_date, m.status,
                   m.venue, m.referee,
                   m.home_team_id, ht.name AS home_team,
                   m.away_team_id, at.name AS away_team,
                   m.home_goals, m.away_goals, m.ht_home_goals, m.ht_away_goals
            FROM matches m
            JOIN teams ht ON ht.team_id = m.home_team_id
            JOIN teams at ON at.team_id = m.away_team_id
            {clause}
            ORDER BY m.utc_date
        """
        with self.connect() as conn:
            df = pd.read_sql_query(sql, conn, params=params)
            if with_stats and not df.empty:
                stats = pd.read_sql_query("SELECT * FROM match_team_stats", conn)
                if not stats.empty:
                    home = stats[stats.is_home == 1].drop(
                        columns=["is_home", "team_id"])
                    away = stats[stats.is_home == 0].drop(
                        columns=["is_home", "team_id"])
                    home = home.rename(columns={
                        c: f"home_{c}" for c in home.columns if c != "match_id"})
                    away = away.rename(columns={
                        c: f"away_{c}" for c in away.columns if c != "match_id"})
                    df = df.merge(home, on="match_id", how="left")
                    df = df.merge(away, on="match_id", how="left")

        if not df.empty:
            df["date"] = pd.to_datetime(df["utc_date"], errors="coerce", utc=True)
            df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
        return df

    def team_stats_df(self, season: int | None = None) -> pd.DataFrame:
        """O linie per (meci, echipa), cu adversarul si indicatorul de teren."""
        params: list[Any] = []
        clause = ""
        if season is not None:
            clause = "WHERE m.season = ?"
            params.append(season)
        sql = f"""
            SELECT s.*, m.season, m.utc_date, m.stage, m.round, m.status,
                   t.name AS team,
                   CASE WHEN s.is_home = 1 THEN at.name ELSE ht.name END AS opponent,
                   CASE WHEN s.is_home = 1 THEN m.home_goals ELSE m.away_goals END
                        AS goals_for,
                   CASE WHEN s.is_home = 1 THEN m.away_goals ELSE m.home_goals END
                        AS goals_against
            FROM match_team_stats s
            JOIN matches m ON m.match_id = s.match_id
            JOIN teams t   ON t.team_id  = s.team_id
            JOIN teams ht  ON ht.team_id = m.home_team_id
            JOIN teams at  ON at.team_id = m.away_team_id
            {clause}
            ORDER BY m.utc_date
        """
        with self.connect() as conn:
            df = pd.read_sql_query(sql, conn, params=params)
        if not df.empty:
            df["date"] = pd.to_datetime(df["utc_date"], errors="coerce", utc=True)
            # xG folosibil: cel publicat de furnizor, iar unde lipseste, cel
            # reconstruit din zonele de sut (vezi modeling/xg.py)
            if "xg" in df.columns and "xg_estimated" in df.columns:
                df["xg_any"] = df["xg"].fillna(df["xg_estimated"])
        return df

    def player_stats_df(self, season: int | None = None) -> pd.DataFrame:
        params: list[Any] = []
        clause = ""
        if season is not None:
            clause = "WHERE m.season = ?"
            params.append(season)
        sql = f"""
            SELECT ps.*, p.name AS player, p.position AS main_position,
                   p.nationality, p.birth_date,
                   t.name AS team, m.season, m.utc_date, m.stage,
                   CASE WHEN m.home_team_id = ps.team_id THEN at.name ELSE ht.name END
                        AS opponent
            FROM player_match_stats ps
            JOIN players p ON p.player_id = ps.player_id
            JOIN teams   t ON t.team_id   = ps.team_id
            JOIN matches m ON m.match_id  = ps.match_id
            JOIN teams ht  ON ht.team_id  = m.home_team_id
            JOIN teams at  ON at.team_id  = m.away_team_id
            {clause}
            ORDER BY m.utc_date
        """
        with self.connect() as conn:
            df = pd.read_sql_query(sql, conn, params=params)
        if not df.empty:
            df["date"] = pd.to_datetime(df["utc_date"], errors="coerce", utc=True)
        return df

    def odds_df(self) -> pd.DataFrame:
        with self.connect() as conn:
            return pd.read_sql_query("SELECT * FROM odds", conn)

    def upcoming_matches(self, limit: int = 50) -> pd.DataFrame:
        sql = """
            SELECT m.match_id, m.season, m.round, m.stage, m.utc_date,
                   ht.name AS home_team, at.name AS away_team, m.venue
            FROM matches m
            JOIN teams ht ON ht.team_id = m.home_team_id
            JOIN teams at ON at.team_id = m.away_team_id
            WHERE m.status != 'finished' AND m.home_goals IS NULL
            ORDER BY m.utc_date
            LIMIT ?
        """
        with self.connect() as conn:
            df = pd.read_sql_query(sql, conn, params=[limit])
        if not df.empty:
            df["date"] = pd.to_datetime(df["utc_date"], errors="coerce", utc=True)
        return df

    # ------------------------------------------------------------------
    # Predictii salvate
    # ------------------------------------------------------------------
    def save_predictions(self, rows: Iterable[tuple]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        now = _utcnow()
        payload = [(*r, now) for r in rows]
        with self.connect() as conn:
            conn.executemany(
                "INSERT INTO predictions(match_id, model, market, selection,"
                " probability, created_at) VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(match_id, model, market, selection)"
                " DO UPDATE SET probability = excluded.probability,"
                " created_at = excluded.created_at",
                payload,
            )
        return len(payload)

    # ------------------------------------------------------------------
    # Jurnal de ingestie / rezumat
    # ------------------------------------------------------------------
    def log_ingest(self, source: str, scope: str, rows: int, status: str,
                   message: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO ingest_log(source, scope, started_at, finished_at,"
                " rows, status, message) VALUES (?,?,?,?,?,?,?)",
                (source, scope, _utcnow(), _utcnow(), rows, status, message[:500]),
            )

    def ingest_log_df(self, limit: int = 50) -> pd.DataFrame:
        with self.connect() as conn:
            return pd.read_sql_query(
                "SELECT * FROM ingest_log ORDER BY id DESC LIMIT ?",
                conn, params=[limit],
            )

    def summary(self) -> dict[str, int]:
        queries = {
            "echipe": "SELECT COUNT(*) c FROM teams",
            "meciuri": "SELECT COUNT(*) c FROM matches",
            "meciuri_jucate":
                "SELECT COUNT(*) c FROM matches WHERE status='finished'",
            "meciuri_cu_statistici":
                "SELECT COUNT(DISTINCT match_id) c FROM match_team_stats",
            "jucatori": "SELECT COUNT(*) c FROM players",
            "aparitii_jucatori": "SELECT COUNT(*) c FROM player_match_stats",
            "cote": "SELECT COUNT(*) c FROM odds",
        }
        out: dict[str, int] = {}
        with self.connect() as conn:
            for key, sql in queries.items():
                out[key] = int(conn.execute(sql).fetchone()["c"])
        return out

    # ------------------------------------------------------------------
    # Disponibilitatea jucatorilor (accidentari, suspendari)
    # ------------------------------------------------------------------
    def replace_availability(self, team_id: int,
                             rows: Iterable[dict[str, Any]],
                             source: str = "") -> int:
        """Inlocuieste lista de absenti a unei echipe.

        Se sterge tot si se rescrie: un jucator care s-a insanatosit trebuie sa
        dispara din lista, nu sa ramana marcat indisponibil la nesfarsit.
        """
        rows = list(rows)
        now = _utcnow()
        with self.connect() as conn:
            conn.execute("DELETE FROM player_availability WHERE team_id = ?",
                         (team_id,))
            conn.executemany(
                "INSERT OR REPLACE INTO player_availability(player_id, team_id,"
                " kind, reason, since, expected_return, missed_matches,"
                " updated_at, source) VALUES (?,?,?,?,?,?,?,?,?)",
                [(r["player_id"], team_id, r.get("kind", "accidentare"),
                  r.get("reason"), r.get("since"), r.get("expected_return"),
                  r.get("missed_matches"), now, source) for r in rows],
            )
        return len(rows)

    def departed_players(self) -> set[str]:
        """Jucatorii care au aparut in meciuri, dar nu mai sunt in niciun lot.

        Sunt cei plecati din liga. Raman in baza cu tot istoricul lor - si e
        corect sa ramana - dar nu mai au ce cauta in predictii pentru fostul
        club.
        """
        sql = """
            SELECT DISTINCT p.name FROM players p
            WHERE (
                    p.team_id IS NULL
                    AND EXISTS (SELECT 1 FROM player_match_stats s
                                 WHERE s.player_id = p.player_id)
                  )
               -- si cei marcati de mana: uneori omul stie inaintea surselor
               OR EXISTS (SELECT 1 FROM manual_departures d
                           WHERE d.player_id = p.player_id)
        """
        with self.connect() as conn:
            return {str(r["name"]) for r in conn.execute(sql) if r["name"]}

    def mark_departed(self, player_id: int, note: str | None = None) -> None:
        """Marcheaza manual un jucator ca plecat. Reversibil."""
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO manual_departures(player_id, noted_at, note)"
                " VALUES (?,?,?) ON CONFLICT(player_id) DO UPDATE SET"
                "   noted_at = excluded.noted_at, note = excluded.note",
                (int(player_id), _utcnow(), note))

    def unmark_departed(self, player_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM manual_departures WHERE player_id = ?",
                         (int(player_id),))

    def manual_departures(self) -> pd.DataFrame:
        """Jucatorii marcati de mana ca plecati, cu echipa la care apar inca."""
        sql = """
            SELECT d.player_id, p.name AS player_name, t.name AS echipa,
                   d.noted_at, d.note
            FROM manual_departures d
            JOIN players p ON p.player_id = d.player_id
            LEFT JOIN teams t ON t.team_id = p.team_id
            ORDER BY d.noted_at DESC
        """
        with self.connect() as conn:
            return pd.read_sql_query(sql, conn)

    def reported_departures(self, *, min_sources: int = 1) -> pd.DataFrame:
        """Jucatori dati plecati de presa, dar inca in lot la Transfermarkt.

        Transfermarkt isi actualizeaza loturile cu intarziere - Tanase apare
        acolo si dupa ce trei publicatii au scris ca a semnat cu Omonia. Presa
        e mai rapida, dar mai putin sigura; de aceea cerem ca articolul sa
        afirme plecarea ca fapt ("confirmat"), nu ca zvon, si aratam din cate
        surse vine.
        """
        sql = """
            SELECT f.player_id, f.player_name, t.name AS echipa,
                   COUNT(DISTINCT a.source) AS surse,
                   MAX(a.published_at) AS ultima,
                   MIN(f.evidence) AS dovada
            FROM news_facts f
            JOIN news_articles a ON a.article_id = f.article_id
            JOIN players p ON p.player_id = f.player_id
            JOIN teams t ON t.team_id = p.team_id
            WHERE f.kind = 'transfer_plecare' AND f.certainty = 'confirmat'
              AND p.team_id IS NOT NULL
              -- Clubul din propozitie trebuie sa fie chiar clubul lui de acum.
              -- Altfel prindem plecari de la fostele echipe: "Omar El Sawy a
              -- dezvaluit de ce a plecat de la Rapid" - el e la UTA Arad.
              AND f.team_id = p.team_id
              -- O plecare anulata de o revenire ulterioara nu mai e o plecare.
              -- Cretu s-a despartit de FCSB, apoi Becali a revenit asupra
              -- deciziei si a fost reprimit.
              AND NOT EXISTS (
                    SELECT 1 FROM news_facts r
                    JOIN news_articles ra ON ra.article_id = r.article_id
                    WHERE r.player_id = f.player_id
                      AND r.kind = 'revenire' AND r.certainty = 'confirmat'
                      AND ra.published_at >= a.published_at)
            GROUP BY f.player_id, f.player_name, t.name
            HAVING surse >= ?
            ORDER BY surse DESC, ultima DESC
        """
        with self.connect() as conn:
            frame = pd.read_sql_query(sql, conn, params=[int(min_sources)])
        if frame.empty:
            return frame
        keep = [not _is_destination(str(r.dovada or ""), str(r.echipa))
                for r in frame.itertuples()]
        return frame[keep].reset_index(drop=True)

    def detach_missing_players(self, team_id: int,
                               still_here: Iterable[int]) -> int:
        """Scoate din lot jucatorii care nu mai apar in el.

        NU sterge jucatorul: aparitiile lui din meciurile trecute raman, si e
        corect sa ramana. Doar legatura cu echipa curenta dispare (`team_id`
        devine NULL), ca sa nu mai fie propus in predictii pentru un club la
        care nu mai joaca.
        """
        keep = [int(p) for p in still_here]
        if not keep:
            return 0
        marks = ",".join("?" * len(keep))
        with self.connect() as conn:
            cursor = conn.execute(
                f"UPDATE players SET team_id = NULL"
                f" WHERE team_id = ? AND player_id NOT IN ({marks})",
                [int(team_id), *keep])
            gone = cursor.rowcount or 0
        if gone:
            log.info("au plecat de la echipa %s: %d jucatori", team_id, gone)
        return gone

    def availability_df(self) -> pd.DataFrame:
        sql = """
            SELECT a.*, p.name AS player, t.name AS team
            FROM player_availability a
            JOIN players p ON p.player_id = a.player_id
            LEFT JOIN teams t ON t.team_id = a.team_id
            ORDER BY t.name, p.name
        """
        with self.connect() as conn:
            return pd.read_sql_query(sql, conn)

    def unavailable_players(self) -> set[str]:
        """Numele jucatorilor indisponibili, pentru filtrarea din model."""
        frame = self.availability_df()
        return set(frame["player"]) if not frame.empty else set()

    # ------------------------------------------------------------------
    # Istoricul absentelor
    # ------------------------------------------------------------------
    def snapshot_availability(self, *, source: str = "transfermarkt",
                              taken_at: str | None = None) -> int:
        """Ingheata starea de acum a absentelor. Intoarce cati jucatori lipsesc.

        Transfermarkt publica doar prezentul. Fara fotografii periodice nu vom
        sti niciodata cine lipsea la meciurile deja jucate, deci un ajustaj
        pentru absente nu s-ar putea masura vreodata. Antetul se scrie chiar si
        cand nu lipseste nimeni, ca sa deosebim "nimeni absent" de "n-am privit".
        """
        stamp = taken_at or _utcnow()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT player_id, team_id, kind, reason, expected_return"
                " FROM player_availability").fetchall()
            teams = len({r["team_id"] for r in rows if r["team_id"] is not None})
            cursor = conn.execute(
                "INSERT INTO availability_snapshots(taken_at, source, teams,"
                " players) VALUES (?,?,?,?)", (stamp, source, teams, len(rows)))
            snapshot_id = cursor.lastrowid
            conn.executemany(
                "INSERT INTO availability_snapshot_rows(snapshot_id, player_id,"
                " team_id, kind, reason, expected_return) VALUES (?,?,?,?,?,?)",
                [(snapshot_id, r["player_id"], r["team_id"], r["kind"],
                  r["reason"], r["expected_return"]) for r in rows])
        return len(rows)

    def availability_on(self, moment: str) -> pd.DataFrame:
        """Cine era indisponibil la un moment dat, dupa cea mai apropiata
        fotografie dinaintea lui.

        Intoarce un tabel gol si daca nu lipsea nimeni, si daca nu exista nicio
        fotografie inainte de acel moment - deosebirea se face cu
        `had_availability_snapshot`.
        """
        sql = """
            SELECT r.player_id, r.team_id, r.kind, r.reason, r.expected_return,
                   p.name AS player, t.name AS team, s.taken_at
            FROM availability_snapshot_rows r
            JOIN availability_snapshots s ON s.snapshot_id = r.snapshot_id
            LEFT JOIN players p ON p.player_id = r.player_id
            LEFT JOIN teams t ON t.team_id = r.team_id
            WHERE s.snapshot_id = (
                SELECT snapshot_id FROM availability_snapshots
                 WHERE taken_at <= ? ORDER BY taken_at DESC LIMIT 1)
            ORDER BY t.name, p.name
        """
        with self.connect() as conn:
            return pd.read_sql_query(sql, conn, params=(moment,))

    def had_availability_snapshot(self, moment: str) -> bool:
        """Exista vreo fotografie facuta inaintea momentului dat?"""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM availability_snapshots WHERE taken_at <= ?"
                " LIMIT 1", (moment,)).fetchone()
        return row is not None

    def current_season_team_names(self) -> list[str]:
        """Numele echipelor care joaca in sezonul curent."""
        sql = """
            SELECT DISTINCT t.name FROM teams t
            JOIN matches m ON t.team_id IN (m.home_team_id, m.away_team_id)
            WHERE m.season = (SELECT MAX(season) FROM matches)
            ORDER BY t.name
        """
        with self.connect() as conn:
            return [str(r["name"]) for r in conn.execute(sql).fetchall()]

    # ------------------------------------------------------------------
    # Stiri
    # ------------------------------------------------------------------
    def upsert_news_article(self, *, url: str, source: str, title: str,
                            summary: str | None = None, body: str | None = None,
                            category: str | None = None,
                            published_at: str | None = None) -> int:
        """Scrie articolul si intoarce id-ul lui. Reluarea nu creeaza duplicate."""
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO news_articles(url, source, title, summary, body,"
                " category, published_at, fetched_at) VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(url) DO UPDATE SET"
                "   title = excluded.title,"
                "   summary = COALESCE(excluded.summary, news_articles.summary),"
                # textul mai lung castiga: fluxul da un rezumat, pagina da tot
                "   body = CASE WHEN LENGTH(COALESCE(excluded.body,'')) >"
                "               LENGTH(COALESCE(news_articles.body,''))"
                "          THEN excluded.body ELSE news_articles.body END,"
                "   fetched_at = excluded.fetched_at",
                (url, source, title, summary, body, category, published_at,
                 _utcnow()))
            row = conn.execute(
                "SELECT article_id FROM news_articles WHERE url = ?",
                (url,)).fetchone()
        return int(row["article_id"])

    def save_news_facts(self, article_id: int, facts: Iterable[dict[str, Any]],
                        *, published_at: str | None = None) -> int:
        """Faptele extrase dintr-un articol. Reluarea le rescrie, nu le dubleaza."""
        facts = list(facts)
        if not facts:
            return 0
        now = _utcnow()
        with self.connect() as conn:
            rows = []
            for fact in facts:
                team_id = None
                if fact.get("team"):
                    found = conn.execute(
                        "SELECT team_id FROM teams WHERE name = ?",
                        (fact["team"],)).fetchone()
                    team_id = int(found["team_id"]) if found else None
                rows.append((
                    article_id, fact["kind"], team_id, fact.get("player_id"),
                    fact.get("player_name"), fact.get("detail"),
                    fact.get("certainty", "zvon"), fact.get("evidence"),
                    published_at, now))
            conn.executemany(
                "INSERT INTO news_facts(article_id, kind, team_id, player_id,"
                " player_name, detail, certainty, evidence, published_at,"
                " created_at) VALUES (?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(article_id, kind, player_name) DO UPDATE SET"
                "   certainty = excluded.certainty,"
                "   evidence = excluded.evidence,"
                "   team_id = COALESCE(excluded.team_id, news_facts.team_id),"
                "   player_id = COALESCE(excluded.player_id, news_facts.player_id)",
                rows)
        return len(rows)

    def news_facts_df(self, *, days: int = 14,
                      kinds: list[str] | None = None) -> pd.DataFrame:
        """Faptele recente, cu articolul si sursa din care provin."""
        sql = """
            SELECT f.fact_id, f.kind, f.certainty, f.player_name, f.player_id,
                   f.detail, f.evidence, f.published_at,
                   t.name AS team, a.title, a.url, a.source
            FROM news_facts f
            JOIN news_articles a ON a.article_id = f.article_id
            LEFT JOIN teams t ON t.team_id = f.team_id
            WHERE COALESCE(f.published_at, f.created_at) >=
                  datetime('now', ?)
        """
        params: list[Any] = [f"-{int(days)} days"]
        if kinds:
            sql += f" AND f.kind IN ({','.join('?' * len(kinds))})"
            params.extend(kinds)
        sql += " ORDER BY COALESCE(f.published_at, f.created_at) DESC"
        with self.connect() as conn:
            return pd.read_sql_query(sql, conn, params=params)

    def news_summary(self) -> dict[str, Any]:
        """Cate articole si fapte avem, si de cand."""
        with self.connect() as conn:
            articles = int(conn.execute(
                "SELECT COUNT(*) c FROM news_articles").fetchone()["c"])
            facts = int(conn.execute(
                "SELECT COUNT(*) c FROM news_facts").fetchone()["c"])
            latest = conn.execute(
                "SELECT MAX(published_at) m FROM news_articles").fetchone()["m"]
            by_kind = {str(r["kind"]): int(r["n"]) for r in conn.execute(
                "SELECT kind, COUNT(*) n FROM news_facts GROUP BY kind")}
            by_certainty = {str(r["certainty"]): int(r["n"]) for r in conn.execute(
                "SELECT certainty, COUNT(*) n FROM news_facts GROUP BY certainty")}
        return {"articole": articles, "fapte": facts, "cea_mai_noua": latest,
                "pe_tip": by_kind, "pe_incredere": by_certainty}

    def availability_history(self) -> pd.DataFrame:
        """Cate absente aveam la fiecare fotografie - pentru grafic si raport."""
        sql = """
            SELECT taken_at, source, teams, players
            FROM availability_snapshots ORDER BY taken_at
        """
        with self.connect() as conn:
            return pd.read_sql_query(sql, conn)


def _is_destination(evidence: str, club: str) -> bool:
    """Clubul e DESTINATIA transferului, nu locul de unde pleaca?

    "Birligea a fost cedat de CFR **la FCSB**" descrie o venire la FCSB, nu o
    plecare de acolo - dar are acelasi verb ca o plecare. Diferenta o face
    prepozitia: "de la X" inseamna dinspre X, "la Y" inseamna catre Y.
    """
    from ..utils.teamnames import strip_diacritics

    text = strip_diacritics(evidence).lower()
    name = strip_diacritics(club).lower()
    if not name or name not in text:
        return False
    at = text.find(name)
    inainte = text[max(0, at - 6):at]
    return " la " in inainte and "de la " not in inainte
