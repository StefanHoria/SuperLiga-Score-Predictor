"""Administrarea datelor: ce surse merg, cat de complete sunt datele, ce lipseste."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from common import (
    data_version, load_matches, load_summary, load_team_stats, local_time,
    refresh_button, run_data_refresh, season_label, setup_page, sidebar_footer,
)
from predictiescor.config import get_config
from predictiescor.data.aggregator import DataAggregator
from predictiescor.data.validate import (
    implausible_rows, missing_columns,
)
from predictiescor.storage.db import Database
from predictiescor.utils.teamnames import unknown_teams

setup_page("Date și surse", "🗄️")
version = data_version()
refresh_button()
sidebar_footer()

cfg = get_config()
db = Database()

st.title("🗄️ Date și surse")

summary = load_summary(version)
cols = st.columns(6)
labels = {
    "meciuri": "Meciuri", "meciuri_jucate": "Jucate",
    "meciuri_cu_statistici": "Cu statistici", "echipe": "Echipe",
    "jucatori": "Jucători", "cote": "Cote",
}
for col, (key, label) in zip(cols, labels.items()):
    col.metric(label, f"{summary[key]:,}".replace(",", "."))

st.divider()

# ----------------------------------------------------------------------
st.subheader("Starea surselor")
aggregator = DataAggregator(db=db, cfg=cfg)
status = pd.DataFrame(aggregator.status())
status["stare"] = status["stare"].map({
    "activ": "✅ activ", "blocat": "⛔ blocat",
    "oprit din .env": "➖ oprit din .env"}).fillna(status["stare"])
st.dataframe(status.rename(columns={
    "sursa": "sursă", "ofera": "oferă", "observatii": "observații",
    "sezoane": "sezoane acoperite", "buget": "buget cereri"}),
    hide_index=True, width="stretch")

with st.expander("Ce face fiecare sursă și de ce"):
    st.markdown("""
| Sursă | Ce aduce | Acoperire | Observații |
|---|---|---|---|
| **football-data.co.uk** | rezultate + cote 1X2 | toate sezoanele, inclusiv cel curent | CSV public, foarte stabil. Baza rezultatelor. |
| **LPF.ro** (oficial) | 23 de statistici pe meci + formații, marcatori, minute jucate | **sezonul curent** | Site-ul oficial al ligii. Acoperă exact golul lăsat de API-Football. |
| **API-Football** | idem, plus xG și statistici pe jucător | **doar 2022–2024** pe planul gratuit | 100 cereri/zi **și** 10 cereri/minut. Statisticile se cer per meci, deci baza se umple în câteva zile de rulări. |
| **Transfermarkt** | calendar, loturi, minute, goluri/pase, cartonașe | toate sezoanele, inclusiv cel curent | Singura sursă gratuită pentru jucătorii din sezonul în curs. |
| **Sofascore** | statistici bogate pe meci | — | Răspunde `403` la acces automat. Codul e gata; se activează dacă devine accesibil. |
| **FBref** | xG alternativ, statistici avansate | — | Protejat de Cloudflare. Idem. |

**De reținut:** planul gratuit API-Football nu acoperă sezonul curent. De aceea
rezultatele curente vin de la football-data.co.uk, statisticile pe meci de la
LPF.ro, iar datele despre jucătorii de azi de la Transfermarkt. Singurul lucru
care rămâne doar pe 2022–2024 este **xG**, pe care LPF nu îl publică.

