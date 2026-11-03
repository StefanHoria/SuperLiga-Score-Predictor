***English** · [Română](README.ro.md)*

# SuperLiga Score Predictor — Romania's SuperLiga

A local statistical prediction app for the Romanian SuperLiga: results, goals,
corners, cards, shots, players and automatically generated **betting slips**,
checked after the matches and improved from their own results.

Everything runs on your computer. The database is an SQLite file in `data/`,
the interface starts on `localhost`, and the API key lives in `.env` (excluded
from git). The only outbound traffic is the requests that download the data.

---

## Quick start

```bash
pip install -r requirements.txt
```

```bash
cp .env.example .env
```

Fill in `APIFOOTBALL_KEY` in `.env` (free account at
[dashboard.api-football.com](https://dashboard.api-football.com/register)).
The app works without a key, but without detailed per-match statistics.

```bash
python scripts/fetch_data.py
```

```bash
python -m streamlit run src/predictiescor/app/main.py
```

It opens in the browser at `http://localhost:8501`.

---

## What the app looks like

| Page | What you get |
|---|---|
| **Overview** | League table, team strength according to the model, Elo evolution |
| **Match prediction** | Every market for any pair of teams: 1X2, exact score, over/under, both teams to score, Asian handicap, corners, cards, goalscorers |
| **Teams** | Full profile: form, home/away, statistics vs the league average, radar chart |
| **Players** | Rankings, squads, goalscorer predictions |
| **Next round** | Predictions for all scheduled matches |
| **Model performance** | Chronological backtest, calibration, comparison with market odds |
| **Data and sources** | Update buttons, which source works, how complete the data is, clean-up tools |
| **Betting slips** | Generation, checking, accuracy, learning, upsets |
| **Live** | Score of matches in progress, cards and corners so far, odds recalculated on the spot, the state of your picks and picks suggested from what is happening on the pitch |
| **News** | Absences, returns and transfers read from the press, with the supporting sentence next to each one |
| **Transfers** | Arrivals and departures per club, with the fees paid and received, and the resulting profit |

> The interface itself is in Romanian.

---

## Where the data comes from

The app combines seven sources. Each one only fills in the fields the others
have not provided, so a weak source never spoils a good one.

| Source | What it provides | Coverage | Status |
|---|---|---|---|
| **football-data.co.uk** | results + 1X2 odds | all seasons, **including the current one** | ✅ works |
| **LPF.ro** (official) | 23 statistics per match + line-ups, goalscorers, minutes played | **current season** | ✅ works |
| **Flashscore** | live score, final score immediately, half-time score, exact kick-off time | **current season** | ✅ works |
| **Flashscore — odds** | pre-match odds from 5 bookmakers: 1X2, over/under (7 lines), both teams to score, double chance | upcoming **and** past matches | ✅ works |
| **The press (RSS)** | absences, returns, transfers — as text, not as model data | from now on only | ✅ works |
| **Transfermarkt** | fixtures, results, squads, minutes, goals, cards, **injuries and suspensions** | all seasons, **including the current one** | ✅ works |
| **API-Football** | the same + xG and per-player statistics | **2022–2024 only** on the free plan | ✅ works, with limits |
| **Sofascore** | rich per-match statistics | — | ⛔ returns `403` to automated access |
| **FBref** | alternative xG, advanced statistics | — | ⛔ protected by Cloudflare |

### LPF.ro — the league's official source

It covers exactly the gap left by API-Football: detailed statistics for the
**current season**. For every match: shots (total, on target, blocked, from
outside the box), corners, yellow and red cards, possession, attacks, passes
(total, accurate, into the box), fouls committed and suffered, dribbles, free
kicks, interceptions, tackles, duels won.

Besides team statistics, LPF publishes **full line-ups and the match
timeline** — goals, cards and substitutions, each with its minute. From these
we rebuild, for every player: minutes played, starter or substitute, goals,
yellow and red cards. This is what lets the **goalscorer** market be settled for
the current season.

The pages are numbered sequentially (`lpf.ro/statistici/{id}`, 8 matches per
round), so the provider walks the range without needing a listing page.
`lpf_first_match_id` in `config.yaml` marks the start of the season — change it
when the next season starts.

**Own goals.** LPF publishes them with the same icon as regular goals, on the
side of the player who scored them — they cannot be told apart from the page.
That is why settlement does not trust goalscorers blindly: **it only accepts the
data if the per-player goals add up exactly to each team's score**. When they do
not match (3 of 53 matches in the current season), the goalscorer market is
marked *void* instead of being settled wrongly.

**A detail that matters.** The pages for upcoming matches already exist. Some
have every value at zero; others show perfectly plausible figures (shots,
possession, corners) for matches that have not been played — I could not work
out what they represent. So the guarantee does not come from what we read on the
page, but from the database: **the provider only writes to matches that already
have a score confirmed by another source**. LPF never creates matches and never
decides what has been played; its role is strictly enrichment.

### Flashscore — live score

The competition page embeds the site's internal feed directly in the HTML, in a
delimited format, with no token and no JavaScript:

```
~AA÷0Sgejp7D¬AD÷1788112800¬CX÷FCSB¬AF÷UTA Arad¬AG÷1¬AH÷3¬AB÷3¬ER÷Runda 7
```

It yields four things the other sources do not provide in time:

* **the live score**, during the match;
* **the final score the second the whistle blows** — football-data publishes
  twice a week, Transfermarkt with a delay;
* **the half-time score**;
* **the exact kick-off time, in UTC**.

**Being honest about the states:** from the feed I could only confirm the codes
`1` (scheduled) and `3` (finished) directly — at the time of writing no match
was in progress, so the code for "live" could not be verified. The provider
**does not rely on any particular value**: it treats as live any match that is
neither scheduled nor finished but already has a score. An unknown code breaks
nothing. Likewise, the current minute is looked up in several possible fields,
and if it appears in none of them, the Live page estimates it from the time
elapsed since kick-off.

#### Pre-match odds

The most serious gap the app had: football-data publishes odds **together with
the result**, so for upcoming matches we had 2 out of 186. Without odds before
the match there is neither real value detection nor a comparison with the
market at the right moment.

Flashscore's match page does not contain the odds, but it requests a GraphQL
endpoint with a persisted query itself, which I found by watching which requests
the page makes in the browser:

```
https://global.ds.lsapp.eu/odds/pq_graphql
  ?_hash=ope2&eventId=<id>&bookmakerId=<n>&betType=HOME_DRAW_AWAY&betScope=FULL_TIME
```

It answers `200` **with no special header** — no token, no Referer — and returns
both the current odds and the **opening odds**.

**Checked before implementing**, on 31 August 2026:

| What | Result |
|---|---|
| Upcoming matches with odds | **9 of 10** (the one missed was 12 days away) |
| Matches already played | **8 of 8** — so the history can be filled in too |
| Bookmakers | 5: Betano, Superbet, Getsbet, Unibet, MrBit |
| Margin per bookmaker | **0.9 % – 4.9 %** (football-data had 8.4 %) |
| Markets | 1X2, over/under (7 lines), both teams to score, double chance, Asian handicap, correct score |

**Two things were verified, not guessed**, and both were wrong on the first
attempt: the bookmaker menu answers `400` without `geoIpSubdivisionCode`, and the
double-chance fields are called `homeOrDraw` / `awayOrDraw` / `noDraw`, not what
I had assumed. A test explicitly checks that the old names match nothing.

**The existing odds are not touched.** football-data already uses the names
`avg` and `max`, and the backtest relies on them; our aggregates are called
`fs_avg` and `fs_max`. Each bookmaker keeps its own name.

**The fragility, stated plainly:** `_hash` identifies a persisted query and **may
change when Flashscore updates its site**. When that happens, the provider raises
an explicit error and stops the step, instead of silently writing zero rows and
making it look as if the matches have no odds.

The step only runs on the **full** update, not the quick one: one match costs
`bookmakers × markets` requests, so a run takes minutes. The default budget is
120 requests, and the nearest matches are served first — the same pattern as
for API-Football. It is switched off in `.env` with `FLASHSCORE_ODDS=false`.

**Terms of use:** the odds are shown publicly on the match page, but the
endpoint is undocumented and Flashscore's terms do not explicitly allow
automated access. I flagged this before building it; the decision to use the
source is yours.

#### Displayed time vs. stored time

These are two different things, and both were wrong in turn.

**Storage** is in UTC, so the database does not depend on the machine it runs
on. That is correct and stays that way.

**Display**, however, has to be Romanian time — otherwise the 20:30 match shows
up as 17:30. On the *Next round* page the time was even written **with no marker
at all**, so you could not even tell it was anything other than local time. The
conversion now happens at display time, through `local_time()` and
`local_series()` in [`common.py`](src/predictiescor/app/common.py), and where
there is room the time zone is shown too (`EEST` in summer, `EET` in winter).
Eleven tests cover the conversion, including late matches that fall on the next
day and the switch to winter time.

#### A time-zone bug, found by comparison

Flashscore publishes the kick-off moment as a UTC timestamp. Comparing it with
what we had in the database showed a systematic two-hour difference:
**Transfermarkt shows German time, LPF Romanian time**, and both were being read
as if they were already UTC. Every match was stored two to three hours later
than it is actually played — which also broke the window for automatically
checking the betting slips.

Both parsers were fixed (`ZoneInfo("Europe/Berlin")` and
`ZoneInfo("Europe/Bucharest")` respectively, with daylight saving time handled
correctly), and the rows written before were repaired from the feed by
`correct_kickoff_times()`: **168 matches corrected**.

#### In-play probabilities

At 1-0 in the 70th minute, the odds are no longer the ones from before kick-off.
The pre-match model gives each team's scoring rate (λ per 90 minutes); the
situation on the pitch follows directly from it:

```
remaining goals ~ Poisson(λ × minutes_remaining / 90)
final score = current score + remaining goals
```

The other markets are derived from the same matrix, with the functions that
already exist — so a live "over 2.5" is computed by exactly the same code as a
pre-match one.

**What is not applied:** the Dixon-Coles correction for low scores. It describes
the dependence between the goals of a *whole* match; over the remainder of a
match it does not mean the same thing, and using it would be an unjustified
extension. **What is not modelled at all:** red cards, tactical changes after a
goal, fatigue. It is a model of tempo, not of play.

On the **Live** page, when no match is in progress, you can simulate any
situation — pick the teams, the score and the minute — and see how the odds
move.

**Terms of use:** the feed is public and unprotected, but Flashscore's terms do
not explicitly allow automated access. Requests are made rarely and with a pause
between them, and the source can be switched off completely in `.env`
(`FLASHSCORE_ENABLED=false`). The decision to use it is yours.

The Sofascore and FBref providers are fully written and tested for structure;
if the sources become accessible, they are switched on from `.env` with no other
change. The app **does not try to get around anti-bot protections** — it detects
the block, stops cleanly and uses the remaining sources.

### Limits of the free API-Football plan

The limits below are all respected automatically by the code:

* **100 requests per day.** Statistics are requested *per match*, so the
  database fills up over a few days of successive runs. The most recent matches
  take priority.
* **10 requests per minute.** The provider leaves 6.5 seconds between requests;
  otherwise you get `429` and waste requests from the daily quota.
* **Seasons 2022–2024 only.** The current season is not included — which is why
  current results come from football-data.co.uk, per-match statistics from
  LPF.ro, and today's players from Transfermarkt.

---

## How the data stays up to date

### From the interface (the button)

**⬇️ Actualizează datele** (*Update the data*), in the sidebar, on any page.
It fetches **everything the model needs**, in eleven steps: results, live
score, fixtures, official LPF statistics, news, pre-match odds, squads and
absences, detailed per-match statistics — then it cleans up, records the
predictions and settles the betting slips.

**The expensive steps are skipped if they are fresh.** Otherwise the button would
have meant a quarter of an hour of waiting on every click, for nothing: squads do
not change twice a day, and odds do not move in five minutes enough to justify
another six minutes of requests.

| Step | Stays fresh for |
|---|---|
| Pre-match odds (Flashscore) | 3 hours |
| Detailed per-match statistics | 6 hours |
| Squads and players | 12 hours |

The rest always runs — it takes seconds and brings exactly what changes often.

Measured on 6 September 2026: **first run 278 seconds**, the second one right
after, **96 seconds**, with the three expensive steps skipped.

From the terminal, `python scripts/update.py --force-all` ignores the freshness
window and redoes everything.

### From the terminal

`scripts/update.py` runs exactly the same code as the buttons (both call
`predictiescor.data.refresh`), so there are no two different behaviours.

```bash
python scripts/update.py
```

### Nothing runs in the background

There are no scheduled tasks and no auto-start. There used to be two — a daily
full update and a betting-slip check every 15 minutes — removed on
29 September 2026, on request, together with the `--install` / `--uninstall`
options that created them. Data is fetched when you press **⬇️ Actualizează
datele** or run one of the commands below.

The consequence, stated plainly: after a break, the first update has more to
fetch. The steps resume where they left off, so nothing is lost — it just takes
longer once.

| Command | Duration | When |
|---|---|---|
| `python scripts/update.py --quick` | ~10 s | often (results, fixtures, LPF statistics) |
| `python scripts/update.py` | ~2–10 min | once a day |
| `python scripts/update.py --with-tickets` | same | in the morning, before the matches |
| `python scripts/update.py --budget 0 --no-players` | ~5 s | like `--quick`, but also runs the clean-up and the settlement of betting slips |

**How "live" it is:** results appear a few minutes to hours after the match
(both Transfermarkt and football-data.co.uk update after the final whistle).
There is no minute-by-minute score — that would need a paid API plan. For
predictions and betting slips, updating after each round is enough.

---

## Betting slips

### Generation

```bash
python scripts/tickets.py --next
```

It builds 5–10 betting slips for the next day with matches, in the format used
by betting sites: several picks, each with its own odds, total odds and
potential winnings.

Eight strategies, with different risk profiles:

| Strategy | Profile |
|---|---|
| **Safe slip** | high probability, low odds |
| **Balanced slip** | a compromise between probability and odds |
| **Value slip** | only where the model beats the bookmaker's odds |
| **Goals slip** | over/under, both teams to score |
| **Statistics slip** | corners and cards |
| **Goalscorer slip** | players who score |
| **High-odds slip** | unlikely, but pays a lot |
| **Results slip** | 1X2 and double chance only |
| **Same-match combo slip** | several picks from the same match, with the probability computed by simulation |

Two rules keep the slips honest:

1. **One pick per match** in every strategy except the "combo slip", where picks
   from the same match are allowed because the joint probability is computed by
   simulation, not by multiplication.
2. **The odds shown are "fair"** (1 ÷ probability). Bookmakers take a margin, so
   the real odds will be lower. A slip is only worth it if the odds offered by
   the bookmaker are higher than the ones computed here.

### Checking after the match

It happens when you press **Verifică biletele acum** (*Check the slips now*) on
the Betting slips page, on **⬇️ Actualizează datele**, or from the terminal:

```bash
python scripts/watch.py --once
```

On every cycle it also reads **the news feeds, at most once an hour** —
independently of the matches. Without this, an injury announced at noon, on the
day of an evening match, did not reach the database unless you pressed the
button: the cycle only got to the feeds when a result was missing, and not
always even then, because Flashscore usually brings it through the fast path.

It makes no network request unless **a match has actually finished** in the
meantime: it knows the schedule, so it knows when checking makes sense. The
Betting slips page shows the "next useful check" — the moment after which
pressing the button makes sense.

The window is **1 hour and 50 minutes** from kick-off — 45+15+45 minutes plus
stoppage time means a match cannot finish any earlier. It used to be 2h15 back
when the result came from football-data, which publishes twice a week;
Flashscore has the final score immediately, so we can check earlier.

When a result really is missing, **the cheap path is tried first**: a single
request to Flashscore, which takes about a second. The full refresh — which
downloads an entire CSV and queries two more sites — only runs if something is
still missing after that. If Flashscore is down, the cycle continues along the
long path instead of stopping.

| Command | What it does |
|---|---|
| `python scripts/watch.py --status` | what it is waiting for and when the next useful check is |
| `python scripts/watch.py --once` | a single check, now |
| `python scripts/watch.py` | runs continuously, in the terminal, until Ctrl+C |

A match only enters the "to check" list if it kicked off more than 1h50 ago
**and** either has no result yet or still has unsettled picks. A slip left open
because of *another* match does not trigger pointless checks on the one already
settled, and matches older than a week with no result are dropped instead of
being queried forever.

### Manual checking and learning

```bash
python scripts/tickets.py --settle --learn
```

Each pick is compared with what actually happened **as soon as its match has
been played** — the rest of the slip is not waited for. The consequences:

* A slip with a missed pick is closed on the spot: the outcome is already decided.
* A slip whose decided picks have all come in stays open, and the *picks won*
  column shows where it stands (`1/2`, `2/3`).

When data is missing (for example the corners of a match without statistics),
the pick is marked **void**, not guessed — void slips do not contaminate the
accuracy statistics.

Two things are learned from the results:

* **Per-market calibration.** If the model says 70% on corners but it happens
  55% of the time, it is overconfident there; the correction is applied
  automatically to the next slips.
* **Strategy weights.** Profitable strategies are suggested more often. A weight
  only changes after at least 8 slips, with shrinkage — so that a lucky streak
  does not overturn the conclusion.

### Does each strategy know its own odds?

A hit rate on its own is misleading. The "High-odds slip" comes in **0 % of
16** and looks broken — but at a predicted probability of 14 % per slip, the
chance that none of them comes in is **15 %**: ordinary bad luck, not a defect.

That is why the table has a column for *how unusual* the result would be if the
model were right (two-tailed binomial test). Measured on 6 September 2026,
**no strategy deviates unusually** — all of them are above 15 %.

The difference from the markets: at the **pick** level, goalscorers really do
deviate significantly (46.9 % predicted vs 14.3 % actual, 3 standard
deviations). At the **slip** level it does not show, because 9 slips are too few
and combining picks dilutes the signal. Markets are the sharper measurement.

### Warning when the market has moved

If a pick sits on a match whose odds have changed a lot since opening, the slip
gets a marker. It does not change the prediction — it only says that **the model
is working with older information than the bookmakers**. It is the only signal
in the app that comes from something the model does not have.

### How to read the check

Each slip is a card, not a row in a table you have to pick it out of. You can
see at a glance: the coloured status label, the odds, the probability, the
profit, a bar showing *how many picks have come in* and, below it, **every pick
with its own mark**, the match, the score and what actually happened
("3 goals", "0 cards", "we have no player data for this match").

Slips are grouped by day, with the day's tally in the heading. The classic table
is still there, but collapsed into a panel — it is no longer the only way to see
anything.

All the picks are fetched **in a single query** (`legs_for_tickets`), not one per
slip.

### An honest history

```bash
python scripts/tickets.py --backfill 30
```

It rebuilds the slips for the last 30 days with matches, **retraining the model
for each day only on the matches before it**. Only then is the reported accuracy
real. Without this, the model would "bet" on matches it had already seen in
training, and the figures would be flattering and useless.

```bash
python scripts/tickets.py --report
```

---

## The models

### Goals — Dixon-Coles

```
λ_home = exp(attack_home + defence_away + home_advantage)
λ_away = exp(attack_away + defence_home)
```

Goals are approximately Poisson, but low scores (0-0, 1-0, 0-1, 1-1) occur more
often than an independent Poisson predicts — the `rho` parameter corrects
exactly those four cells.

Two details that matter:

* **Time weighting.** A match from 240 days ago weighs half as much as one from
  yesterday (`half_life_days` in `config.yaml`).
* **Regularisation as a Bayesian prior.** Each team gets the equivalent of
  `shrinkage_matches` fictitious matches of an average team. Without this, a
  newly promoted side with 6 good matches ends up artificially at the top of the
  strength ranking.

**All** markets follow from the score matrix: 1X2, double chance, exact score,
over/under on any line, both teams to score, Asian handicap, goal difference,
odd/even.

### xG rebuilt from shot zones

API-Football publishes xG, but the free plan only covers 2022–2024. LPF publishes
shot zones for the current season, but not xG. The gap is closed: we learn from
the seasons where we have both how much a shot from each zone is worth, then
apply the formula where xG is missing.

The model is deliberately constrained, not a free regression:

* **no intercept** — zero shots means zero xG;
* **coefficients forced to be positive** — a shot cannot lower xG.

Without the constraints, the regression gives shots from outside the box a
*negative* coefficient (collinearity with total shots) — physically absurd and
not generalisable. With them, R² **rises** from 0.562 to 0.587, and the
coefficients read directly as the average value of a shot:

| Zone | Goals per shot |
|---|---|
| Shot from inside the box | 0.129 |
| Shot on target (bonus) | 0.061 |
| Blocked shot | 0.020 |
| Shot from outside the box | 0.001 |

The real xG is never overwritten: it stays in the `xg` column, the estimate in
`xg_estimated`. You always know which value comes from the provider.

### Correlated markets — Monte Carlo simulation

Two picks from the same match are correlated: "home win" and "over 2.5 goals"
rise together. Multiplying the probabilities would lie about the slip's chance.

We simulate the match 20,000 times and count in how many runs all the picks come
in. The difference is real:

| Combination | Multiplied | Actual (simulated) |
|---|---|---|
| FCSB win + over 2.5 | 28.5% | **33.9%** (1.19×) |
| FCSB win + under 2.5 | 29.1% | **24.0%** (0.83×) |

Each simulation is turned into the same structure used for real settlement and
passed through the same code. A pick is priced exactly the way it will be
settled after the match — no differences can appear between the two.

### Secondary statistics — 22 indicators, multiplicative model + Negative Binomial

```
expected = league_average × team_attack × opponent_permissiveness × venue_factor
```

Corners and cards are over-dispersed relative to Poisson, so the distribution is
Negative Binomial, with the dispersion estimated from the residuals.

### Players

Per-90-minute rates with shrinkage towards the position average, multiplied by
the expected minutes, then **scaled so that the team total equals the goals
predicted by Dixon-Coles**. That way the two models stay consistent with each
other.

### Elo — an independent benchmark and part of the blend

A classic rating, with the update amplified by the goal difference.

Measured by backtest, the Elo + Dixon-Coles blend **helps**: RPS drops from
0.2099 to 0.2086, and accuracy rises from 45.1% to 47.6%. The default weight is
0.4, but the curve is flat between 0.3 and 0.6 — with ~600 matches tested, the
differences in that range are noise.

The blend is logarithmic (weighted geometric mean), and the score matrix is
**reweighted** so that its 1X2 marginals are exactly the blended ones. Otherwise
the exact scores and the goal markets would contradict the new 1X2.

### 1X2 calibration — measured, not applied

A model can rank teams correctly and still express its confidence badly. I
tested whether a calibration layer would help, training on the first 561
backtest matches and testing on the next 374:

| Metric | uncalibrated | isotonic | Platt |
|---|---|---|---|
| RPS | **0.2088** | 0.2121 | 0.2090 |
| log loss | 1.0345 | 1.0933 | **1.0335** |
| Brier | 0.6202 | 0.6278 | **0.6200** |

Isotonic regression makes **everything worse** — it is too flexible and learns
the noise. Platt changes nothing (gains of the order of 0.0002).

The reason: the model is **already well calibrated** on 1X2. The weighted mean
deviation between predicted and observed is 0.027 — under three percentage
points. There is no systematic distortion to correct.

That is why the calibrator is not wired into the prediction. The module stays as
a diagnostic, visible on the *Model performance* page: if the model changes a
lot, it tells you within seconds whether the situation has changed.

Mind the distinction: the **secondary** markets (corners, goalscorers) really are
poorly calibrated — corners 59% predicted vs 52% actual, goalscorers 46% vs
38% — but those are already corrected automatically, from the settled slips.

### Per-market calibration, after the clean backfill

Measured on 336 settled picks, 6 September 2026:

| Market | Picks | Predicted | Actual | Difference |
|---|---|---|---|---|
| total goals | 106 | 80.7 % | 80.2 % | **−0.6** |
| cards | 76 | 77.3 % | 76.3 % | −1.0 |
| double chance | 67 | 72.0 % | 67.2 % | −4.8 |
| corners | 34 | 62.9 % | 55.9 % | −7.0 |
| team to score | 22 | 81.3 % | 77.3 % | −4.1 |
| **goalscorers** | 21 | 46.9 % | **14.3 %** | **−32.6** |
| 1X2 | 7 | 45.1 % | 57.1 % | +12.1 |

Overall: 73.2 % predicted, 69.0 % actual. The goals and cards markets are **very
well calibrated** — under one point of difference over more than 180 picks.
Goalscorers are the pathological case; the other differences are within noise at
these sample sizes.

### What did NOT help

Home advantage estimated **per team** (instead of one shared across the league)
changed practically nothing: RPS 0.2101 → 0.2099, within noise. The code stays,
with strong shrinkage, but do not expect a gain from it. You can switch it off
by setting `home_advantage_spread` very high in `config.yaml`.

**Parameter tuning.** The values in `config.yaml` (half-life 240, shrinkage 20,
Elo weight 0.4) had never been measured as a grid. I tuned three parameters with
a **holdout**: choose on the first 70 % of the tested matches, report on the last
30 %, which I did not look at while choosing.

| Step | Chosen on the tuning set | Tuning RPS | Holdout RPS |
|---|---|---|---|
| half-life | 400 (from 120–400) | 0.2101 ↓ | 0.2107 |
| shrinkage | 10 (from 10–35) | 0.2097 ↓ | 0.2109 |
| Elo weight | 0.4 | 0.2097 | 0.2109 |
| **current: 240 / 20 / 0.4** | — | 0.2104 | **0.2105** |

At every step the tuning metric improved, and at every step the holdout **did
not follow**. The "optimised" configuration comes out **0.0004 worse** on the
holdout than the current one. Textbook overfitting — and exactly why the holdout
is not optional.

Across the whole grid, the holdout RPS sits between 0.2104 and 0.2114. The model
is practically **insensitive** to these parameters within reasonable ranges. The
current values stay unchanged.

One useful thing did come out of it: with `elo_ensemble_weight = 0`, the holdout
rises to **0.2114**, the worst in the whole grid. The Elo blend really does help,
by about 0.0005 RPS — independent confirmation of the choice in the
configuration.

**The referee's effect on cards** — plausible, but not provable with the current
data. Measured on 155 matches with a known referee and cards for both teams,
27 referees, **median 5 matches per referee**:

| Model | MAE | RMSE |
|---|---|---|
| league average only (baseline) | 1.9419 | 2.3791 |
| referee, no shrinkage | 1.9646 | 2.4387 |
| referee, shrinkage k=10 | **1.8972** | **2.3442** |

The 2.3 % gain looks real until it is checked: 90 % confidence interval
**[−0.008, +0.097]** — it contains zero; permutation test **p = 0.059**. And `k=10`
was chosen by looking at the same results we were judging, so even that 0.059 is
optimistic.

The variance decomposition says the same thing more directly: the variance
**between** referees (1.258) is *smaller* than what pure chance would produce
(1.475) — estimated signal **zero**. With five matches per referee, the
per-referee averages are almost pure noise.

The code stays in [`referees.py`](src/predictiescor/modeling/referees.py), not
wired into the predictions. **The measurement is worth redoing** when there are
~500 matches and 15+ per referee; the test would then have real power.

---

## Evaluation

The *Model performance* page runs a **walk-forward backtest**: for each tested
match, the model is retrained using only the matches played before it. No
information from the future reaches the model.

Reported metrics, compared with **market odds with the margin removed**:

* **RPS** (Ranked Probability Score) — the standard for football; it accounts
  for "draw" lying between "home win" and "away win". Lower = better.
* **Log loss**, **Brier**, **accuracy**, **calibration by bins**.
* Simulated **ROI** on the bets where the model beats the market by a chosen
  threshold.

The market is a very hard opponent to beat. If the model gets close to it, it is
already good; if it beats it consistently over many hundreds of matches, then it
is worth talking about.

### When the model disagrees with the market, who is right?

The fundamental question behind the betting slips. The slip history cannot
answer it — 119 slips and 266 picks are dominated by luck. But the same question
can be asked on **1004 matches with closing odds**, i.e. four times as much
information. Measured on 31 August 2026:

**1. Where the model deviates, how much the deviation costs it** (RPS, lower = better):

| Disagreement with the market | Matches | Model RPS | Market RPS | Model edge |
|---|---|---|---|---|
| 0–3 % | 94 | 0.2304 | 0.2312 | **+0.0008** |
| 3–6 % | 368 | 0.1995 | 0.1984 | −0.0011 |
| 6–10 % | 327 | 0.2093 | 0.2023 | −0.0070 |
| over 10 % | 215 | 0.2214 | 0.2004 | **−0.0210** |

The trend is monotonic and clear: **the more the model deviates from the market,
the worse it is**. Where they agree, they are level; where the model has "opinions
of its own", it loses ten times as much ground.

**2. The duel of favourites** — the 167 matches where they pick different favourites:

| | Model | Market |
|---|---|---|
| Got it right | 39 (**23.4 %**) | 70 (**41.9 %**) |

The market is right almost twice as often. The test is immune to calibration: it
does not matter how confident each one is, only whom it bets on.

**3. Simulated ROI, with a 90 % confidence interval:**

| Price used | 2 % threshold | 5 % threshold | 8 % threshold |
|---|---|---|---|
| average odds (8.4 % margin) | −13.1 % *(significant)* | −15.6 % *(significant)* | −16.2 % *(significant)* |
| best price | −6.2 % *(not distinguishable from zero)* | −9.0 % *(not distinguishable)* | −9.4 % *(not distinguishable)* |
| Pinnacle benchmark, best price | −7.7 % *(not distinguishable)* | −16.1 % *(significant)* | −21.4 % *(significant)* |

**The conclusion, stated directly:** the "value" logic has no edge. What is more,
a real edge should **grow** with the threshold, because we demand an ever larger
difference from the market. Here it shrinks. Combined with table 1 and the duel
of favourites, the verdict is that **the model's disagreements are its error, not
extra information**, and the value picks bet precisely on its errors.

A nuance, for fairness: taking **the best available price** instead of the
average odds, the loss at a low threshold can no longer be distinguished from
zero — you pay the margin and that is about it. Only when you demand a large edge
do you demonstrably lose.

**What this means in practice:** the betting slips remain an analysis tool. If
you want to use them as a filter, the filter that survives measurement is "where
the model agrees with the market", not "where it deviates from it".

#### Re-measured with better odds — the verdict does not change

Once pre-match odds from Flashscore became available, the natural question was
whether the result above was somehow down to football-data's poor odds
(10.2 % margin). I filled in the Flashscore odds for the **54 matches played in
the current season** — everything their feed covers — and re-measured on
31 August 2026.

**First, validating the source**, on the 47 matches that have both sources:

| | football-data | Flashscore |
|---|---|---|
| Average margin | 10.21 % | **3.14 %** |
| RPS | 0.1867 | 0.1862 |

Mean absolute difference between the implied probabilities, after removing the
margin: **0.0052** — half a percentage point. Correlation on the home-win
probability: **0.9989**. The two sources say practically the same thing;
Flashscore just says it with a margin three times smaller.

**Then the re-measurement.** On the 54 matches, the result looks **reversed**:
the model wins on the large disagreements (+0.0410) and beats the market in the
duel of favourites (30.0 % vs 20.0 %). Tempting — and wrong.

The control test shows why. The same matches, priced with the **old odds**:

| Set | Matches | Duel of favourites | Edge on large disagreement |
|---|---|---|---|
| Flashscore | 54 | model 30.0 % vs market 20.0 % *(n=10)* | **+0.0410** |
| football-data, **the same ones** | 47 | model 37.5 % vs market 25.0 % *(n=8)* | **+0.0466** |
| football-data, all | **1004** | model 23.4 % vs market **41.9 %** *(n=167)* | **−0.0210** |

The old odds, on the same small sample, give the same favourable picture — even
slightly more favourable. **So it was not the odds that changed the result, but
the sample.** The current season happens to be a period in which the model looks
good on eight to ten disagreement matches, i.e. on noise.

The ROI says it just as clearly: +21.6 % at the 8 % threshold, with an interval
of **[−26.4 %, +72.6 %]** over 21 bets. A hundred-point interval is not a
measurement, it is an opinion.

**The conclusion from the 1004 matches stands.** When a sample of 54 contradicts
one of 1004, the 1004 wins. The better odds are a real gain for pricing and for
display — but they changed nothing about value.

#### About the confidence interval

Without it, I was reading noise as an edge: a +8 % ROI over 60 bets means
nothing if the interval is [−25 %, +40 %]. The interval is computed by
resampling **matches**, not bets: picks on the same match are not independent (if
the home side wins, the "draw" pick automatically loses), and treating them as
independent would give a falsely narrow interval. There is a test that checks
exactly this.

**Be careful with the threshold table:** several thresholds are tried at once,
so the best of them always looks better than it deserves. Use it to see the
*shape*, not the peak.

---

## Project structure

```
SuperLiga-Score-Predictor/
├── config.yaml                    league IDs, seasons, model parameters
├── .env                           keys and switches (excluded from git)
├── data/predictiescor.db          the SQLite database
├── scripts/
│   ├── fetch_data.py              initial / per-source ingestion
│   ├── update.py                  periodic update
│   ├── watch.py                   automatic check after each match
│   └── tickets.py                 betting slips: generation, settlement, learning
└── src/predictiescor/
    ├── config.py                  reading the configuration
    ├── utils/
    │   ├── teamnames.py           team name normalisation
    │   └── http.py                HTTP client with caching and rate limiting
    ├── storage/                   SQLite schema and data access
    ├── data/                      the seven providers + the aggregator
    │   ├── flashscore.py          live score, statistics, kick-off times
    │   ├── flashscoreodds.py      pre-match odds from 5 bookmakers
    │   ├── news.py                RSS feeds from the sports press
    │   ├── newsfacts.py           free text → facts, with a confidence level
    │   ├── transfers.py           arrivals, departures and their fees
    │   ├── lpf.py                 the league's official source
    │   └── refresh.py             the update steps (button + terminal)
    ├── modeling/
    │   ├── dixon_coles.py         the goals model
    │   ├── markets.py             deriving every market
    │   ├── inplay.py              in-play probabilities
    │   ├── livetips.py            picks from the situation on the pitch
    │   ├── secondary.py           corners, cards, shots
    │   ├── xg.py                  xG rebuilt from shot zones
    │   ├── simulation.py          correlated markets, Monte Carlo
    │   ├── players.py             player predictions
    │   ├── elo.py                 Elo rating
    │   ├── calibration.py         measured calibration (not applied)
    │   ├── referees.py            measured referee effect (not applied)
    │   ├── evaluate.py            backtest and metrics
    │   ├── disagreement.py        model vs market, with a confidence interval
    │   └── pipeline.py            ties everything together
    ├── tickets/                   generation, settlement, learning, upsets
    └── app/                       the Streamlit dashboard
```

---

## Team name normalisation

Every source writes them differently: `FCSB`, `Steaua Bucuresti`, `FC FCSB`,
`(3.) FCSB`. `utils/teamnames.py` maps all of them to one canonical name;
otherwise the same team would appear three times in the database.

Two traps handled explicitly:

* **`Universitatea Craiova` vs `FC U Craiova 1948`** are *different* clubs. A
  hard rule, applied before any fuzzy matching, prevents them from being merged.
* **Years in the names** (`FC Rapid 1923`, `CFR 1907 Cluj`) are decoration and
  are ignored when comparing — except `1948`, which is caught by the rule above.

If a source introduces a new variant, the *Data and sources* page flags it and
offers a merge button. You can also fix it from code:

```bash
python -c "import sys; sys.path.insert(0,'src'); from predictiescor.storage.db import Database; print(Database().deduplicate())"
```

---

## Configuration

Everything worth tuning is in `config.yaml`, with an explanation next to each
parameter: the half-life, the shrinkage, the betting lines, the request budget,
the seasons to download.

The model parameters can be tested directly from the interface, on the *Model
performance* page: change the half-life or the shrinkage, run the backtest and
see the effect on the RPS immediately.

---

## What the press says

Four RSS feeds from the Romanian sports press, read for one thing only:
**absences, returns and transfers**. RSS, not scraping listing pages — the feed is
published precisely so that it can be read automatically.

| Source | Articles in the feed | Full text | Category |
|---|---|---|---|
| **sport.ro** | 1106 | no (~250 chars) | **"Superliga" = 412** |
| **fanatik.ro** | 72 | **yes (~2900 chars)** | mixed |
| digisport.ro | 100 | no (~86 chars) | missing |
| prosport.ro | 20 | no | "Fotbal Intern" |

sport.ro provides the coverage, fanatik the depth. The article page is only
downloaded for the news items that pass the filter — usually ten to fifteen a
day. **Nothing is downloaded from sport.ro**: their pages put teasers for other
articles right in the body of the story, and those produced facts attributed to
the wrong article. The "Superliga" category, however, is exact, and the summary
usually captures the fact in the headline.

### Three confidence levels

| | Marker | Example (Romanian phrasing) |
|---|---|---|
| ✅ **confirmed** | an accomplished fact, stated as such | "a semnat" (*has signed*), "s-a accidentat" (*got injured*), "nu va juca" (*will not play*) |
| 🟡 **likely** | about to happen | "e aproape de" (*is close to*), "99%", "urmează să" (*is set to*) |
| ⚪ **rumour** | talk, not fact | "potrivit surselor" (*according to sources*), "ar putea" (*could*), "negociază" (*is negotiating*) |

The **weakest** marker in the sentence sets the tone: *"According to sources, he
has officially signed"* remains a rumour, because the anchor is "according to
sources". When there is no marker at all, the answer is still rumour — a story
that says nothing firm is not a confirmation.

### How well it works, counted on real articles

Measured on 31 August 2026, on 21 articles → 26 facts (13 confirmed, 1 likely,
12 rumours). **I read all 13 confirmed facts and graded them one by one: 12
clearly correct, 1 borderline, 0 wrong.**

The first run, however, showed 12 correct, **4 clearly wrong** and 3 borderline.
The four errors were fixed, and each one remained as a test:

| The error | The cause | The fix |
|---|---|---|
| "Andrei Rațiu" → the player *Doru Andrei* | first name read as a surname | common first names no longer match on their own |
| "UTA Arad" → the player *Ofri Arad* | team name read as a player | team names are removed before searching |
| Djokovic from tennis → the defender *Damjan Djokovic* | relevance filter too permissive | the source category decides **in both directions** |
| "left the **pitch** at half-time" → transfer | right verb, wrong object | a list of things that are not the club |

The last one was the most frequent — three of the four errors. "Left the
training session" and "left the pitch" both came out as departures from the
club.

### What it cannot do

* It does not understand irony, quotes from someone else ("X denied that he is
  leaving") or questions in headlines — although a headline ending in "?" can no
  longer be "confirmed", only "likely" at most.
* In a sentence with two players and a single verb, it attributes the fact to the
  one nearest the verb. That is right most of the time, but *"who comes to FCSB
  after Tănase's departure"* can come out as an arrival under Tănase's name. Such
  cases almost always fall into "rumour".
* A surname shared by two players in the league is not linked to either — the
  fact is recorded with the name as written in the text, marked **unlinked**.
* **"Confirmed" means the article states it as certain**, not that we have
  verified it. That is why the supporting sentence is always shown: you read it
  and decide for yourself.

### Corroboration

When two different publications say the same thing, the fact gets 🔁 *2 sources*.
It is the only independent check we have without reading the articles ourselves.
In the measurement above, Dennis Politic's injury appeared in two sources, and
Florin Tănase's departure in three.

### None of this goes into the model

Deliberately, and for the same reason as with absences: the press only publishes
the present. We cannot know what was written before the matches already played,
so an adjustment built on them **could not be validated**. The data is collected
with dates; once there are a few months of history, the question becomes
measurable.

One observation from the first measurement: none of the 4 absences found in the
press appeared in Transfermarkt's list of 25 unavailable players. That is to be
expected — **the press is faster** — but it also means the cross-check has
nothing to lean on yet.

---

## When the sources disagree

Transfermarkt updates its squads with a delay. On 6 September 2026, **Florin
Tănase was still listed in the FCSB squad** — even though three publications had
written, a week earlier, that he had signed for Omonia Nicosia. The same with
Daniel Bîrligea.

It is not a reading error: the club page really does list them. The data
faithfully reflects the source; the source is behind.

This is where the news feeds earn their place. `reported_departures()` compares
the two sources and surfaces the conflicts — players the press reports as gone,
but still in the squad:

| Player | Still listed at | Sources |
|---|---|---|
| Florin Tănase | FCSB | 3 |
| Anzor Mekvabishvili | Universitatea Craiova | 2 |
| Daniel Bîrligea | FCSB | 1 |

**We exclude them from the predictions** when the article states the departure
as a fact (`confirmed`), not as a rumour.

### Checked one by one

The first version flagged 8 players. Reading them one at a time, **4 were
wrong** — three distinct causes, each fixed and covered by a test:

| The error | The cause | The rule added |
|---|---|---|
| "El Sawy left **Rapid**" — he is at UTA Arad | departure from a previous club | the club in the sentence must be his current club |
| "Țicu is leaving **Dinamo**" — he is at Farul | same | same |
| Crețu parted ways with FCSB… then came back | departure later cancelled | a more recent confirmed return cancels the departure |
| "Bîrligea was released by CFR **to** FCSB" | it is an *arrival*, with the same verb | "from X" = away from X; "to Y" = towards Y |

4 remained, all correct. Checked the other way round too: Crețu is back in the
predictions for FCSB, Țicu for Farul.

### When even the sources do not know

Bîrligea really had left — for Frosinone — but **no source said so**:
Transfermarkt listed him, and the two news items about him described his
*arrival* from CFR. For cases like this there is a manual marker, on the
*Players → Absent* page: you mark him as gone, he is excluded like any other, and
the marker can be undone. It deletes nothing.

The conflicts are shown on the **Players → Absent** page, with the sentence from
the article, so you can check them. We do not hide the disagreement, we show it.

### Who can be removed from a squad

The first version removed anyone missing from the Transfermarkt page. Wrong: the
players brought in by LPF have their full legal name ("Manuel Alberto Cunha
Lopes"), while Transfermarkt uses short names — they never match. That is how
**16 players who had played the day before** ended up "without a club", among
them Korenica, Heras Garcia and Zandbergen.

Two guards now:

* **only the players the source knows** are judged (they have a Transfermarkt
  id); the others being absent from the page means nothing;
* nobody who **has played in the last 30 days** is removed — whoever was on the
  pitch last week is at the club.

### Transfers during the season

LPF saw the player in the new club's line-up, did not find him in that club's
squad and created a new row. The result: **two Korenicas**, one at CFR Cluj and
one at FCSB, with his history split in two. Now, before creating a row, the
player is looked up at the other teams — exact name match, so as not to merge
different people — and moved.

### One player, one row

The model grouped appearances by `(player, team)`, so a transfer split his
history in two: Korenica had 418 minutes, but only 45 at FCSB — under the 270
threshold, so absent from FCSB's predictions, while his CFR rates sat unused.

Now the grouping is by **player**, with a separation that matters:

* the **per-90-minute rates** (goals, assists, cards) are computed from *all*
  appearances — they are a property of the player, not of the club;
* the **expected minutes** are computed only from matches at the **current**
  club, relative to how many matches the club has played since he arrived — his
  role there is a different story from the one at his previous club.

The team comes from the current squad (`current_squads()`), not from the majority
of appearances. Korenica now appears at FCSB with 45 expected minutes — exactly
what he has played there — but with the scoring rate from all 418.

The second duplicate came from filling in season totals: those sit under the old
club, and the "do I already have him?" check was done *per team*, so it did not
find him. The lookup now also runs without the team.

### And who has actually left the league

Separately: `upsert_player` moved a player when it found him at another club
**in the league**, but those who left for abroad no longer appeared anywhere —
they stayed attached to their former club forever. Now, after each squad read,
whoever is no longer in it is detached (`team_id` becomes NULL). The player is
**not** deleted: his appearances in past matches remain, and rightly so.

---

## Zero does not mean "zero" when the source has not finished writing

LPF publishes the match page **gradually**: right after the final whistle the
cards appear, while the rest of the table stays at zero for a few more hours. The
guard on reading only required *something* to be non-zero — the two cards were
enough — so the page passed and we wrote "0 corners, 0 shots" as real values.

The other end, without which fixing the first would not have helped: the page was
read **only once in its life**. Whatever was written then stayed there forever.

### How much

Five of the 61 matches played this year, checked against Flashscore:

| match | in the database | actually |
|---|---|---|
| FCSB — UTA Arad | 0 corners | 9 |
| CFR Cluj — Farul | 0 | 11 |
| Farul — FC Botoșani | 0 | 11 |
| FC Botoșani — Sepsi | 0 | 8 |
| Oțelul — Rapid | 0 | 9 |

The 2022–2024 seasons, brought in by API-Football, have no such row: it comes
strictly from the LPF source added this year.

### What it broke

The corners and cards model was trained on these zeros, so the league averages
came out too low. And settlement judged "over 8.5 corners" against an invented
zero: the *under 8.5* pick on FCSB — UTA had come out **won** in a match with nine
corners, and *over 7.5* on the same match had come out **lost**.

### The fix

The reference is the statistics that cannot be zero in a match that was actually
played: no team finishes with zero passes and zero possession. When those are
zero, the page is not filled in yet, and nothing is taken from it — not even the
cards, which for Oțelul — Rapid showed 2 when there had been 3.

Re-reading now requires the stored row to be **complete** (`passes > 0`);
otherwise the page goes back on the list on the next run. `upsert_team_stats`
already used `COALESCE`, so a real value overwrites the zero, and a `NULL` does
not erase what is known.

### And it was not enough

The page does not fill in all at once, nor always in the same order. For UTA Arad
— Csikszereda the statistics were all there — 450 passes, 17 shots, 8 corners —
but the **cards** sat at zero and only appeared two days later. The "over 2.5
cards" pick had already been settled against a zero.

Any guard placed on a particular field would miss another field. So the pages of
matches from the last **7 days are re-read regardless**, until LPF finishes
filling them in: about two rounds on each run, cheap, and the only way to catch
both late additions and corrections.

Re-reading the whole season once changed only one more match — the two cards
above — but it also brought in the missing player data, and the goalscorer slip
from 5 September went from "void" to **won**.

For slips already judged on the wrong figures there is `resettle_matches()`: it
reopens the picks on the given matches and judges them again. Normal settlement no
longer looks at a pick once it is decided — and rightly so, otherwise it would
rewrite history on every run — but when the **data** is fixed, the verdict has to
be redone. After the fix, the Brier score on the corners market dropped from
0.274 to **0.258**.

---

## The check after a break (25 September 2026)

The app had not been started for 17 days. The results were still in the database
— the betting-slip checker, which ran every 15 minutes, had brought them in — but
the full update had not succeeded since 9 September. The whole database was then
compared with Flashscore: **0 different scores** across the 79 matches played, no
played match missing. Four problems did come up, all fixed.

### 1. The daily task skipped the days the PC was off

`schtasks /create` leaves two conditions on by default: a missed run **is not
made up** (PC off at 04:30 = a lost day) and the task **does not start on
battery** (on 24 September it was refused exactly like that, `0x800710E0`). The
installer was fixed at the time to change them through PowerShell — but on
29 September the tasks were **removed altogether**, on request, together with the
installer. Nothing in the project runs in the background any more; see "Nothing
runs in the background" above.

### 2. Rescheduled matches kept their old date

The time correction accepted at most 36 hours, so as not to confuse the first and
second legs. Eight matches moved by two days kept their original date — FCSB —
Petrolul on the 12th instead of the 14th, and Rapid — Argeș and Dinamo — Farul had
swapped days with each other. For upcoming matches this matters: a wrong date
moves the match into other slips' day. Where the home–away pair is unique both in
the feed and in the database — the whole regular season — there is no ambiguity,
so moves of up to 60 days are accepted.

### 3. "No card row" means zero, not "I don't know"

Flashscore only lists the card row **if at least one of that colour was shown**.
Verified: in all 60 matches of 2026 with no red-card row, the match sheets show
zero reds. For yellows, the matches with no row are **1.18%**, which is what
Poisson gives for zero yellows at the league average (**1.21%**).

Read as "I don't know", 82% of the rows were left without reds, and the model was
trained only on the matches with sendings-off: **0.287 reds per team per match,
2.7 times too many**. Now 0.105 — i.e. ~0.21 per match, which is what the league
has. The rule only applies when the feed has statistics at all; an empty answer
stays empty.

### 4. The app never noticed updates made from the terminal

`data_version()` was correct and tested, but it reached the cache through a
parameter called `_version`. **Streamlit drops from the cache key any parameter
starting with an underscore** — that is how you pass it objects that cannot be
hashed. So the models and summaries were computed once per process and stayed
that way: the page showed 1355 matches with statistics when the database had
1361, with models trained before the last update. The old test checked the
version function, not that the cache uses it; the new one checks exactly that.

### And Python

Meanwhile Python 3.14 had been installed and became the default `python`, without
the project's dependencies. The app is now started explicitly with `py -3.11`; the
scheduled tasks already used the full path to 3.11.

---

## Data is no longer fixed afterwards — it arrives correct

Until now, every source was corrected *after* it got something wrong: an odd
number was spotted, the cause was tracked down, it was fixed. Three things changed
that.

### 1. An impossible value no longer gets in

The check lives in `upsert_team_stats`, which **every** provider goes through —
not in each provider, where the next one would forget it. A figure that cannot be
true is written as "I don't know", not as zero:

| rule | why |
|---|---|
| `passes = 0`, `possession = 0` | no team finishes a match like that |
| out of range (`yellow_cards > 12`, `possession > 100`) | format or unit error |
| text that is not a number | the source changed its format |
| `corners = 0` **is still allowed** | rare, but possible |

The bounds are deliberately wide: they catch a format error, not an unusual
match. Real records pass — 21 corners in a match, 9 yellows for one team — and
there is a test for exactly that.

The difference between `0` and `NULL` carries further: the model skips what it
does not know, settlement voids the pick instead of losing it, and when the source
fills it in later, the real value goes in over the `NULL` without clashing with an
invented zero.

### 2. Late additions are caught automatically

LPF pages are re-read for 7 days after the match, and the Flashscore statistics
too. There is no more "read once, wrong forever". And a verdict given on data that
has since changed is redone with `resettle_matches()`.

### 3. The app checks itself

The "Data and sources" page now runs the check every time it opens, plus how
complete each column is. Everything found broken so far was found because
somebody looked.

### The archive: from a third to almost everything

The regular feed only covers the current season, so old matches had no Flashscore
id and their statistics could not be requested. `fetch_archive` pages through the
results of a past season, `link_archive` links them to our database by day and
normalised team names — **1282 of 1282, 100%**.

| season | corners before | after |
|---|---|---|
| 2022 | 233 / 321 | **321 / 321** |
| 2023 | 63 / 321 | **321 / 321** |
| 2024 | 65 / 319 | **319 / 319** |
| 2025 | **0** / 321 | **321 / 321** |
| 2026 | 62 / 62 | 62 / 63 (last night's match, fetched on the next update) |

Team rows with corners: **from 848 to 2688**. The corners and cards model used to
see a third of the history; now it sees almost all of it.

### The other statistics, as gap filling

Flashscore returns **15 statistics** in the same response from which we used to
take three. The rest are now written as *gap filling*: only where nobody has
anything. Nobody has measured Flashscore to be better than LPF or API-Football on
them, so they have no business overwriting those values — but where they are
missing altogether, a figure is better than none.

| column | before | after |
|---|---|---|
| shots | 840 | **2690** (100%) |
| shots on target | 824 | **2690** (100%) |
| possession | 650 | **2690** (100%) |
| goalkeeper saves | 614 | **2690** (100%) |
| fouls | 844 | **2688** (99.9%) |
| offsides | 524 | **2680** (99.6%) |
| free kicks | 122 | **2668** (99.2%) |
| blocked shots | 648 | **2406** (89.4%) |
| **measured xG** | **220** | **1678** (62.4%) |

The last one matters most: we had real xG for 220 rows and were *rebuilding* it
from shot zones in `xg.py` — an approximation made precisely because the
measurement was missing. Now there is seven times as much real xG.

One thing to say plainly: **no bet changes**. The goals model (Dixon-Coles), which
produces 1X2, total goals, both teams to score, exact score and handicap, is
trained **only on goals** — not shots, not xG, not possession. And of the
statistics, only corners and cards are bet on, and those were complete already.
The gain is in reading: the tables on *Match prediction* and *Teams* and the
secondary models (22 now) have something to stand on.

### What remains unguaranteed

A **plausible but wrong** figure gets through. The guard catches the impossible,
not the incorrect: if Flashscore says 9 corners and there were 10, nothing catches
it. And where two sources plausibly contradict each other, we follow a rule, not
proof.

## Who has the last word on corners and cards

LPF is the league's official source and remains the source for the other
statistics. But on **the two markets that are bet on** it has proved unreliable,
and the comparison with Flashscore, across all 61 matches of the season, shows it:

| | match | differ |
|---|---|---|
| corners | 54 / 61 | 7 |
| yellow cards | 53 / 59 | 6 |

Most differences are one or two and come down to how each source counts. One,
however, does not: for **Dinamo — FCSB**, LPF publishes 2-2 corners in a match
with 32 shots, where Flashscore gives 14.

So Flashscore has the last word on `corners`, `yellow_cards` and `red_cards`; LPF
only fills in where nobody has anything.

### Precedence does not depend on the order of the steps

The temptation is to leave "whoever writes last wins" and put the Flashscore step
after the LPF one. It would work — until the first reordering of the steps, when
it would break silently.

Instead, `upsert_team_stats` takes `fill_only`: the columns the provider sets
**only if nobody has them**. LPF puts those three there, Flashscore writes them
normally. The result is the same in either order — and there is a test for
exactly that.

```sql
-- normal:     the new value wins
corners = COALESCE(excluded.corners, match_team_stats.corners)
-- fill_only:  the old value stays
corners = COALESCE(match_team_stats.corners, excluded.corners)
```

### What changed with the fill-in

Ten matches corrected, the biggest being Dinamo — FCSB (4 → 14 corners). Two
picks changed their verdict — "under 10.5 corners" went from lost to won, and so
did "over 2.5 cards" — and one slip came out in profit.

The effect on accuracy, measured *without* any model change, just because the
ground truth is more accurate:

| market | Brier before | after | calibration deviation |
|---|---|---|---|
| corners | 0.258 | **0.245** | −0.070 → **−0.041** |
| cards | 0.174 | **0.165** | −0.025 → **−0.013** |

One request per match, so the list is kept short: on a normal run it is the
round's eight matches, plus any older match that is missing its corners.

---

## Live picks, from what has happened so far

The idea fits in one sentence: **what has been played is certain; the rest is
estimated from the tempo so far blended with what was expected before kick-off.**
Three corners in 45 minutes means neither six at the end (the observed tempo,
taken blindly) nor nine and a half (the expectation, as if nothing had been
played).

The blend is a Gamma-Poisson update. The expectation weighs as much as one whole
match of observations, so at minute 45 the two pull equally; towards the end it is
almost entirely what has been seen that decides — and it has to, because by then
there is little left to estimate.

```
rate_per_90 = (a · λ + k) / (a + m / 90)        a = 1 match of "weight"
```

The rest of the match is counted with **Poisson, not Negative Binomial**. The
dispersion measured in `secondary.py` describes the spread of a whole match,
largely from differences *between* matches — referee, stakes, pitch. By minute 48
those have already happened and do not repeat over the remainder; applied to the
rest, it would widen the interval for nothing. It is the same reasoning for which
`inplay.py` does not apply the Dixon-Coles correction to the rest of the match.

### What is shown

One pick per market, not five neighbouring lines saying the same thing, and only
between 65% and 97%: below the threshold it is not worth saying, above it it is no
longer a pick — "over 0.5 goals" at 2-0 has probability 1 and is not news.

**The order is by how much the match has moved the probability**, not by how high
it is. That the favourite is still the favourite says nothing; that "under 8.5
corners" rose from 43% to 82% does.

| the pick | probability | fair odds | before the match | move |
|---|---|---|---|---|
| corners: under 8.5 | 81.6% | 1.23 | 43.1% | **+38.5** |
| total goals 2.5: under | 86.9% | 1.15 | 49.4% | +37.5 |
| both teams to score: no | 80.1% | 1.25 | 49.4% | +30.6 |
| double chance: 1X | 80.7% | 1.24 | 77.2% | +3.5 |

### What they are not

**They are not "value".** It was measured on 1004 matches that where the model
parts ways with the market, it is the model that is wrong more often. Here they
are not even compared with live odds — we have no such thing. The odds shown are
the *fair* ones according to the model: there is profit only if you find higher
odds at a bookmaker.

**Cards pile up towards the end**, while the model assumes a constant tempo. We do
not have their minutes in the database, so the profile cannot be measured; until
then, "over" on cards comes out underestimated. The page says so too.

### A bug found while building it

Flashscore writes "Univ. Craiova" and "'U' Cluj"; the model knows "Universitatea
Craiova" and "Universitatea Cluj". Without translation, `attack.get(name, 1.0)`
silently returned the league-average strength for both — so **every** figure on
the Live page was computed with average teams: 35.8% for the home side instead of
42.0%. Nobody would have noticed, because the number came out plausible. Names
now go through `canonical_team()`.

## Transfers, with the money included

Each club's transfer page on Transfermarkt has two tables — arrivals and
departures — and, unlike the press, **it publishes the fees**. They are fetched on
the full update, once per club, and end up in the `transfers` table.

Current season: **581 transfers across 16 clubs**, €9.34m paid and €18.21m
received across the whole league.

### Free and undisclosed are not the same thing

The easiest mistake to make here is to set a missing fee to zero. A club that sold
its players for undisclosed fees would then appear to have given them away — and
its balance would lie.

So `0.0` means **free, and known** ("free transfer", "loan transfer"), while
`None` means **not disclosed** ("?"). Only the former count towards profit; the
latter are counted separately and shown on the page, so you know how complete the
tally is: **442 free, 96 undisclosed, 190 loans**.

The forms the parser understands:

| how the source writes it | what it means |
|---|---|
| `€1.00m`, `€600k` | the fee, in euros |
| `free transfer` | free, known → `0` |
| `Loan fee: €1.50m` | loan **with a fee** → the amount, marked as a loan |
| `loan transfer` | loan without a fee → `0` |
| `?` | undisclosed → `None` |

On the page, the "most expensive moves" table keeps a column with **the source's
original text**, so the interpretation can be checked without opening
Transfermarkt.

### The table header says which is which

Not the table's position on the page, but its header: the **"Left"** column means
*where he came from* — so it is the **arrivals** table; **"Joined"** means *where
he went*. The opposite of how it sounds.

### The balance, by club

|  | paid | received | profit |
|---|---|---|---|
| FCSB | €1.70m | €5.10m | **+€3.40m** |
| Rapid București | €0.35m | €2.90m | +€2.55m |
| Farul Constanța | 0 | €2.30m | +€2.30m |
| … | | | |
| Universitatea Craiova | €5.90m | €3.10m | **−€2.80m** |

### A better source than the press for departures

The useful side effect: transfers say who has left **with the money included**,
earlier and more reliably than the press. The case that started all this —
Daniel Bîrligea was still shown at FCSB even though he had left a week earlier —
now resolves itself: the row "FCSB → Frosinone, Loan fee: €1.50m" is enough.

They feed into `departed_players()` with two guards, both necessary:

* **"End of loan"** rows are not departures — they are returns to the owning club,
  sometimes with a future date (that is how Garita and Dumbravanu were kept);
* nobody who **has played in the last 30 days** is removed, whatever the table
  says (that is how Korenica stayed, transferred but present on the pitch).

## Absences: we collect them, but the model does not see them (yet)

The comment in the schema states the problem: *"Without this, the model assumes
the whole squad is fit, and the absence of a top scorer goes unnoticed."* Exactly
the information we know the market has and we do not.

Two things were fixed here:

1. **Absences were not updated by the refresh at all.** The "Squads and players"
   step fetched the squads but did not call `fetch_absences` — injuries were only
   refreshed on the full ingestion of the season. Now they are fetched on every
   full update.
2. **A snapshot is saved on every update.** Transfermarkt only publishes the state
   *now*; without history we will never know who was missing from the matches
   already played, so an absence adjustment **could never be measured**. The
   `availability_snapshots` and `availability_snapshot_rows` tables solve that.

The snapshot header is written even when nobody is missing, so that we can tell
"nobody was absent" from "we didn't look": `availability_on(date)` gives the
absentees, `had_availability_snapshot(date)` says whether we are entitled to a
conclusion.

**The adjustment itself is deliberately not implemented.** It cannot be validated
today, and the project has already rejected isotonic calibration and per-team home
advantage precisely because the measurement did not support them. After a few
months of snapshots the question becomes measurable — that is when it is worth
building, not before.

---

## Limitations, stated plainly

* **xG only covers 2022–2024** (the limit of the free API-Football plan). Corners,
  cards and shots for the current season come from LPF.ro, but LPF does not
  publish xG.
* **The live score exists, but the in-play model is simple.** It only sees the
  score and the minute; it knows nothing about sendings-off, tactical changes
  after a goal or fatigue. And the minute is sometimes estimated from the time
  elapsed since kick-off rather than read from the feed — so around half-time it
  can be off by up to a quarter of an hour.
* **The model does not beat the market.** Bookmakers' odds incorporate information
  the model does not have — injuries, rotated squads, motivation. The betting
  slips are an analysis tool, not a way to win.
* **Pre-match odds come from an undocumented endpoint.** It works today, verified
  on real data; it can stop at any moment, without notice. The code flags this
  explicitly instead of staying silent, but it cannot fix it on its own.
* **The betting-slip backfill is honest, but short.** Over a few dozen slips, the
  ROI is dominated by luck. You need a few hundred for a conclusion.
* **The goalscorer market is unusable for now.** The backfill redone without
  leakage (6 September 2026) shows how much the leakage was hiding: the model
  predicts **46.9 %**, it happens **14.3 %** of the time — three times less, over
  21 picks. The difference is **3 standard deviations**, so it is not noise. With
  the leakage active, the same figures showed 46.3 % vs 36.4 %.

  The calibrator has caught the correction (in the 50–65 % bin it predicted 57.4 %
  and 0 of 5 were observed), so the probabilities are toned down automatically.
  But 21 picks are few even for the calibrator: **do not rely on goalscorer
  slips** until a few hundred have accumulated.
* **The "value" logic has no demonstrable edge** — in fact it measurably loses on
  the large disagreements. See *When the model disagrees with the market*.
* **Absences do not go into the model**, although they are collected. See the
  section above: they cannot be validated until a few months of snapshots have
  accumulated.
* **Nor does the news go into the model**, for the same reason. And extraction
  from free text is rule-based: it catches plainly stated facts well, and gets
  confused by irony and quotes. The "confirmed" verdict is the article's, not
  ours.

---

## Troubleshooting

**Diacritics show up wrong in the terminal.** Run with
`PYTHONIOENCODING=utf-8 python scripts/...` or `chcp 65001` beforehand.

**`streamlit` is not recognised.** Use `python -m streamlit run ...`.

**API-Football returns `Too many requests`.** You have exceeded 10 requests per
minute. The code waits 6.5 seconds between requests; if you run two scripts at
the same time, the limit is exceeded anyway. Run just one.

**"Free plans do not have access to this season".** Normal — see the table above.
The current season comes from the other sources.
