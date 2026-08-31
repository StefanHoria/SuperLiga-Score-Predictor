"""Predictii pentru toate meciurile care urmeaza."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from common import (  # noqa: F401
    local_series,
    data_version, refresh_button, require_pipeline, setup_page, sidebar_footer,
)
from predictiescor.storage.db import Database

setup_page("Etapa următoare", "📅")
version = data_version()
refresh_button()

pipeline = require_pipeline()
sidebar_footer(pipeline)

st.title("📅 Meciuri programate")

db = Database()
upcoming = db.upcoming_matches(limit=100)

if upcoming.empty:
    st.info(
        "Nu există meciuri programate în baza de date. Calendarul viitor vine "
        "de la Transfermarkt — adu-l cu comanda de mai jos."
    )
    st.code("python scripts/update.py --quick", language="bash")
    st.markdown("**Alternativ**, compune manual o etapă mai jos, sau prezice "
                "un singur meci pe pagina dedicată.")
    st.page_link("pages/1_Predictie_meci.py", label="Predicție meci", icon="🎯")

    st.divider()
    st.subheader("Simulează o etapă")
    st.caption("Introdu perechile de echipe, câte una pe rând: `Gazdă - Oaspete`")
    teams = pipeline.current_season_teams()
    default = "\n".join(
        f"{teams[i]} - {teams[i + 1]}"
        for i in range(0, min(len(teams) - 1, 8), 2)
    )
    raw = st.text_area("Meciuri", value=default, height=180)
    if st.button("Calculează predicțiile", type="primary"):
        rows = []
        for line in raw.splitlines():
            if "-" not in line:
                continue
            home, _, away = line.partition("-")
            home, away = home.strip(), away.strip()
            if not home or not away:
                continue
            try:
                prediction = pipeline.predict(home, away)
            except Exception as exc:  # noqa: BLE001
                st.warning(f"{line.strip()}: {exc}")
                continue
            result = prediction.markets["1X2"]
            over25 = prediction.markets["total goluri 2.5"]
            btts = prediction.markets["ambele marcheaza"]
            rows.append({
                "meci": f"{prediction.home_team} — {prediction.away_team}",
                "1": result["gazda"], "X": result["egal"], "2": result["oaspete"],
                "scor probabil": prediction.most_likely_score,
                "goluri aștept.": round(prediction.expected_total_goals, 2),
                "peste 2.5": over25["peste"],
                "ambele marchează": btts["da"],
            })
        if rows:
            frame = pd.DataFrame(rows)
            st.dataframe(
                frame, hide_index=True, width="stretch",
                column_config={
                    c: st.column_config.ProgressColumn(
                        format="percent", min_value=0.0, max_value=1.0)
                    for c in ("1", "X", "2", "peste 2.5", "ambele marchează")})
            st.download_button(
                "Descarcă CSV", frame.to_csv(index=False).encode("utf-8"),
                file_name="predictii_etapa.csv", mime="text/csv")
    st.stop()

# ----------------------------------------------------------------------
upcoming["data"] = local_series(upcoming["date"])
limit = st.slider("Câte meciuri", 5, min(50, len(upcoming)),
                  min(10, len(upcoming)))

rows = []
for row in upcoming.head(limit).itertuples():
    try:
        prediction = pipeline.predict(row.home_team, row.away_team)
    except Exception:  # noqa: BLE001
        continue
    result = prediction.markets["1X2"]
    rows.append({
        "data": row.data,
        "meci": f"{row.home_team} — {row.away_team}",
        "1": result["gazda"], "X": result["egal"], "2": result["oaspete"],
        "scor probabil": prediction.most_likely_score,
        "goluri aștept.": round(prediction.expected_total_goals, 2),
        "peste 2.5": prediction.markets["total goluri 2.5"]["peste"],
        "ambele marchează": prediction.markets["ambele marcheaza"]["da"],
    })

if not rows:
    st.info("Nu am putut calcula predicții pentru meciurile programate.")
    st.stop()

frame = pd.DataFrame(rows)
st.dataframe(
    frame, hide_index=True, width="stretch", height=min(700, 40 * len(frame) + 60),
    column_config={
        c: st.column_config.ProgressColumn(format="percent", min_value=0.0,
                                           max_value=1.0)
        for c in ("1", "X", "2", "peste 2.5", "ambele marchează")})

st.download_button("Descarcă CSV", frame.to_csv(index=False).encode("utf-8"),
                   file_name="predictii_etapa.csv", mime="text/csv")
