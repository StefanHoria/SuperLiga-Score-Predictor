"""Statistici si predictii pe jucatori."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from common import (  # noqa: F401
    local_series,
    data_version, load_pipeline, load_player_season_stats, refresh_button,
    season_label, setup_page, sidebar_footer,
)

setup_page("Jucători", "👤")
version = data_version()
refresh_button()

pipeline = load_pipeline(version)
sidebar_footer(pipeline)

st.title("👤 Jucători")

season_stats = load_player_season_stats(version)
if season_stats.empty:
    st.warning("Nu există statistici de jucători în baza de date.")
    st.markdown("Descarcă-le cu:")
    st.code("python scripts/fetch_data.py --sources transfermarkt", language="bash")
    st.stop()

seasons = sorted(season_stats["season"].unique(), reverse=True)
season = st.sidebar.selectbox("Sezon", seasons, format_func=season_label)
data = season_stats[season_stats["season"] == season].copy()

for col in ("minutes", "goals", "assists", "appearances",
            "yellow_cards", "red_cards"):
    data[col] = pd.to_numeric(data.get(col), errors="coerce").fillna(0.0)

min_minutes = st.sidebar.slider("Minute minime jucate", 0, 1500, 180, step=30)
filtered = data[data["minutes"] >= min_minutes]

tab_top, tab_team, tab_absent, tab_predict = st.tabs(
    ["Clasamente", "Lot pe echipă", "Absenți", "Predicții marcatori"])

# ======================================================================
with tab_top:
    cols = st.columns(4)
    cols[0].metric("Jucători", len(data))
    cols[1].metric("Goluri marcate", int(data["goals"].sum()))
    cols[2].metric("Pase decisive", int(data["assists"].sum()))
    cols[3].metric("Cartonașe galbene", int(data["yellow_cards"].sum()))

    st.divider()
    metric = st.radio(
        "Clasament după",
        ["Goluri", "Pase decisive", "Goluri + pase", "Minute",
         "Goluri la 90 min", "Cartonașe"],
        horizontal=True)

    frame = filtered.copy()
    frame["goluri+pase"] = frame["goals"] + frame["assists"]
    frame["nouăzeci"] = (frame["minutes"] / 90.0).clip(lower=0.1)
    frame["goluri/90"] = frame["goals"] / frame["nouăzeci"]
    frame["cartonașe"] = frame["yellow_cards"] + frame["red_cards"]

    column = {
        "Goluri": "goals", "Pase decisive": "assists",
        "Goluri + pase": "goluri+pase", "Minute": "minutes",
        "Goluri la 90 min": "goluri/90", "Cartonașe": "cartonașe",
    }[metric]

    top = frame.sort_values(column, ascending=False).head(30)
    display = top[["player", "team", "position", "appearances", "minutes",
                   "goals", "assists", "yellow_cards", "red_cards",
                   "goluri/90"]].rename(columns={
        "player": "jucător", "team": "echipă", "position": "poziție",
        "appearances": "meciuri", "minutes": "minute", "goals": "goluri",
        "assists": "pase dec.", "yellow_cards": "galbene",
        "red_cards": "roșii"})
    display["goluri/90"] = display["goluri/90"].round(2)

    left, right = st.columns([3, 2])
    left.dataframe(display, hide_index=True, width="stretch", height=620)

    chart = top.head(15)
    fig = px.bar(chart, x=column, y="player", orientation="h",
                 color="team", height=620,
                 hover_data=["minutes", "appearances"])
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0),
                      yaxis=dict(autorange="reversed"), yaxis_title="",
                      legend_title="")
    right.plotly_chart(fig, width="stretch")

# ======================================================================
with tab_team:
    teams = sorted(data["team"].dropna().unique())
    team = st.selectbox("Echipă", teams)
    squad = data[data["team"] == team].sort_values("minutes", ascending=False)

    cols = st.columns(4)
    cols[0].metric("Jucători folosiți", len(squad))
    cols[1].metric("Goluri echipă", int(squad["goals"].sum()))
    cols[2].metric("Pase decisive", int(squad["assists"].sum()))
    cols[3].metric("Cartonașe", int(squad["yellow_cards"].sum()
                                    + squad["red_cards"].sum()))

    display = squad[["player", "position", "appearances", "minutes", "goals",
                     "assists", "yellow_cards", "red_cards",
                     "points_per_game"]].rename(columns={
        "player": "jucător", "position": "poziție", "appearances": "meciuri",
        "minutes": "minute", "goals": "goluri", "assists": "pase dec.",
        "yellow_cards": "galbene", "red_cards": "roșii",
        "points_per_game": "puncte/meci"})
    st.dataframe(display, hide_index=True, width="stretch", height=520)

    contributors = squad[squad["goals"] + squad["assists"] > 0]
    if not contributors.empty:
        fig = px.bar(
            contributors.sort_values("goals", ascending=False).head(15),
            x="player", y=["goals", "assists"], height=380,
            labels={"value": "număr", "player": "", "variable": ""})
        fig.update_layout(margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, width="stretch")

# ======================================================================
with tab_absent:
    from predictiescor.storage.db import Database

    _db = Database()

    # Transfermarkt actualizează loturile cu întârziere: Tănase apare acolo și
    # după ce trei publicații au scris că a semnat cu Omonia. Presa e mai
    # rapidă, dar mai puțin sigură — de aceea arătăm din câte surse vine.
    plecati = _db.reported_departures()
    if not plecati.empty:
        st.warning(
            f"**{len(plecati)} jucători sunt dați plecați de presă, dar apar "
            f"încă în lot la Transfermarkt.** Îi excludem din predicții — "
            f"costul unei excluderi greșite e mic, al unei predicții pe cineva "
            f"care nu mai e la club e vizibil.")
        plecati = plecati.assign(
            ultima=lambda f: local_series(f["ultima"], "%d.%m.%Y %H:%M"))
        arata = plecati.rename(columns={
            "player_name": "jucător", "echipa": "încă listat la",
            "ultima": "ultima știre", "dovada": "din articol"})
        st.dataframe(
            arata[["jucător", "încă listat la", "surse", "ultima știre",
                   "din articol"]],
            hide_index=True, width="stretch",
            column_config={"din articol": st.column_config.TextColumn(
                width="large")})
        st.caption(
            "Verifică-le: dacă vreunul e greșit, faptul vine dintr-un articol "
            "care afirma plecarea ca sigură. Pagina **Știri** arată propoziția "
            "exactă și linkul."
        )
        st.divider()

    absences = _db.availability_df()
    if absences.empty:
        st.info("Nu există date despre accidentări sau suspendări.")
        st.code("python scripts/update.py", language="bash")
    else:
        cols = st.columns(3)
        cols[0].metric("Jucători indisponibili", len(absences))
        cols[1].metric("Accidentați",
                       int((absences["kind"] == "accidentare").sum()))
        cols[2].metric("Suspendați",
                       int((absences["kind"] == "suspendare").sum()))

        display = absences[["team", "player", "kind", "reason", "since",
                            "expected_return", "missed_matches"]].copy()
        # Transfermarkt nu publica mereu data revenirii; fara asta ar aparea
        # textul "None" in tabel.
        for column in ("reason", "since", "expected_return"):
            display[column] = display[column].fillna("necunoscut")
        display["missed_matches"] = pd.to_numeric(
            display["missed_matches"], errors="coerce")

        st.dataframe(
            display.rename(columns={
                "team": "echipă", "player": "jucător", "kind": "motiv",
                "reason": "detaliu", "since": "din", 
                "expected_return": "revenire estimată",
                "missed_matches": "meciuri ratate"}),
            hide_index=True, width="stretch", height=460)

        st.caption(
            "Acești jucători sunt **excluși automat** din predicțiile de "
            "marcatori. Golurile lor așteptate se redistribuie către restul "
            "echipei, pentru că suma pe echipă rămâne fixată la cât prezice "
            "modelul principal."
        )

# ======================================================================
with tab_predict:
    if pipeline.players is None or not pipeline.players.fitted:
        st.info("Modelul de jucători nu este antrenat.")
    elif not pipeline.ready:
        st.info("Modelul de goluri nu este antrenat, deci nu pot scala "
                "predicțiile individuale.")
    else:
        st.markdown(
            "Alege un meci: golurile așteptate ale echipei sunt împărțite "
            "între jucători după ratele lor la 90 de minute."
        )
        current = pipeline.current_season_teams()
        c1, c2 = st.columns(2)
        home = c1.selectbox("Gazdă", current, key="pl_home")
        away = c2.selectbox("Oaspete", [t for t in current if t != home],
                            key="pl_away")

        predictions = pipeline.predict_players(home, away)
        if predictions.empty:
            st.info("Nu există jucători cu destule minute pentru aceste echipe.")
        else:
            display = predictions.rename(columns={
                "echipa": "echipă", "jucator": "jucător", "pozitie": "poziție",
                "minute_asteptate": "minute aștept.",
                "goluri_asteptate": "goluri aștept.",
                "marcheaza": "marchează", "marcheaza_2plus": "2+ goluri",
                "da_assist": "dă assist", "gol_sau_assist": "gol sau assist",
                "cartonas": "cartonaș", "minute_jucate_sezon": "minute sezon"})
            st.dataframe(
                display, hide_index=True, width="stretch", height=560,
                column_config={
                    "marchează": st.column_config.ProgressColumn(
                        format="percent", min_value=0.0, max_value=1.0),
                    "gol sau assist": st.column_config.ProgressColumn(
                        format="percent", min_value=0.0, max_value=1.0),
                    "cartonaș": st.column_config.ProgressColumn(
                        format="percent", min_value=0.0, max_value=1.0),
                })
            st.download_button(
                "Descarcă CSV",
                predictions.to_csv(index=False).encode("utf-8"),
                file_name=f"marcatori_{home}_{away}.csv".replace(" ", "_"),
                mime="text/csv")
