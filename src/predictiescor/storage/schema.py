"""Schema SQLite pentru PredictieScor."""
from __future__ import annotations

SCHEMA_VERSION = 8

DDL = """
PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------------
-- Echipe (cheia de unificare intre provideri este numele canonic)
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS teams (
    team_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,          -- nume canonic
    short_name TEXT,
    city       TEXT,
    founded    INTEGER,
    venue      TEXT,
    logo_url   TEXT
);

CREATE TABLE IF NOT EXISTS team_external_ids (
    team_id  INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
    source   TEXT NOT NULL,                   -- apifootball | sofascore | fbref
    ext_id   TEXT NOT NULL,
    ext_name TEXT,
    PRIMARY KEY (source, ext_id)
);

-- ------------------------------------------------------------------
-- Meciuri
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS matches (
    match_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    season        INTEGER NOT NULL,           -- anul de start (2024 = 2024/25)
    stage         TEXT,                       -- 'regular' | 'playoff' | 'playout'
    round         TEXT,
    utc_date      TEXT NOT NULL,              -- ISO 8601
    home_team_id  INTEGER NOT NULL REFERENCES teams(team_id),
    away_team_id  INTEGER NOT NULL REFERENCES teams(team_id),
    home_goals    INTEGER,
    away_goals    INTEGER,
    ht_home_goals INTEGER,
    ht_away_goals INTEGER,
    status        TEXT NOT NULL DEFAULT 'scheduled',  -- scheduled|finished|postponed
    venue         TEXT,
    referee       TEXT,
    UNIQUE (season, home_team_id, away_team_id, utc_date)
);

CREATE INDEX IF NOT EXISTS idx_matches_date   ON matches(utc_date);
CREATE INDEX IF NOT EXISTS idx_matches_season ON matches(season, status);
CREATE INDEX IF NOT EXISTS idx_matches_home   ON matches(home_team_id);
CREATE INDEX IF NOT EXISTS idx_matches_away   ON matches(away_team_id);

CREATE TABLE IF NOT EXISTS match_external_ids (
    match_id INTEGER NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
    source   TEXT NOT NULL,
    ext_id   TEXT NOT NULL,
    PRIMARY KEY (source, ext_id)
);

-- ------------------------------------------------------------------
-- Statistici de echipa pe meci (o linie per echipa per meci)
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS match_team_stats (
    match_id          INTEGER NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
    team_id           INTEGER NOT NULL REFERENCES teams(team_id),
    is_home           INTEGER NOT NULL,
    shots             REAL,
    shots_on_target   REAL,
    shots_off_target  REAL,
    shots_blocked     REAL,
    shots_inside_box  REAL,
    shots_outside_box REAL,
    corners           REAL,
    offsides          REAL,
    fouls             REAL,
    yellow_cards      REAL,
    red_cards         REAL,
    possession        REAL,   -- procent 0-100
    passes            REAL,
    passes_accurate   REAL,
    pass_accuracy     REAL,
    saves             REAL,
    xg                REAL,   -- publicat de furnizor (API-Football)
    xg_estimated      REAL,   -- reconstruit de noi din zonele de sut
    xga               REAL,
    big_chances       REAL,
    tackles           REAL,
    -- statistici suplimentare oferite de LPF (sursa oficiala a ligii)
    attacks           REAL,
    interceptions     REAL,
    duels_won         REAL,
    dribbles          REAL,
    free_kicks        REAL,
    passes_into_box   REAL,
    source            TEXT,
    PRIMARY KEY (match_id, team_id)
);

-- ------------------------------------------------------------------
-- Cote (pentru calibrare si calcul de valoare)
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS odds (
    match_id  INTEGER NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
    bookmaker TEXT NOT NULL,
    market    TEXT NOT NULL,     -- 1x2 | ou_2.5 | btts | ah_-0.5 ...
    selection TEXT NOT NULL,     -- home|draw|away|over|under|yes|no
    price     REAL NOT NULL,
    -- Cota la care casa a DESCHIS piata. Nu se suprascrie niciodata: miscarea
    -- de la deschidere la inchidere e cel mai sensibil semn ca piata a aflat
    -- ceva (o accidentare, formatia). Fara ea, fiecare rulare stergea definitiv
    -- pretul anterior si informatia se pierdea zilnic.
    opening    REAL,
    updated_at TEXT,
    PRIMARY KEY (match_id, bookmaker, market, selection)
);

-- ------------------------------------------------------------------
-- Jucatori
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS players (
    player_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    team_id     INTEGER REFERENCES teams(team_id),
    position    TEXT,
    birth_date  TEXT,
    nationality TEXT,
    height_cm   INTEGER,
    photo_url   TEXT,
    UNIQUE (name, team_id)
);

CREATE TABLE IF NOT EXISTS player_external_ids (
    player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
    source    TEXT NOT NULL,
    ext_id    TEXT NOT NULL,
    PRIMARY KEY (source, ext_id)
);

CREATE TABLE IF NOT EXISTS player_match_stats (
    match_id        INTEGER NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
    player_id       INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
    team_id         INTEGER NOT NULL REFERENCES teams(team_id),
    minutes         REAL,
    is_starter      INTEGER,
    position        TEXT,
    rating          REAL,
    goals           REAL,
    assists         REAL,
    shots           REAL,
    shots_on_target REAL,
    passes          REAL,
    key_passes      REAL,
    pass_accuracy   REAL,
    tackles         REAL,
    interceptions   REAL,
    duels_total     REAL,
    duels_won       REAL,
    dribbles_att    REAL,
    dribbles_succ   REAL,
    fouls_drawn     REAL,
    fouls_committed REAL,
    yellow_cards    REAL,
    red_cards       REAL,
    penalties_scored REAL,
    penalties_missed REAL,
    saves           REAL,
    goals_conceded  REAL,
    source          TEXT,
    PRIMARY KEY (match_id, player_id)
);

-- Agregate pe sezon (Transfermarkt da totaluri, nu date per meci)
CREATE TABLE IF NOT EXISTS player_season_stats (
    season           INTEGER NOT NULL,
    player_id        INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
    team_id          INTEGER REFERENCES teams(team_id),
    competition      TEXT NOT NULL DEFAULT 'liga1',
    appearances      REAL,
    minutes          REAL,
    goals            REAL,
    assists          REAL,
    yellow_cards     REAL,
    second_yellow    REAL,
    red_cards        REAL,
    subbed_on        REAL,
    subbed_off       REAL,
    points_per_game  REAL,
    market_value_eur REAL,
    source           TEXT,
    PRIMARY KEY (season, player_id, competition)
);

CREATE INDEX IF NOT EXISTS idx_pss_team ON player_season_stats(team_id, season);

-- Cine nu e disponibil: accidentari si suspendari (Transfermarkt).
-- Fara asta, modelul presupune ca tot lotul e apt, iar absenta unui golgheter
-- trece neobservata.
CREATE TABLE IF NOT EXISTS player_availability (
    player_id       INTEGER PRIMARY KEY REFERENCES players(player_id)
                        ON DELETE CASCADE,
    team_id         INTEGER REFERENCES teams(team_id),
    kind            TEXT NOT NULL,      -- accidentare | suspendare
    reason          TEXT,
    since           TEXT,
    expected_return TEXT,
    missed_matches  INTEGER,
    updated_at      TEXT NOT NULL,
    source          TEXT
);

CREATE INDEX IF NOT EXISTS idx_avail_team ON player_availability(team_id);

CREATE INDEX IF NOT EXISTS idx_pms_player ON player_match_stats(player_id);
CREATE INDEX IF NOT EXISTS idx_pms_team   ON player_match_stats(team_id);

-- ------------------------------------------------------------------
-- Clasament (snapshot per sezon)
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS standings (
    season   INTEGER NOT NULL,
    stage    TEXT NOT NULL DEFAULT 'regular',
    team_id  INTEGER NOT NULL REFERENCES teams(team_id),
    rank     INTEGER,
    played   INTEGER,
    wins     INTEGER,
    draws    INTEGER,
    losses   INTEGER,
    goals_for INTEGER,
    goals_against INTEGER,
    points   REAL,
    form     TEXT,
    PRIMARY KEY (season, stage, team_id)
);

-- ------------------------------------------------------------------
-- Predictii salvate (pentru urmarirea performantei in timp)
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS predictions (
    match_id    INTEGER NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
    model       TEXT NOT NULL,
    market      TEXT NOT NULL,
    selection   TEXT NOT NULL,
    probability REAL NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (match_id, model, market, selection)
);

-- ------------------------------------------------------------------
-- Bilete de pariuri generate de aplicatie
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    match_day     TEXT NOT NULL,       -- ziua pentru care e biletul (YYYY-MM-DD)
    strategy      TEXT NOT NULL,       -- sigur | echilibrat | valoare | cota mare ...
    label         TEXT,                -- numele afisat al biletului
    stake         REAL NOT NULL DEFAULT 10.0,
    total_odds    REAL,                -- produsul cotelor
    probability   REAL,                -- sansa estimata sa iasa tot biletul
    expected_value REAL,               -- valoarea asteptata la miza data
    status        TEXT NOT NULL DEFAULT 'deschis',  -- deschis|castigat|pierdut|anulat
    legs_won      INTEGER DEFAULT 0,
    legs_total    INTEGER DEFAULT 0,
    payout        REAL,                -- incasare efectiva (0 daca e pierdut)
    profit        REAL,
    settled_at    TEXT,
    model_version TEXT
);

CREATE INDEX IF NOT EXISTS idx_tickets_day ON tickets(match_day, status);

CREATE TABLE IF NOT EXISTS ticket_legs (
    leg_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id     INTEGER NOT NULL REFERENCES tickets(ticket_id) ON DELETE CASCADE,
    match_id      INTEGER NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
    market        TEXT NOT NULL,       -- 1X2 | total goluri 2.5 | cornere ...
    selection     TEXT NOT NULL,       -- gazda | peste | da | "Birligea marcheaza"
    description   TEXT,                -- text afisat, ca pe biletul real
    probability   REAL NOT NULL,       -- probabilitatea data de model
    fair_odds     REAL NOT NULL,       -- 1 / probabilitate
    offered_odds  REAL,                -- cota casei, daca o stim
    result        TEXT DEFAULT 'deschis',  -- castigat|pierdut|anulat|deschis
    actual_value  TEXT                 -- ce s-a intamplat de fapt
);

CREATE INDEX IF NOT EXISTS idx_legs_ticket ON ticket_legs(ticket_id);
CREATE INDEX IF NOT EXISTS idx_legs_match  ON ticket_legs(match_id);

-- Calibrarea invatata din pariurile decontate: pe ce piete se poate baza
-- modelul si pe care e prea increzator.
CREATE TABLE IF NOT EXISTS market_calibration (
    market        TEXT NOT NULL,
    bucket        TEXT NOT NULL,       -- intervalul de probabilitate, ex. "60-70%"
    predicted     REAL NOT NULL,       -- media probabilitatilor prezise
    observed      REAL NOT NULL,       -- frecventa reala
    n             INTEGER NOT NULL,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (market, bucket)
);

-- Scorul fiecarei strategii de bilet, actualizat dupa fiecare decontare.
CREATE TABLE IF NOT EXISTS strategy_performance (
    strategy      TEXT PRIMARY KEY,
    tickets       INTEGER DEFAULT 0,
    won           INTEGER DEFAULT 0,
    staked        REAL DEFAULT 0,
    returned      REAL DEFAULT 0,
    legs_total    INTEGER DEFAULT 0,
    legs_won      INTEGER DEFAULT 0,
    brier_sum     REAL DEFAULT 0,
    weight        REAL DEFAULT 1.0,    -- cat de des mai generam aceasta strategie
    updated_at    TEXT
);

-- ------------------------------------------------------------------
-- Istoricul absentelor
-- ------------------------------------------------------------------
-- Transfermarkt publica doar starea de ACUM: cine e accidentat astazi. Fara
-- istoric, un ajustaj pentru absente nu poate fi masurat niciodata - nu stim
-- cine lipsea la meciurile deja jucate. Salvam deci o fotografie la fiecare
-- actualizare, ca peste cateva luni sa existe setul de date care face
-- masuratoarea posibila.
--
-- Doua tabele, nu unul: antetul se scrie chiar si cand nu lipseste nimeni, ca
-- sa putem deosebi "nimeni nu era absent" de "nu ne-am uitat".
CREATE TABLE IF NOT EXISTS availability_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at    TEXT NOT NULL,
    source      TEXT,
    teams       INTEGER NOT NULL DEFAULT 0,
    players     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS availability_snapshot_rows (
    snapshot_id     INTEGER NOT NULL REFERENCES availability_snapshots(snapshot_id)
                        ON DELETE CASCADE,
    player_id       INTEGER REFERENCES players(player_id) ON DELETE CASCADE,
    team_id         INTEGER REFERENCES teams(team_id),
    kind            TEXT NOT NULL,
    reason          TEXT,
    expected_return TEXT
);

CREATE INDEX IF NOT EXISTS idx_avail_snap_time
    ON availability_snapshots(taken_at);
CREATE INDEX IF NOT EXISTS idx_avail_snap_rows
    ON availability_snapshot_rows(snapshot_id, team_id);

-- ------------------------------------------------------------------
-- Stiri: articole si faptele extrase din ele
-- ------------------------------------------------------------------
-- Pastram articolul intreg langa fapte din doua motive: sa nu reinterogam
-- site-ul cand imbunatatim extragerea, si ca fiecare fapt sa poata fi
-- verificat de om, propozitie cu propozitie.
CREATE TABLE IF NOT EXISTS news_articles (
    article_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    url          TEXT NOT NULL UNIQUE,
    source       TEXT NOT NULL,
    title        TEXT NOT NULL,
    summary      TEXT,
    body         TEXT,
    category     TEXT,
    published_at TEXT,
    fetched_at   TEXT NOT NULL
);

-- Un fapt = o afirmatie despre un jucator, cu increderea ei si cu propozitia
-- din care a iesit. `player_id` ramane NULL cand numele nu s-a putut lega
-- SIGUR de un jucator din baza - preferam sa nu stim decat sa ghicim.
CREATE TABLE IF NOT EXISTS news_facts (
    fact_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id  INTEGER NOT NULL REFERENCES news_articles(article_id)
                    ON DELETE CASCADE,
    kind        TEXT NOT NULL,   -- absenta|revenire|transfer_venire|
                                 -- transfer_plecare|formatie_probabila
    team_id     INTEGER REFERENCES teams(team_id),
    player_id   INTEGER REFERENCES players(player_id),
    player_name TEXT,
    detail      TEXT,
    certainty   TEXT NOT NULL,   -- confirmat | probabil | zvon
    evidence    TEXT,
    published_at TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE(article_id, kind, player_name)
);

CREATE INDEX IF NOT EXISTS idx_news_pub ON news_articles(published_at);
CREATE INDEX IF NOT EXISTS idx_news_facts_kind
    ON news_facts(kind, published_at);
CREATE INDEX IF NOT EXISTS idx_news_facts_player ON news_facts(player_id);

-- ------------------------------------------------------------------
-- Ce stie omul si sursele nu
-- ------------------------------------------------------------------
-- Transfermarkt isi actualizeaza loturile cu intarziere, iar presa nu scrie
-- despre orice plecare. Cand utilizatorul stie ca un jucator a plecat, o poate
-- spune aici, iar modelul il exclude ca pe oricare altul. Marcajul e reversibil
-- si nu sterge nimic - doar il scoate din predictii.
CREATE TABLE IF NOT EXISTS manual_departures (
    player_id INTEGER PRIMARY KEY REFERENCES players(player_id)
                  ON DELETE CASCADE,
    noted_at  TEXT NOT NULL,
    note      TEXT
);

-- ------------------------------------------------------------------
-- Metadate
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS ingest_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    source    TEXT NOT NULL,
    scope     TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    rows      INTEGER DEFAULT 0,
    status    TEXT,
    message   TEXT
);
"""
