"""Scor live: meciurile în desfășurare, cu șansele recalculate pe loc."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import (  # noqa: F401
    local_time, now_local, tz_note,
    data_version, probability_bar, refresh_button, require_pipeline,
    setup_page, sidebar_footer,
)
from predictiescor.data.flashscore import FlashscoreProvider
from predictiescor.modeling.inplay import (
    InPlayState, live_markets, ticket_status_live,
)
from predictiescor.storage.db import Database

def _estimate_minute(match) -> int:
    """Minutul aproximativ, cand feed-ul nu il publica.

    Il deducem din cat a trecut de la start. Nu tine cont de pauza, deci e o
    aproximare - dar mai buna decat sa presupunem minutul zero, care ar da
    sanse identice cu cele de dinainte de meci.
    """
    if match.kickoff is None:
        return 45
    elapsed = (datetime.now(timezone.utc) - match.kickoff).total_seconds() / 60
    # scadem cele 15 minute de pauza daca meciul a trecut de repriza intai
    if elapsed > 60:
        elapsed -= 15
    return int(min(90, max(1, elapsed)))


setup_page("Live", "🔴")
version = data_version()
refresh_button()

pipeline = require_pipeline()
sidebar_footer(pipeline)
db = Database()

st.title("🔴 Scor live")

provider = FlashscoreProvider(db=db)
if not provider.available:
    st.warning("Sursa live e oprită din `.env` (FLASHSCORE_ENABLED=false).")
    st.stop()

if st.button("Reîmprospătează", type="primary"):
    st.cache_data.clear()

try:
    # Nu `fetch_feed`: feed-ul incorporat in pagina Flashscore nu contine
    # meciul care se joaca acum, iar pagina asta tocmai despre el e.
    feed = provider.fetch_feed_with_live()
except Exception as exc:  # noqa: BLE001
    st.error(f"Nu am putut citi feed-ul: {exc}")
    st.stop()

st.caption(f"Actualizat: {now_local():%H:%M:%S} ({tz_note()}) · "
           f"{len(feed)} meciuri în feed")

live = [m for m in feed if m.is_live]
today = datetime.now(timezone.utc).date()
upcoming = sorted(
    [m for m in feed if m.state == "programat" and m.kickoff],
    key=lambda m: m.kickoff)[:6]

# ----------------------------------------------------------------------
if not live:
    st.info("**Niciun meci în desfășurare acum.**")

    if upcoming:
        st.subheader("Următoarele meciuri")
        rows = []
        for match in upcoming:
            delta = match.kickoff - datetime.now(timezone.utc)
            hours = delta.total_seconds() / 3600
            rows.append({
                "începe": local_time(match.kickoff, "%d.%m %H:%M"),
                "peste": (f"{hours:.1f} ore" if hours > 0 else "a început"),
                "meci": match.label,
                "etapa": match.round or "",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    st.divider()
    st.subheader("Simulează o situație din teren")
    st.caption(
        "Alege un meci și un scor, ca să vezi cum se schimbă șansele pe măsură "
        "ce trece timpul. Aceleași calcule rulează automat când e meci live."
    )
    teams = pipeline.current_season_teams()
    c1, c2, c3, c4, c5 = st.columns([2, 2, 1, 1, 1])
    home = c1.selectbox("Gazdă", teams, key="live_home")
    away = c2.selectbox("Oaspete", [t for t in teams if t != home],
                        key="live_away")
    home_goals = c3.number_input("Goluri gazdă", 0, 9, 1)
    away_goals = c4.number_input("Goluri oaspete", 0, 9, 0)
    minute = c5.number_input("Minut", 0, 90, 60)

    state = InPlayState(home, away, int(home_goals), int(away_goals),
                        int(minute))
    result = live_markets(pipeline, state)

    before = pipeline.predict(home, away).markets["1X2"]
    after = result["piete"]["1X2"]

    st.markdown(f"#### {home} {home_goals}-{away_goals} {away}, minutul {minute}")
    cols = st.columns(3)
    for col, key, label in zip(cols, ("gazda", "egal", "oaspete"),
                               (home, "Egal", away)):
        col.metric(label, f"{after[key]:.1%}",
                   delta=f"{after[key] - before[key]:+.1%} față de start")

    left, right = st.columns(2)
    with left:
        st.markdown("**Cum evoluează dacă scorul rămâne așa**")
        minutes = list(range(0, 91, 5))
        curve = []
        for m in minutes:
            snapshot = InPlayState(home, away, int(home_goals),
                                   int(away_goals), m)
            curve.append(live_markets(pipeline, snapshot)["piete"]["1X2"])
        figure = go.Figure()
        for key, name in (("gazda", home), ("egal", "Egal"), ("oaspete", away)):
            figure.add_trace(go.Scatter(
                x=minutes, y=[c[key] for c in curve], mode="lines", name=name))
        figure.add_vline(x=int(minute), line_dash="dot")
        figure.update_layout(height=380, margin=dict(l=0, r=0, t=10, b=0),
                             yaxis_tickformat=".0%", xaxis_title="minut",
                             yaxis_title="șansă", legend_title="")
        st.plotly_chart(figure, width="stretch")

    with right:
        st.markdown("**Piețele de goluri, acum**")
        lines = {k: v for k, v in result["piete"].items()
                 if k.startswith("total goluri")}
        frame = pd.DataFrame([
            {"linie": k.replace("total goluri ", ""), "peste": v["peste"],
             "sub": v["sub"]} for k, v in lines.items()])
        st.dataframe(
            frame, hide_index=True, width="stretch",
            column_config={c: st.column_config.ProgressColumn(
                format="percent", min_value=0.0, max_value=1.0)
                for c in ("peste", "sub")})
        expected = result["goluri_asteptate_final"]
        st.caption(f"Scor final așteptat: **{expected[0]:.2f} – "
                   f"{expected[1]:.2f}**")
    st.stop()

# ----------------------------------------------------------------------
st.success("**1 meci în desfășurare**" if len(live) == 1
           else f"**{len(live)} meciuri în desfășurare**")

for match in live:
    with st.container(border=True):
        # Statisticile vin din alt feed decat scorul; daca nu raspunde,
        # meciul se arata oricum, doar fara ele.
        try:
            stats = provider.fetch_match_stats(match.external_id)
        except Exception as exc:  # noqa: BLE001
            stats = {}
            st.caption(f"Statisticile nu sunt disponibile acum ({exc}).")

        def total(name: str) -> float | None:
            pair = stats.get(name)
            return None if pair is None else pair[0] + pair[1]

        cartonase, cornere = total("yellow_cards"), total("corners")

        header = st.columns([3, 1, 1, 1, 1])
        header[0].markdown(f"### {match.label}")
        header[1].metric("Scor", match.score)
        header[2].metric("Minut", match.minute if match.minute else "—")
        header[3].metric(
            "Cartonașe", "—" if cartonase is None else f"{cartonase:.0f}",
            delta=None if stats.get("yellow_cards") is None else
            f"{stats['yellow_cards'][0]:.0f} – {stats['yellow_cards'][1]:.0f}",
            delta_color="off", help="Galbene, gazdă – oaspete. Cartonașele "
                                    "roșii se numără separat de piața noastră.")
        header[4].metric(
            "Cornere", "—" if cornere is None else f"{cornere:.0f}",
            delta=None if stats.get("corners") is None else
            f"{stats['corners'][0]:.0f} – {stats['corners'][1]:.0f}",
            delta_color="off")

        minute = match.minute or _estimate_minute(match)
        state = InPlayState(
            match.home, match.away,
            match.home_goals or 0, match.away_goals or 0, minute)

        try:
            result = live_markets(pipeline, state)
        except Exception as exc:  # noqa: BLE001
            st.caption(f"Nu pot recalcula șansele: {exc}")
            continue

        left, right = st.columns(2)
        with left:
            st.markdown("**Șanse acum**")
            probability_bar(result["piete"]["1X2"])
        with right:
            st.markdown("**Ambele marchează / total goluri**")
            probability_bar(result["piete"]["ambele marcheaza"])
            probability_bar(result["piete"]["total goluri 2.5"],
                            "peste/sub 2.5 goluri")

        match_id = db.match_id_by_external("flashscore", match.external_id)
        if match_id:
            rows = ticket_status_live(db, state, match_id,
                                      pipeline, stats=stats)
            if rows:
                st.markdown("**Ponturile tale pe acest meci**")
                icons = {"castigat": "✅ iese acum", "pierdut": "❌ pică acum",
                         "anulat": "➖ nedecidabil"}
                frame = pd.DataFrame(rows)
                frame["la scorul curent"] = frame["la_scorul_curent"].map(
                    icons).fillna(frame["la_scorul_curent"])
                st.dataframe(
                    frame[["bilet", "pontul", "la scorul curent", "sansa_acum"]]
                    .rename(columns={"sansa_acum": "șansă acum"}),
                    hide_index=True, width="stretch",
                    column_config={"șansă acum": st.column_config.NumberColumn(
                        format="percent")})
                st.caption(
                    "„Iese acum” înseamnă că pontul ar fi câștigat dacă meciul "
                    "s-ar termina în clipa asta — nu că e deja decis."
                )
