"""Bilete de pariuri: generare, istoric, acuratețe și surprize."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import plotly.express as px
import streamlit as st

from common import (  # noqa: F401
    local_series, local_time,
    data_version, load_matches, load_team_stats, refresh_button,
    require_pipeline, setup_page, sidebar_footer,
)
from predictiescor.storage.db import Database
from predictiescor.tickets import (
    STRATEGIES, TicketGenerator, accuracy_report, learn_calibration,
    legs_frame, load_calibration, matches_in_range, matches_on,
    next_match_days, overall_summary, recent_match_days, settle_tickets,
    strategy_performance, surprising_legs, surprising_results,
    surprising_stats, tickets_frame, update_strategy_weights,
)
from predictiescor.tickets.watcher import status as watcher_status
from predictiescor.data.odds_input import (
    coverage as odds_coverage, import_csv, store_odds, upcoming_template,
)

setup_page("Bilete", "🎫")
version = data_version()
refresh_button()

pipeline = require_pipeline()
sidebar_footer(pipeline)
db = Database()

st.title("🎫 Bilete")

tab_new, tab_history, tab_accuracy, tab_odds, tab_surprises = st.tabs(
    ["Bilete noi", "Istoric și verificare", "Acuratețe și învățare",
     "Cotele casei", "Surprize"])

# ======================================================================
# 1. Generare
# ======================================================================
with tab_new:
    upcoming_days = next_match_days(db, 12)
    past_days = recent_match_days(db, 12)

    col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
    all_days = upcoming_days + [d for d in past_days if d not in upcoming_days]
    if not all_days:
        st.warning("Nu există meciuri în baza de date.")
        st.code("python scripts/update.py", language="bash")
        st.stop()

    day = col1.selectbox(
        "Ziua meciurilor", all_days,
        format_func=lambda d: f"{d}" + (" (urmează)" if d in upcoming_days
                                        else " (jucat)"))
    count = col2.number_input("Câte bilete", 5, 10, 8)
    stake = col3.number_input("Miză / bilet (lei)", 1.0, 1000.0, 10.0, step=5.0)
    scope = col4.selectbox(
        "Acoperire",
        ["Doar ziua", "Etapa (±3 zile)", "Următoarele meciuri programate"],
        help="O zi de SuperLiga are adesea 1-3 meciuri. Cu unul singur, "
             "singurul bilet posibil e cel combinat.")

    is_upcoming = day in upcoming_days
    if scope == "Doar ziua":
        matches = matches_on(db, day)
    elif scope == "Etapa (±3 zile)":
        start = (date.fromisoformat(day) - timedelta(days=3)).isoformat()
        end = (date.fromisoformat(day) + timedelta(days=3)).isoformat()
        matches = matches_in_range(db, start, end)
    else:
        matches = db.upcoming_matches(limit=10)

    if matches.empty:
        st.info("Nu sunt meciuri de pariat în intervalul ales.")
        st.stop()

    # `upcoming_matches` nu intoarce coloanele de scor (meciurile n-au unul).
    # Le adaugam goale, ca restul paginii sa trateze la fel toate variantele.
    for column in ("home_goals", "away_goals"):
        if column not in matches.columns:
            matches[column] = pd.NA

    # Pentru o zi din viitor, intervalul de ±3 zile prinde si meciuri deja
    # jucate. Nu are rost sa pariem pe ele.
    if is_upcoming:
        matches = matches[matches["home_goals"].isna()].reset_index(drop=True)

    if matches.empty:
        st.info("Nu sunt meciuri de pariat în intervalul ales.")
        st.stop()

    st.caption(f"**{len(matches)} meciuri** în selecție:")
    preview = matches.copy()
    preview["ora"] = local_series(preview["utc_date"], "%H:%M")
    preview["utc_date"] = local_series(preview["utc_date"], "%d.%m.%Y")
    preview["meci"] = preview["home_team"] + " — " + preview["away_team"]
    preview["scor"] = [
        f"{int(h)}-{int(a)}" if pd.notna(h) and pd.notna(a) else "—"
        for h, a in zip(preview["home_goals"], preview["away_goals"])
    ]
    columns = [c for c in ("utc_date", "ora", "meci", "scor", "status")
               if c in preview.columns]
    st.dataframe(preview[columns].rename(columns={"utc_date": "data"}),
                 hide_index=True, width="stretch")

    if len(matches) < 2:
        st.warning(
            "**Un singur meci în selecție.** Toate strategiile în afară de "
            "*Bilet combinat* cer cel puțin două ponturi din meciuri "
            "**diferite** — altfel înmulțirea cotelor ar minți despre șansa "
            "biletului. Ca să vezi și restul strategiilor, schimbă "
            "**Acoperire** pe „Următoarele meciuri programate”.",
            icon="ℹ️")

    chosen = st.multiselect(
        "Strategii", [s.name for s in STRATEGIES],
        default=[s.name for s in STRATEGIES],
        format_func=lambda n: next(s.label for s in STRATEGIES if s.name == n))

    with st.expander("Ce înseamnă fiecare strategie"):
        for strategy in STRATEGIES:
            st.markdown(f"**{strategy.label}** — {strategy.description}")

    if st.button("Generează bilete", type="primary"):
        generator = TicketGenerator(pipeline, db,
                                    calibrator=load_calibration(db), stake=stake)
        tickets = generator.build(matches, count=int(count), match_day=day,
                                  strategies=chosen)
        st.session_state["tickets"] = tickets
        st.session_state["tickets_day"] = day

    tickets = st.session_state.get("tickets", [])
    if tickets and st.session_state.get("tickets_day") == day:
        st.divider()
        st.subheader(f"{len(tickets)} bilete pentru {day}")

        for i, ticket in enumerate(tickets, 1):
            with st.container(border=True):
                head = st.columns([3, 1, 1, 1, 1])
                head[0].markdown(f"### {i}. {ticket.label}")
                head[1].metric("Cotă totală", f"{ticket.total_odds:.2f}")
                head[2].metric("Șansă", f"{ticket.probability:.1%}")
                head[3].metric("Miză", f"{ticket.stake:.0f} lei")
                head[4].metric("Câștig potențial",
                               f"{ticket.potential_payout:.2f} lei")

                frame = ticket.to_frame()
                st.dataframe(
                    frame, hide_index=True, width="stretch",
                    column_config={
                        "șansă": st.column_config.ProgressColumn(
                            format="percent", min_value=0.0, max_value=1.0),
                        "cotă": st.column_config.NumberColumn(format="%.2f"),
                    })

        if st.button("Salvează biletele în baza de date"):
            generator = TicketGenerator(pipeline, db, stake=stake)
            ids = generator.save(tickets)
            st.success(f"{len(ids)} bilete salvate. Se vor verifica automat "
                       f"după ce se joacă meciurile.")

        export = pd.concat(
            [t.to_frame().assign(bilet=i, strategie=t.label,
                                 cota_totala=round(t.total_odds, 2))
             for i, t in enumerate(tickets, 1)], ignore_index=True)
        st.download_button("Descarcă biletele (CSV)",
                           export.to_csv(index=False).encode("utf-8"),
                           file_name=f"bilete_{day}.csv", mime="text/csv")

        st.info(
            "**Cotele afișate sunt „cote corecte” — 1 împărțit la șansă.** "
            "Casele de pariuri scad o marjă din ele, deci cota reală va fi mai "
            "mică. Un bilet e interesant doar dacă cota oferită de casă e mai "
            "mare decât cea de aici.", icon="ℹ️")

# ======================================================================
# 2. Istoric si verificare
# ======================================================================
with tab_history:
    c1, c2 = st.columns([1, 3])
    if c1.button("Verifică biletele acum", type="primary"):
        outcome = settle_tickets(db)
        st.success(f"Decontate: {outcome['decontate']} · "
                   f"în așteptare: {outcome['in_asteptare']}")
    c2.caption(
        "Un bilet se închide de îndată ce **un pont a ratat** — nu mai are rost "
        "să aștepte restul meciurilor. Cât timp toate ponturile decise sunt "
        "câștigate, biletul rămâne deschis, iar coloana *ponturi ieșite* arată "
        "pe unde e."
    )

    # ---- verificare automată ----
    watch = watcher_status(db)
    with st.container(border=True):
        st.markdown("#### Verificare automată")
        cols = st.columns([1, 1, 2])
        cols[0].metric("Bilete deschise", watch["bilete_deschise"])
        cols[1].metric("De verificat acum", len(watch["de_verificat"]))

        last = watch["ultima_verificare"]
        upcoming = watch["urmatoarea_verificare"]
        if upcoming is not None:
            hours = (upcoming - datetime.now(timezone.utc)).total_seconds() / 3600
            when = (f"peste {hours:.1f} ore" if hours > 0
                    else "acum (meciul s-a încheiat)")
            cols[2].markdown(
                f"**Următoarea verificare utilă:** "
                f"{local_time(upcoming, '%d.%m %H:%M')} — {when}")
        else:
            cols[2].markdown("**Nu sunt meciuri în așteptare.**")
        cols[2].caption(
            f"Ultima verificare: {local_time(last) or 'niciodată'}")

        if watch["de_verificat"]:
            st.dataframe(
                pd.DataFrame(watch["de_verificat"])
                .assign(kickoff=lambda f: local_series(f["kickoff"]))[
                    ["kickoff", "label", "has_result", "open_legs"]
                ].rename(columns={
                    "kickoff": "început", "label": "meci",
                    "has_result": "are rezultat",
                    "open_legs": "ponturi nedecontate"}),
                hide_index=True, width="stretch")

        st.markdown(
            "Ca biletele să fie verificate **singure, după fiecare meci**, "
            "chiar și cu aplicația închisă, înregistrează o sarcină Windows:")
        st.code("python scripts/watch.py --install", language="bash")
        st.caption(
            "Rulează din 15 în 15 minute, dar nu face nicio cerere de rețea "
            "decât dacă un meci chiar s-a încheiat între timp. "
            "O oprești cu `python scripts/watch.py --uninstall`, "
            "iar starea o vezi cu `python scripts/watch.py --status`."
        )

    summary = overall_summary(db)
    if summary["bilete"] == 0:
        st.info("Niciun bilet decontat încă. Generează bilete, apoi verifică-le "
                "după ce se joacă meciurile.")
        st.code("python scripts/tickets.py --backfill 20", language="bash")
        st.caption("Comanda de mai sus construiește un istoric onest pe "
                   "meciurile trecute, antrenând modelul doar pe date "
                   "anterioare fiecărei zile.")
    else:
        cols = st.columns(5)
        cols[0].metric("Bilete decontate", summary["bilete"])
        cols[1].metric("Câștigate", summary["castigate"])
        cols[2].metric("Rată de reușită", f"{summary['rata %']:.1f}%")
        cols[3].metric("Profit", f"{summary['profit']:+.2f} lei")
        cols[4].metric("ROI", f"{summary['ROI %']:+.1f}%")

    st.divider()
    all_tickets = tickets_frame(db, limit=300)
    if not all_tickets.empty:
        status_filter = st.multiselect(
            "Stare", sorted(all_tickets["status"].unique()),
            default=sorted(all_tickets["status"].unique()))
        shown = all_tickets[all_tickets["status"].isin(status_filter)]

        display = shown[["ticket_id", "match_day", "label", "total_odds",
                         "probability", "stake", "status", "legs_won",
                         "legs_total", "payout", "profit"]].rename(columns={
            "ticket_id": "id", "match_day": "ziua", "label": "bilet",
            "total_odds": "cotă", "probability": "șansă", "stake": "miză",
            "status": "stare", "legs_won": "ponturi ieșite",
            "legs_total": "ponturi", "payout": "încasat"})
        st.dataframe(display, hide_index=True, width="stretch", height=380,
                     column_config={
                         "șansă": st.column_config.ProgressColumn(
                             format="percent", min_value=0.0, max_value=1.0),
                         "cotă": st.column_config.NumberColumn(format="%.2f"),
                         "profit": st.column_config.NumberColumn(format="%.2f"),
                     })

        chosen_id = st.selectbox("Vezi detaliile biletului",
                                 shown["ticket_id"].tolist())
        if chosen_id:
            legs = legs_frame(db, int(chosen_id))
            legs["meci"] = legs["home_team"] + " — " + legs["away_team"]
            legs["scor"] = legs.apply(
                lambda r: (f"{int(r.home_goals)}-{int(r.away_goals)}"
                           if pd.notna(r.home_goals) else "—"), axis=1)
            icons = {"castigat": "✅", "pierdut": "❌", "anulat": "➖",
                     "deschis": "⏳"}
            legs["✓"] = legs["result"].map(icons).fillna("⏳")
            st.dataframe(
                legs[["✓", "meci", "scor", "market", "description",
                      "probability", "fair_odds", "result", "actual_value"]]
                .rename(columns={
                    "market": "piață", "description": "pontul",
                    "probability": "șansă", "fair_odds": "cotă",
                    "result": "rezultat", "actual_value": "ce s-a întâmplat"}),
                hide_index=True, width="stretch",
                column_config={
                    "șansă": st.column_config.ProgressColumn(
                        format="percent", min_value=0.0, max_value=1.0),
                    "cotă": st.column_config.NumberColumn(format="%.2f")})

# ======================================================================
# 3. Acuratete si invatare
# ======================================================================
with tab_accuracy:
    if st.button("Reînvață din rezultate", type="primary"):
        calibrator = learn_calibration(db)
        weights = update_strategy_weights(db)
        st.success(f"Calibrare actualizată pe {len(calibrator.table)} "
                   f"combinații piață × interval; "
                   f"{len(weights)} strategii recântărite.")

    accuracy = accuracy_report(db)
    if not accuracy:
        st.info("Nu există încă bilete decontate din care să se învețe.")
    else:
        if "pe_piata" in accuracy:
            st.subheader("Cât de bine nimerește modelul, pe fiecare piață")
            frame = accuracy["pe_piata"].rename(columns={
                "market": "piață", "picioare": "picioare",
                "castigate": "câștigate", "prezis": "șansa dată",
                "observat": "rata reală", "diferență": "diferență"})
            st.dataframe(
                frame, hide_index=True, width="stretch",
                column_config={
                    "șansa dată": st.column_config.NumberColumn(format="percent"),
                    "rata reală": st.column_config.NumberColumn(format="percent"),
                    "diferență": st.column_config.NumberColumn(format="percent"),
                    "Brier": st.column_config.NumberColumn(format="%.3f")})
            st.caption(
                "**Diferență** pozitivă = modelul e prea prudent pe acea piață; "
                "negativă = e prea încrezător. Corecția se aplică automat la "
                "biletele următoare."
            )

            fig = px.scatter(
                accuracy["pe_piata"], x="prezis", y="observat", size="picioare",
                text="market", height=420)
            fig.add_shape(type="line", x0=0, y0=0, x1=1, y1=1,
                          line=dict(dash="dot"))
            fig.update_traces(textposition="top center")
            fig.update_layout(margin=dict(l=0, r=0, t=10, b=0),
                              xaxis_title="șansa dată de model",
                              yaxis_title="rata reală")
            st.plotly_chart(fig, width="stretch")

        if "pe_zi" in accuracy:
            st.subheader("Profit în timp")
            frame = accuracy["pe_zi"]
            fig = px.line(frame, x="match_day", y="profit cumulat", markers=True,
                          height=360)
            fig.add_hline(y=0, line_dash="dot")
            fig.update_layout(margin=dict(l=0, r=0, t=10, b=0),
                              xaxis_title="", yaxis_title="profit cumulat (lei)")
            st.plotly_chart(fig, width="stretch")

        if "anulate" in accuracy:
            st.subheader("Picioare anulate")
            st.dataframe(accuracy["anulate"], hide_index=True, width="stretch")
            st.caption(
                "Anulate = nu am avut datele necesare ca să le verificăm "
                "(de obicei cornere/cartonașe pentru meciuri fără statistici "
                "detaliate). Nu contează la acuratețe."
            )

    st.divider()
    st.subheader("Cum se descurcă fiecare strategie")
    performance = strategy_performance(db)
    if performance.empty:
        st.caption("Încă nu sunt date.")
    else:
        display = performance[["strategy", "tickets", "won", "rată bilete %",
                               "rată picioare %", "ROI %", "weight"]].rename(
            columns={"strategy": "strategie", "tickets": "bilete",
                     "won": "câștigate", "weight": "greutate"})
        st.dataframe(display, hide_index=True, width="stretch",
                     column_config={
                         # aceste coloane sunt deja in procente (0-100),
                         # deci NU folosim presetul "percent"
                         "ROI %": st.column_config.NumberColumn(format="%+.1f"),
                         "rată bilete %": st.column_config.NumberColumn(
                             format="%.1f"),
                         "rată picioare %": st.column_config.NumberColumn(
                             format="%.1f"),
                         "greutate": st.column_config.ProgressColumn(
                             format="%.2f", min_value=0.0, max_value=2.0)})
        st.caption(
            "**Greutatea** decide cât de des e propusă strategia: peste 1 = "
            "merge bine și apare mai des, sub 1 = pierde și apare mai rar. "
            "Se schimbă doar după cel puțin 8 bilete, ca norocul de moment să "
            "nu răstoarne concluzia."
        )

    calibration = load_calibration(db).as_frame()
    if not calibration.empty:
        with st.expander("Tabelul de calibrare învățat"):
            st.dataframe(calibration, hide_index=True, width="stretch",
                         column_config={
                             "prezis": st.column_config.NumberColumn(
                                 format="percent"),
                             "observat": st.column_config.NumberColumn(
                                 format="percent"),
                             "corecție": st.column_config.NumberColumn(
                                 format="%.3f")})

# ======================================================================
# 4. Cotele casei
# ======================================================================
with tab_odds:
    st.markdown(
        "Fără cotele casei, modelul nu are cu ce să compare, iar strategia "
        "**Bilet de valoare** nu produce nimic. Cotele istorice le avem "
        "automat, dar doar pentru meciuri deja jucate — pentru cele care "
        "urmează, le introduci tu."
    )

    cov = odds_coverage(db)
    cols = st.columns(3)
    cols[0].metric("Meciuri viitoare", cov["meciuri_viitoare"])
    cols[1].metric("Cu cote introduse", cov["cu_cote"])
    cols[2].metric("Acoperire",
                   f"{100 * cov['cu_cote'] / max(1, cov['meciuri_viitoare']):.0f}%")

    st.divider()
    st.subheader("Completează direct în tabel")
    bookmaker = st.text_input("Casa de pariuri", value="manual",
                              help="Ține minte de unde vin cotele, ca să poți "
                                   "compara mai târziu între case.")
    template = upcoming_template(db, limit=20)
    edited = st.data_editor(
        template, hide_index=True, width="stretch", num_rows="fixed",
        disabled=["data", "gazda", "oaspete"],
        column_config={
            c: st.column_config.NumberColumn(c, min_value=1.01, max_value=100.0,
                                             step=0.01, format="%.2f")
            for c in ("1", "X", "2", "peste_2.5", "sub_2.5")},
        key="odds_editor")

    if st.button("Salvează cotele", type="primary"):
        outcome = store_odds(db, edited.to_dict("records"),
                             bookmaker=bookmaker or "manual")
        st.success(f"{outcome['cote']} cote salvate pentru "
                   f"{outcome['meciuri']} meciuri.")
        if outcome["nepotrivite"]:
            st.warning("Nu am găsit meciurile: "
                       + ", ".join(outcome["nepotrivite"][:5]))
        st.cache_data.clear()

    st.divider()
    st.subheader("Sau importă un CSV")
    st.caption("Antet: `gazda,oaspete,1,X,2` — opțional și `data`, "
               "`peste_2.5`, `sub_2.5`.")
    uploaded = st.file_uploader("Fișier CSV", type=["csv"])
    if uploaded is not None and st.button("Importă"):
        outcome = import_csv(db, uploaded.getvalue(),
                             bookmaker=bookmaker or "manual")
        st.success(f"{outcome['cote']} cote importate pentru "
                   f"{outcome['meciuri']} meciuri.")
        if outcome["nepotrivite"]:
            st.warning("Nepotrivite: " + ", ".join(outcome["nepotrivite"][:8]))
        st.cache_data.clear()

    st.info(
        "**De ce nu se descarcă automat?** Casele de pariuri își randează "
        "cotele din JavaScript și le schimbă constant; raclarea lor ar fi și "
        "fragilă, și într-o zonă incertă din punct de vedere al termenilor de "
        "utilizare. Introducerea manuală merge cu orice casă și nu se strică.",
        icon="ℹ️")

# ======================================================================
# 5. Surprize
# ======================================================================
with tab_surprises:
    st.markdown(
        "Surpriza se măsoară în **biți**: cât de improbabil era, după model, "
        "ce s-a întâmplat. 1 bit = șansă de 50%, 2 biți = 25%, 3.3 biți = 10%."
    )

    sub_results, sub_stats, sub_legs = st.tabs(
        ["Rezultate neașteptate", "Statistici neașteptate", "Ponturi ratate"])

    with sub_results:
        matches = load_matches(version)
        season = st.selectbox(
            "Sezon", sorted(matches["season"].unique(), reverse=True),
            key="surprise_season")
        subset = matches[matches["season"] == season]
        with st.spinner("Caut rezultatele care contrazic modelul..."):
            frame = surprising_results(pipeline, subset, limit=30)
        if frame.empty:
            st.info("Nu am putut calcula surprizele.")
        else:
            st.dataframe(
                frame, hide_index=True, width="stretch", height=560,
                column_config={
                    "șansa dată": st.column_config.NumberColumn(format="percent"),
                    "șansa scorului": st.column_config.NumberColumn(
                        format="percent")})
            st.caption(
                "Notă: modelul folosit aici a fost antrenat pe tot istoricul, "
                "deci a „văzut” și aceste meciuri. Lista e utilă ca să scoată "
                "anomaliile la suprafață, nu ca măsură de performanță — pentru "
                "asta există pagina *Performanța modelului*."
            )

    with sub_stats:
        team_stats = load_team_stats(version)
        if team_stats.empty or pipeline.secondary is None:
            st.info("Nu există statistici detaliate pe meci.")
            st.code("python scripts/fetch_data.py --stats-only", language="bash")
        else:
            threshold = st.slider("Abatere minimă (σ)", 1.0, 4.0, 2.0, step=0.25)
            frame = surprising_stats(pipeline, team_stats, limit=40,
                                     min_z=threshold)
            if frame.empty:
                st.info("Nicio statistică peste pragul ales.")
            else:
                st.dataframe(frame, hide_index=True, width="stretch", height=560)

    with sub_legs:
        frame = surprising_legs(db, limit=40)
        if frame.empty:
            st.info("Niciun pont decontat încă.")
        else:
            st.dataframe(
                frame, hide_index=True, width="stretch", height=560,
                column_config={"șansa dată": st.column_config.NumberColumn(
                    format="percent")})
