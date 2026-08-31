"""PredictieScor - dashboard SuperLiga.

Pornire:
    python -m streamlit run src/predictiescor/app/main.py
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from common import (  # noqa: E402  (import local, langa scriptul Streamlit)
    APP_TITLE, data_version, load_matches, load_pipeline, load_summary,
    refresh_button, season_label, setup_page, sidebar_footer,
)
from predictiescor.config import get_config  # noqa: E402

setup_page("Prezentare generala")
cfg = get_config()
version = data_version()

st.title("⚽ PredictieScor — SuperLiga României")
st.caption(
    "Predictii statistice pentru toate echipele: rezultate, goluri, cornere, "
    "cartonase, suturi si jucatori. Toate datele si modelele rulează local."
)

refresh_button()

summary = load_summary(version)
matches = load_matches(version)

if matches.empty:
    st.warning("Baza de date este goala.")
    st.markdown("Descarca datele rulând, în terminal, din folderul proiectului:")
    st.code("python scripts/fetch_data.py", language="bash")
    st.stop()

pipeline = load_pipeline(version)
sidebar_footer(pipeline)

# ----------------------------------------------------------------------
# Indicatori principali
# ----------------------------------------------------------------------
cols = st.columns(5)
cols[0].metric("Meciuri în baza de date", f"{summary['meciuri']:,}".replace(",", "."))
cols[1].metric("Meciuri cu statistici detaliate", summary["meciuri_cu_statistici"])
cols[2].metric("Echipe", summary["echipe"])
cols[3].metric("Jucători", summary["jucatori"])
cols[4].metric("Cote înregistrate", f"{summary['cote']:,}".replace(",", "."))

st.divider()

# ----------------------------------------------------------------------
# Clasament calculat din rezultate
# ----------------------------------------------------------------------
left, right = st.columns([3, 2])

with left:
    st.subheader("Clasament")
    seasons = sorted(matches["season"].unique(), reverse=True)
    season = st.selectbox("Sezon", seasons, format_func=season_label)
    season_matches = matches[matches["season"] == season]

    rows: dict[str, dict[str, float]] = {}
    for row in season_matches.itertuples():
        for team, scored, conceded in (
            (row.home_team, row.home_goals, row.away_goals),
            (row.away_team, row.away_goals, row.home_goals),
        ):
            entry = rows.setdefault(team, {
                "M": 0, "V": 0, "E": 0, "Î": 0, "GM": 0, "GP": 0, "Pct": 0})
            entry["M"] += 1
            entry["GM"] += scored
            entry["GP"] += conceded
            if scored > conceded:
                entry["V"] += 1
                entry["Pct"] += 3
            elif scored == conceded:
                entry["E"] += 1
                entry["Pct"] += 1
            else:
                entry["Î"] += 1

    table = pd.DataFrame(rows).T
    if not table.empty:
        table["GD"] = table["GM"] - table["GP"]
        table = table.sort_values(["Pct", "GD", "GM"], ascending=False)
        table.insert(0, "#", range(1, len(table) + 1))
        table = table[["#", "M", "V", "E", "Î", "GM", "GP", "GD", "Pct"]]
        st.dataframe(table.astype(int), width="stretch", height=560)

with right:
    st.subheader("Forța echipelor (model)")
    ratings = pipeline.goals.team_ratings()
    ratings = ratings.rename(columns={
        "echipa": "Echipă", "atac": "Atac", "aparare": "Apărare",
        "forta_totala": "Forță", "meciuri_efective": "Meciuri efective"})
    fig = px.bar(
        ratings.sort_values("Forță"), x="Forță", y="Echipă",
        orientation="h", height=560,
        color="Forță", color_continuous_scale="RdYlGn",
        hover_data={"Atac": ":.3f", "Apărare": ":.3f",
                    "Meciuri efective": ":.1f"},
    )
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0),
                      coloraxis_showscale=False,
                      yaxis_title="", xaxis_title="atac − apărare")
    st.plotly_chart(fig, width="stretch")
    st.caption(
        "Forța = atac − apărare, pe scară logaritmică. Echipele cu puține "
        "meciuri sunt trase spre media ligii, ca să nu apară artificial în vârf."
    )

st.divider()

# ----------------------------------------------------------------------
# Evolutia Elo
# ----------------------------------------------------------------------
if pipeline.elo is not None:
    st.subheader("Evoluția ratingului Elo")
    history = pipeline.elo.history_frame()
    current_teams = pipeline.current_season_teams()
    default = [t for t in ["FCSB", "CFR Cluj", "Universitatea Craiova",
                           "Rapid Bucuresti", "Dinamo Bucuresti"]
               if t in current_teams][:5]
    chosen = st.multiselect(
        "Echipe", sorted(history["echipa"].unique()),
        default=default or current_teams[:5])
    if chosen:
        subset = history[history["echipa"].isin(chosen)]
        fig = px.line(subset, x="date", y="elo", color="echipa", height=420)
        fig.update_layout(margin=dict(l=0, r=0, t=10, b=0),
                          xaxis_title="", yaxis_title="Elo",
                          legend_title="")
        st.plotly_chart(fig, width="stretch")

st.divider()
st.subheader("Ce poți face mai departe")
nav = st.columns(6)
nav[0].page_link("pages/1_Predictie_meci.py", label="Prezice un meci", icon="🎯")
nav[1].page_link("pages/7_Bilete.py", label="Generează bilete", icon="🎫")
nav[2].page_link("pages/8_Live.py", label="Scor live", icon="🔴")
nav[3].page_link("pages/9_Stiri.py", label="Ce scrie presa", icon="📰")
nav[4].page_link("pages/4_Etapa_urmatoare.py", label="Etapa următoare", icon="📅")
nav[5].page_link("pages/6_Date_si_surse.py", label="Date și surse", icon="🗄️")
