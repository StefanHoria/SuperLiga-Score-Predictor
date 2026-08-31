"""Profilul detaliat al unei echipe: formă, statistici, comparație cu liga."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from common import (  # noqa: F401
    local_series,
    data_version, load_matches, load_team_stats, refresh_button, require_pipeline,
    season_label, setup_page, sidebar_footer,
)
from predictiescor.modeling.secondary import SUPPORTED_STATS

setup_page("Echipe", "📊")
version = data_version()
refresh_button()

pipeline = require_pipeline()
sidebar_footer(pipeline)

st.title("📊 Analiza echipelor")

matches = load_matches(version)
team_stats = load_team_stats(version)

tab_team, tab_league = st.tabs(["Profil echipă", "Comparație în ligă"])

# ======================================================================
with tab_team:
    teams = pipeline.current_season_teams()
    if st.sidebar.checkbox("Include echipe din sezoane trecute", value=False):
        teams = pipeline.teams
    team = st.selectbox("Echipă", teams)

    played = matches[(matches["home_team"] == team) | (matches["away_team"] == team)]
    if played.empty:
        st.info("Nu există meciuri pentru această echipă.")
        st.stop()

    # ---- indicatori generali ----
    scored = np.where(played["home_team"] == team,
                      played["home_goals"], played["away_goals"])
    conceded = np.where(played["home_team"] == team,
                        played["away_goals"], played["home_goals"])
    wins = int((scored > conceded).sum())
    draws = int((scored == conceded).sum())
    losses = int((scored < conceded).sum())

    cols = st.columns(6)
    cols[0].metric("Meciuri", len(played))
    cols[1].metric("V-E-Î", f"{wins}-{draws}-{losses}")
    cols[2].metric("Goluri marcate / meci", f"{scored.mean():.2f}")
    cols[3].metric("Goluri primite / meci", f"{conceded.mean():.2f}")
    if pipeline.elo:
        cols[4].metric("Rating Elo", f"{pipeline.elo.rating(team):.0f}")
    ratings = pipeline.goals.team_ratings()
    row = ratings[ratings["echipa"] == team]
    if not row.empty:
        rank = int(row.index[0]) + 1
        cols[5].metric("Loc după forța modelului", f"{rank} / {len(ratings)}")

    st.divider()

    # ---- forma recenta ----
    left, right = st.columns([3, 2])
    with left:
        st.subheader("Ultimele meciuri")
        recent = played.sort_values("date", ascending=False).head(15).copy()
        recent["acasă/deplasare"] = np.where(
            recent["home_team"] == team, "acasă", "deplasare")
        recent["adversar"] = np.where(
            recent["home_team"] == team, recent["away_team"], recent["home_team"])
        recent["scor"] = (recent["home_goals"].astype(int).astype(str) + "-"
                          + recent["away_goals"].astype(int).astype(str))
        recent["marcate"] = np.where(recent["home_team"] == team,
                                     recent["home_goals"], recent["away_goals"])
        recent["primite"] = np.where(recent["home_team"] == team,
                                     recent["away_goals"], recent["home_goals"])
        recent["rezultat"] = np.select(
            [recent["marcate"] > recent["primite"],
             recent["marcate"] == recent["primite"]],
            ["V", "E"], "Î")
        recent["data"] = local_series(recent["date"], "%d.%m.%Y")
        st.dataframe(
            recent[["data", "adversar", "acasă/deplasare", "scor", "rezultat"]],
            hide_index=True, width="stretch", height=430)

    with right:
        st.subheader("Acasă vs deplasare")
        home_mask = played["home_team"] == team
        summary = pd.DataFrame({
            "context": ["acasă", "deplasare"],
            "meciuri": [int(home_mask.sum()), int((~home_mask).sum())],
            "goluri marcate/meci": [
                float(played.loc[home_mask, "home_goals"].mean() or 0),
                float(played.loc[~home_mask, "away_goals"].mean() or 0)],
            "goluri primite/meci": [
                float(played.loc[home_mask, "away_goals"].mean() or 0),
                float(played.loc[~home_mask, "home_goals"].mean() or 0)],
        })
        st.dataframe(summary, hide_index=True, width="stretch")

        if pipeline.elo:
            history = pipeline.elo.history_frame()
            team_history = history[history["echipa"] == team]
            if not team_history.empty:
                fig = px.line(team_history, x="date", y="elo", height=280)
                fig.update_layout(margin=dict(l=0, r=0, t=20, b=0),
                                  xaxis_title="", yaxis_title="Elo",
                                  title="Evoluție Elo")
                st.plotly_chart(fig, width="stretch")

    st.divider()

    # ---- statistici detaliate ----
    st.subheader("Statistici detaliate")
    if team_stats.empty:
        st.info("Nu există încă statistici pe meci în baza de date.")
        st.code("python scripts/fetch_data.py --stats-only", language="bash")
    else:
        team_rows = team_stats[team_stats["team"] == team]
        if team_rows.empty:
            st.info(f"Nu există statistici detaliate pentru {team}.")
        else:
            available = [c for c in SUPPORTED_STATS
                         if c in team_rows.columns and team_rows[c].notna().any()]
            league_means = {c: float(team_stats[c].mean()) for c in available}
            rows = []
            for stat in available:
                team_mean = float(team_rows[stat].mean())
                league = league_means[stat]
                rows.append({
                    "statistică": SUPPORTED_STATS[stat],
                    "medie echipă": round(team_mean, 2),
                    "medie ligă": round(league, 2),
                    "raport": round(team_mean / league, 2) if league else None,
                    "meciuri": int(team_rows[stat].notna().sum()),
                })
            frame = pd.DataFrame(rows)
            c1, c2 = st.columns([2, 3])
            c1.dataframe(frame, hide_index=True, width="stretch", height=420)

            radar = frame.dropna(subset=["raport"])
            if len(radar) >= 3:
                fig = go.Figure()
                fig.add_trace(go.Scatterpolar(
                    r=radar["raport"], theta=radar["statistică"],
                    fill="toself", name=team))
                fig.add_trace(go.Scatterpolar(
                    r=[1.0] * len(radar), theta=radar["statistică"],
                    name="media ligii", line=dict(dash="dot")))
                fig.update_layout(
                    height=420, margin=dict(l=40, r=40, t=30, b=30),
                    polar=dict(radialaxis=dict(visible=True, range=[0, max(
                        1.6, float(radar["raport"].max()) * 1.1)])),
                    showlegend=True)
                c2.plotly_chart(fig, width="stretch")

# ======================================================================
with tab_league:
    st.subheader("Toate echipele, comparate")
    ratings = pipeline.goals.team_ratings().rename(columns={
        "echipa": "Echipă", "atac": "Atac", "aparare": "Apărare",
        "goluri_marcate_asteptate": "Goluri marcate așteptate",
        "goluri_primite_asteptate": "Goluri primite așteptate",
        "forta_totala": "Forță", "meciuri_efective": "Meciuri efective"})
    st.dataframe(ratings, hide_index=True, width="stretch", height=380,
                 column_config={c: st.column_config.NumberColumn(format="%.3f")
                                for c in ratings.columns if c != "Echipă"})

    fig = px.scatter(
        ratings, x="Goluri marcate așteptate", y="Goluri primite așteptate",
        text="Echipă", size="Meciuri efective", color="Forță",
        color_continuous_scale="RdYlGn", height=560)
    fig.update_traces(textposition="top center")
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(margin=dict(l=0, r=0, t=30, b=0),
                      title="Dreapta-sus = atac bun și apărare bună")
    st.plotly_chart(fig, width="stretch")

    if not team_stats.empty:
        st.divider()
        st.subheader("Clasament pe o statistică anume")
        options = [c for c in SUPPORTED_STATS
                   if c in team_stats.columns and team_stats[c].notna().any()]
        if options:
            stat = st.selectbox("Statistică", options,
                                format_func=lambda c: SUPPORTED_STATS[c])
            grouped = (team_stats.groupby("team")[stat]
                       .agg(["mean", "count"]).reset_index()
                       .rename(columns={"team": "Echipă", "mean": "medie/meci",
                                        "count": "meciuri"}))
            grouped = grouped[grouped["meciuri"] >= 3].sort_values(
                "medie/meci", ascending=False)
            fig = px.bar(grouped, x="medie/meci", y="Echipă", orientation="h",
                         height=max(300, 26 * len(grouped)),
                         hover_data=["meciuri"])
            fig.update_layout(margin=dict(l=0, r=0, t=10, b=0),
                              yaxis=dict(autorange="reversed"), yaxis_title="")
            st.plotly_chart(fig, width="stretch")
