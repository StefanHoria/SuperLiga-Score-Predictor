"""Pagina de predictie pentru un meci ales manual: toate pietele."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from common import (
    data_version, load_matches, probability_bar, refresh_button, require_pipeline,
    setup_page, sidebar_footer,
)

setup_page("Predicție meci", "🎯")
version = data_version()
refresh_button()

pipeline = require_pipeline()
sidebar_footer(pipeline)

st.title("🎯 Predicție meci")

teams = pipeline.current_season_teams()
all_teams = pipeline.teams
show_all = st.sidebar.checkbox("Arată toate echipele din istoric", value=False)
options = all_teams if show_all else teams

col1, col2, col3 = st.columns([2, 2, 1])
home = col1.selectbox("Echipa gazdă", options,
                      index=0 if options else None)
away_options = [t for t in options if t != home]
away = col2.selectbox("Echipa oaspete", away_options,
                      index=min(1, len(away_options) - 1) if away_options else None)
col3.write("")
col3.write("")
swap = col3.button("↔ Inversează")
if swap:
    home, away = away, home

if not home or not away:
    st.stop()

report = pipeline.full_report(home, away)
prediction = report["predictie"]

# ----------------------------------------------------------------------
st.subheader(f"{home} — {away}")

metrics = st.columns(4)
metrics[0].metric("Goluri așteptate gazdă", f"{prediction.expected_home_goals:.2f}")
metrics[1].metric("Goluri așteptate oaspete", f"{prediction.expected_away_goals:.2f}")
metrics[2].metric("Total goluri așteptat", f"{prediction.expected_total_goals:.2f}")
metrics[3].metric("Scor cel mai probabil", prediction.most_likely_score)

st.divider()

# ----------------------------------------------------------------------
left, right = st.columns([1, 1])

with left:
    st.markdown("#### Rezultat final (1X2)")
    probability_bar(prediction.markets["1X2"])

    if report["elo"]:
        st.caption("Pentru comparație, același meci văzut de modelul Elo:")
        elo_frame = pd.DataFrame({
            "selecție": list(report["elo"].keys()),
            "Dixon-Coles": [prediction.markets["1X2"][k] for k in report["elo"]],
            "Elo": list(report["elo"].values()),
        })
        st.dataframe(elo_frame, hide_index=True, width="stretch",
                     column_config={
                         "Dixon-Coles": st.column_config.NumberColumn(
                             format="percent"),
                         "Elo": st.column_config.NumberColumn(format="percent"),
                     })

    st.markdown("#### Șansă dublă")
    probability_bar(prediction.markets["sansa dubla"])

with right:
    st.markdown("#### Matricea scorurilor")
    size = min(6, prediction.matrix.shape[0])
    matrix = prediction.matrix[:size, :size]
    fig = px.imshow(
        matrix,
        labels=dict(x=f"goluri {away}", y=f"goluri {home}", color="probabilitate"),
        x=[str(i) for i in range(size)], y=[str(i) for i in range(size)],
        color_continuous_scale="Blues", text_auto=".1%", aspect="auto", height=420,
    )
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), coloraxis_showscale=False)
    st.plotly_chart(fig, width="stretch")

st.divider()

# ----------------------------------------------------------------------
tabs = st.tabs([
    "Goluri (peste/sub)", "Ambele marchează", "Scor exact",
    "Handicap asiatic", "Statistici (cornere, cartonașe)", "Jucători",
])

with tabs[0]:
    lines = {k: v for k, v in prediction.markets.items()
             if k.startswith("total goluri")}
    frame = pd.DataFrame([
        {"linie": k.replace("total goluri ", ""),
         "peste": v["peste"], "sub": v["sub"],
         "cotă peste": round(1 / v["peste"], 2) if v["peste"] > 0 else None,
         "cotă sub": round(1 / v["sub"], 2) if v["sub"] > 0 else None}
        for k, v in lines.items()
    ])
    st.dataframe(frame, hide_index=True, width="stretch",
                 column_config={
                     "peste": st.column_config.ProgressColumn(
                         format="percent", min_value=0.0, max_value=1.0),
                     "sub": st.column_config.ProgressColumn(
                         format="percent", min_value=0.0, max_value=1.0)})

    totals = np.zeros(2 * prediction.matrix.shape[0] - 1)
    for i in range(prediction.matrix.shape[0]):
        for j in range(prediction.matrix.shape[0]):
            totals[i + j] += prediction.matrix[i, j]
    dist = pd.DataFrame({"goluri": range(len(totals)), "probabilitate": totals})
    dist = dist[dist["goluri"] <= 8]
    fig = px.bar(dist, x="goluri", y="probabilitate", height=300)
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), yaxis_tickformat=".0%")
    st.plotly_chart(fig, width="stretch")

with tabs[1]:
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Ambele echipe marchează**")
        probability_bar(prediction.markets["ambele marcheaza"])
        st.markdown("**Goluri par / impar**")
        probability_bar(prediction.markets["goluri par/impar"])
    with c2:
        st.markdown("**Poartă goală (adversarul nu marchează)**")
        probability_bar(prediction.markets["poarta goala"])
        st.markdown("**Echipa marchează cel puțin un gol**")
        probability_bar(prediction.markets["echipa marcheaza"])

with tabs[2]:
    c1, c2 = st.columns([1, 1])
    with c1:
        scores = prediction.correct_scores.rename(columns={
            "scor": "scor", "probabilitate": "probabilitate",
            "cota_corecta": "cotă corectă"})
        st.dataframe(scores, hide_index=True, width="stretch",
                     column_config={"probabilitate":
                                    st.column_config.ProgressColumn(
                                        format="percent", min_value=0.0,
                                        max_value=float(
                                            scores["probabilitate"].max()))})
    with c2:
        st.markdown("**Diferența de goluri**")
        margin = prediction.markets["diferenta de goluri"]
        margin = dict(sorted(margin.items(), key=lambda kv: -kv[1])[:8])
        probability_bar(margin)

with tabs[3]:
    handicaps = {k: v for k, v in prediction.markets.items()
                 if k.startswith("handicap asiatic")}
    frame = pd.DataFrame([
        {"linie": k.replace("handicap asiatic ", ""),
         f"{home}": v["gazda"], f"{away}": v["oaspete"], "nul (push)": v["nul"]}
        for k, v in handicaps.items()
    ])
    st.dataframe(frame, hide_index=True, width="stretch",
                 column_config={
                     home: st.column_config.ProgressColumn(
                         format="percent", min_value=0.0, max_value=1.0),
                     away: st.column_config.ProgressColumn(
                         format="percent", min_value=0.0, max_value=1.0)})
    st.caption("Liniile întregi (0, ±1, ±2) pot da 'push' — miza se returnează.")

with tabs[4]:
    secondary = report["secundare"]
    if secondary.empty:
        st.info(
            "Nu există încă suficiente statistici pe meci pentru cornere, "
            "cartonașe și șuturi."
        )
        st.code("python scripts/fetch_data.py --stats-only", language="bash")
    else:
        # Liniile de pariere exista doar pentru cornere si cartonase, deci
        # tinem valorile asteptate separat de probabilitati: intr-un singur
        # tabel, restul statisticilor ar avea coloanele goale.
        over_columns = [c for c in secondary.columns if c.startswith("peste ")]

        expected = secondary[["statistica", "gazda", "oaspete", "total"]].rename(
            columns={"statistica": "statistică", "gazda": home,
                     "oaspete": away})
        st.markdown("#### Valori așteptate")
        st.dataframe(expected, hide_index=True, width="stretch",
                     height=min(760, 36 * len(expected) + 40))

        if over_columns:
            lines = secondary[["statistica", "total", *over_columns]].copy()
            for column in over_columns:
                lines[column] = pd.to_numeric(lines[column], errors="coerce")
            lines = lines.dropna(subset=over_columns, how="all")
            if not lines.empty:
                st.markdown("#### Peste / sub, pe totalul meciului")
                st.dataframe(
                    lines.rename(columns={"statistica": "statistică"}),
                    hide_index=True, width="stretch",
                    column_config={
                        c: st.column_config.NumberColumn(format="percent")
                        for c in over_columns})
                st.caption(
                    "Probabilități pentru **totalul meciului** — ambele echipe "
                    "însumate."
                )

with tabs[5]:
    players = report["jucatori"]
    if players.empty:
        st.info("Nu există statistici de jucători în baza de date.")
        st.code("python scripts/fetch_data.py --sources transfermarkt",
                language="bash")
    else:
        st.markdown("#### Marcatori probabili")
        top = players.head(20).rename(columns={
            "echipa": "echipă", "jucator": "jucător", "pozitie": "poziție",
            "minute_asteptate": "minute aștept.",
            "goluri_asteptate": "goluri aștept.",
            "marcheaza": "marchează", "marcheaza_2plus": "2+ goluri",
            "da_assist": "dă assist", "gol_sau_assist": "gol sau assist",
            "cartonas": "cartonaș",
            "minute_jucate_sezon": "minute sezon"})
        st.dataframe(
            top, hide_index=True, width="stretch",
            column_config={
                "marchează": st.column_config.ProgressColumn(
                    format="percent", min_value=0.0, max_value=1.0),
                "gol sau assist": st.column_config.ProgressColumn(
                    format="percent", min_value=0.0, max_value=1.0),
                "cartonaș": st.column_config.ProgressColumn(
                    format="percent", min_value=0.0, max_value=1.0),
            })
        st.caption(
            "Golurile individuale sunt scalate astfel încât suma pe echipă să "
            "fie egală cu golurile prezise de modelul principal."
        )

st.divider()
with st.expander("Toate piețele, ca tabel (pentru export)"):
    frame = prediction.to_frame()
    st.dataframe(frame, hide_index=True, width="stretch", height=400)
    st.download_button(
        "Descarcă CSV",
        frame.to_csv(index=False).encode("utf-8"),
        file_name=f"predictie_{home}_{away}.csv".replace(" ", "_"),
        mime="text/csv",
    )