**Două precizări despre LPF.** Publică pagini de statistici și pentru meciuri
care nu s-au disputat încă, cu valori care arată plauzibil — providerul scrie
doar pe meciurile care au deja un scor confirmat din altă sursă. Iar
autogolurile apar cu aceeași iconiță ca golurile normale, pe partea
jucătorului: de aceea marcatorii sunt folosiți la decontare doar când
reconstruiesc exact scorul fiecărei echipe.
""")

st.divider()

# ----------------------------------------------------------------------
st.subheader("Cât de complete sunt datele")
matches = load_matches(version)
team_stats = load_team_stats(version)

if not matches.empty:
    rows = []
    stats_ids = set(team_stats["match_id"]) if not team_stats.empty else set()
    for season, group in matches.groupby("season"):
        ids = set(group["match_id"])
        covered = len(ids & stats_ids)
        rows.append({
            "sezon": season_label(int(season)),
            "meciuri": len(ids),
            "cu statistici": covered,
            "acoperire": covered / len(ids) if ids else 0.0,
        })
    coverage = pd.DataFrame(rows)
    left, right = st.columns([2, 3])
    left.dataframe(
        coverage, hide_index=True, width="stretch",
        column_config={"acoperire": st.column_config.ProgressColumn(
            format="percent", min_value=0.0, max_value=1.0)})

    fig = px.bar(coverage, x="sezon", y=["cu statistici", "meciuri"],
                 barmode="overlay", height=340,
                 labels={"value": "meciuri", "variable": ""})
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0))
    right.plotly_chart(fig, width="stretch")

st.divider()

# ----------------------------------------------------------------------
st.subheader("Actualizează datele")
st.caption(
    "Datele se acumulează: fiecare rulare completează doar ce lipsește, "
    "nimic nu se descarcă de două ori."
)

left, right = st.columns([1, 1])

with left:
    with st.container(border=True):
        st.markdown("#### Rapid")
        st.caption(
            "Rezultatele noi, calendarul și statisticile oficiale LPF "
            "(cornere, cartonașe, șuturi) pentru sezonul curent. "
            "Durează ~10 secunde și nu consumă cota API."
        )
        if st.button("⬇️ Actualizare rapidă", width="stretch", type="primary",
                     key="quick_refresh"):
            if run_data_refresh(quick=True) is not None:
                st.rerun()

with right:
    with st.container(border=True):
        st.markdown("#### Completă")
        st.caption(
            "În plus: loturile de jucători și statisticile istorice "
            "(2022–2024, cu xG) de la API-Football. Poate dura câteva minute — "
            "sunt acceptate doar 10 cereri pe minut."
        )
        opt1, opt2 = st.columns(2)
        budget = opt1.number_input(
            "Cereri API-Football", 0, 100,
            int(cfg.league.get("apifootball_daily_budget", 90)), step=10,
            help="Limita gratuită e 100 pe zi. Fiecare cerere aduce "
                 "statisticile unui meci.")
        with_players = opt2.checkbox("Reîmprospătează loturile", value=True)
        make_tickets = st.checkbox(
            "Generează și biletele zilei următoare", value=False)

        if st.button("⬇️ Actualizare completă", width="stretch",
                     key="full_refresh"):
            report = run_data_refresh(
                quick=False, with_players=with_players,
                api_budget=int(budget), with_tickets=make_tickets)
            if report is not None:
                st.rerun()

last = st.session_state.get("last_refresh_rows")
if last:
    fresh = st.session_state.pop("last_refresh_fresh", False)
    with st.expander("Rezultatul ultimei actualizări", expanded=fresh):
        st.dataframe(pd.DataFrame(last), hide_index=True, width="stretch")

with st.expander("Aceleași lucruri, din terminal"):
    st.markdown(
        "Butoanele de mai sus rulează exact acest cod. Pentru automatizare "
        "(vezi README) folosește comenzile direct:")
    st.code("python scripts/update.py --quick", language="bash")
    st.code("python scripts/update.py --with-tickets", language="bash")
    st.markdown("Ingestie punctuală, pe o singură sursă:")
    st.code("python scripts/fetch_data.py --sources footballdata", language="bash")
    st.code("python scripts/fetch_data.py --sources transfermarkt", language="bash")
    st.code("python scripts/fetch_data.py --stats-only --budget 90",
            language="bash")

st.divider()

# ----------------------------------------------------------------------
st.subheader("Sănătatea datelor")

orphans = db.orphan_teams()
if orphans.empty:
    st.success("Nicio echipă duplicată: toate numele s-au mapat corect.")
else:
    st.warning(
        "Echipe fără niciun meci — cel mai probabil variante de nume pe care "
        "normalizatorul nu le cunoaște încă."
    )
    st.dataframe(orphans, hide_index=True, width="stretch")
    st.markdown("Le poți uni manual aici:")
    c1, c2, c3 = st.columns([2, 2, 1])
    source = c1.selectbox("Echipa duplicată", orphans["name"].tolist())
    target = c2.selectbox("Se unește în", sorted(db.teams()["name"].tolist()))
    c3.write("")
    c3.write("")
    if c3.button("Unește"):
        moved = db.merge_teams(source, target)
        st.success(f"{moved} rânduri mutate din „{source}” în „{target}”.")
        st.cache_data.clear()
        st.rerun()

unmapped = unknown_teams()
if unmapped:
    st.info(
        "Nume de echipe care nu au fost recunoscute în această sesiune: "
        + ", ".join(unmapped)
        + ". Adaugă-le în `src/predictiescor/utils/teamnames.py`."
    )

st.divider()

# ----------------------------------------------------------------------
# Verificarea de plauzibilitate
#
# Tot ce s-a găsit stricat până acum s-a găsit fiindcă s-a uitat cineva. Aici
# se uită aplicația, la fiecare deschidere a paginii.
# ----------------------------------------------------------------------
st.subheader("Verificarea datelor")

stricate = implausible_rows(db)
if not stricate:
    st.success(
        "**Nicio valoare imposibilă în baza de date.** Se verifică la "
        "scriere, nu la citire: o cifră care nu poate fi adevărată — zero "
        "pase, posesie peste 100, cartonașe cu zecimale — se scrie ca "
        "„nu știu”, nu ca zero."
    )
else:
    st.error(f"**{len(stricate)} rânduri cu valori imposibile.** Au intrat "
             f"înainte ca verificarea să existe, sau printr-un drum care o "
             f"ocolește.")
    st.dataframe(
        pd.DataFrame([{
            "sezon": r["season"],
            "data": local_time(r["utc_date"], "%d.%m.%Y"),
            "meci": r["match_id"],
            "câmpuri": ", ".join(r["campuri"]),
            "valori": str(r["valori"]),
        } for r in stricate[:50]],),
        hide_index=True, width="stretch")

lipsa = missing_columns(db, ("corners", "yellow_cards", "shots", "possession"))
jucate = int(summary["meciuri_jucate"])
st.caption("**Cât de completă e fiecare coloană**, din cele "
           f"{jucate:,} meciuri jucate:".replace(",", "."))
st.dataframe(
    pd.DataFrame([{
        "statistică": nume,
        "meciuri cu date": jucate - fara,
        "acoperire": (jucate - fara) / jucate if jucate else 0.0,
    } for nume, fara in lipsa.items()]),
    hide_index=True, width="stretch",
    column_config={"acoperire": st.column_config.ProgressColumn(
        format="percent", min_value=0.0, max_value=1.0)})
st.caption(
    "Golurile nu sunt erori — sunt limita reală a modelului. Un meci fără "
    "cornere înregistrate nu strică nimic, dar nici nu învață nimic; "
    "modelul de cornere vede doar meciurile din coloana din mijloc."
)

st.divider()
st.subheader("Jurnalul descărcărilor")
log = db.ingest_log_df(limit=30)
if log.empty:
    st.caption("Încă nicio rulare înregistrată.")
else:
    st.dataframe(
        log[["finished_at", "source", "scope", "rows", "status", "message"]]
        .rename(columns={"finished_at": "când", "source": "sursă",
                         "scope": "domeniu", "rows": "rânduri",
                         "status": "stare", "message": "mesaj"}),
        hide_index=True, width="stretch", height=320)

st.divider()
with st.expander("Configurație activă"):
    st.json({
        "ligă": cfg.league,
        "sezon curent": cfg.current_season,
        "bază de date": str(cfg.db_path),
        "API-Football configurat": cfg.secrets.has_apifootball,
    })
