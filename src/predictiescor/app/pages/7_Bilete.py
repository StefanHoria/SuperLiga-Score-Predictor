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
    bettable, legs_for_tickets, legs_frame, load_calibration,
    matches_in_range, matches_on,
    next_match_days, overall_summary, recent_match_days, settle_tickets,
    strategy_calibration, strategy_performance, surprising_legs,
    surprising_results,
    surprising_stats, tickets_frame, update_strategy_weights,
)
from predictiescor.tickets.watcher import status as watcher_status
from predictiescor.data.odds_input import (
    automatic_odds, coverage as odds_coverage, import_csv,
    line_movement, moved_matches, store_odds,
    upcoming_template,
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
# `st.stop()` oprea intregul script, nu doar tabul asta: cand ziua aleasa
# n-avea niciun meci pe care sa se mai poata paria - azi, dupa startul
# ultimului meci - se goleau si Istoric, Acuratete, Cote si Surprize.
# Sectiunea sta intr-o functie ca sa poata iesi cu `return`.
def _sectiunea_generare() -> None:
    upcoming_days = next_match_days(db, 12)
    past_days = recent_match_days(db, 12)

    col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
    all_days = upcoming_days + [d for d in past_days if d not in upcoming_days]
    if not all_days:
        st.warning("Nu există meciuri în baza de date.")
        st.code("python scripts/update.py", language="bash")
        return

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
        return

    # `upcoming_matches` nu intoarce coloanele de scor (meciurile n-au unul).
    # Le adaugam goale, ca restul paginii sa trateze la fel toate variantele.
    for column in ("home_goals", "away_goals"):
        if column not in matches.columns:
            matches[column] = pd.NA

    # Pentru o zi din viitor, intervalul de ±3 zile prinde si meciuri deja
    # jucate — si chiar ziua de azi poate avea un meci terminat la prânz si
    # unul seara. Nu are rost sa pariem pe cele începute.
    if is_upcoming:
        inainte = len(matches)
        matches = bettable(matches)
        incepute = inainte - len(matches)
    else:
        incepute = 0

    if matches.empty:
        if incepute:
            st.info(
                f"Toate cele {incepute} meciuri din interval au început deja. "
                "Pe un meci în desfășurare nu se mai pariază — alege altă zi "
                "sau treci **Acoperire** pe „Următoarele meciuri programate”.",
                icon="🕐")
        else:
            st.info("Nu sunt meciuri de pariat în intervalul ales.")
        return

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

    # Cu „Etapa" sau „Următoarele meciuri programate", selecția se întinde pe
    # mai multe zile, iar ziua din casetă poate fi una ale cărei meciuri s-au
    # jucat deja. Biletul se pune atunci pe ziua primului meci din el, altfel
    # ar apărea în istoric sub o zi cu care n-are nicio legătură.
    ziua_biletelor = min(str(v)[:10] for v in matches["utc_date"])
    if ziua_biletelor != day:
        st.caption(f"Biletele se vor înregistra pe **{ziua_biletelor}**, ziua "
                   f"primului meci din selecție.")

    if st.button("Generează bilete", type="primary"):
        generator = TicketGenerator(pipeline, db,
                                    calibrator=load_calibration(db), stake=stake)
        tickets = generator.build(matches, count=int(count),
                                  match_day=ziua_biletelor,
                                  strategies=chosen)
        st.session_state["tickets"] = tickets
        st.session_state["tickets_day"] = ziua_biletelor

    tickets = st.session_state.get("tickets", [])
    # Comparatia se face cu `ziua_biletelor`, adica exact ce s-a salvat la
    # generare. Cu `day` (ziua din caseta), o selectie pe mai multe zile
    # nu s-ar potrivi niciodata si biletele generate n-ar mai aparea.
    if tickets and st.session_state.get("tickets_day") == ziua_biletelor:
        st.divider()
        st.subheader(f"{len(tickets)} bilete pentru {ziua_biletelor}")

        # o singură interogare pentru toate biletele
        mutari = moved_matches(db)
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

                # Avertisment când piața s-a mutat mult pe un meci din bilet.
                # Nu schimbă predicția — doar spune că modelul lucrează cu
                # informație mai veche decât casele.
                flagged = [(leg, mutari[leg.match_id]) for leg in ticket.legs
                           if leg.match_id in mutari]
                if flagged:
                    detalii = " · ".join(
                        f"**{leg.match_label}** ({move:.0%})"
                        for leg, move in sorted(flagged, key=lambda x: -x[1]))
                    st.warning(
                        f"⚠️ Piața s-a mutat mult aici: {detalii}. Casele au "
                        f"aflat ceva ce modelul nu știe — de obicei o "
                        f"accidentare sau formația anunțată.")

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


with tab_new:
    _sectiunea_generare()

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
    if all_tickets.empty:
        st.caption("Niciun bilet în istoric.")
    else:
        ICONS = {"castigat": "✅", "pierdut": "❌", "anulat": "➖",
                 "deschis": "⏳"}
        CULORI = {"castigat": "#1a7f37", "pierdut": "#8b2c2c",
                  "deschis": "#8a6d1f", "anulat": "#4a4a4a"}

        f1, f2, f3 = st.columns([2, 2, 1])
        stari = f1.multiselect(
            "Stare", sorted(all_tickets["status"].unique()),
            default=sorted(all_tickets["status"].unique()),
            format_func=lambda v: f"{ICONS.get(v, '')} {v}")
        zile = sorted(all_tickets["match_day"].unique(), reverse=True)
        ziua_aleasa = f2.selectbox("Ziua", ["toate"] + zile)
        cate = f3.number_input("Câte bilete", 5, 60, 12, step=5)

        shown = all_tickets[all_tickets["status"].isin(stari)]
        if ziua_aleasa != "toate":
            shown = shown[shown["match_day"] == ziua_aleasa]
        shown = shown.head(int(cate))

        if shown.empty:
            st.info("Niciun bilet cu filtrele alese.")
        else:
            # toate ponturile dintr-o singura interogare, nu una per bilet
            toate_ponturile = legs_for_tickets(db, shown["ticket_id"].tolist())
            pe_bilet = dict(tuple(toate_ponturile.groupby("ticket_id")))                 if not toate_ponturile.empty else {}

            st.caption(f"**{len(shown)}** bilete. Fiecare card arată tot: "
                       f"ponturile, scorurile și unde s-a rupt.")

            for grupa_zi, bilete_zi in shown.groupby("match_day", sort=False):
                castigate = int((bilete_zi["status"] == "castigat").sum())
                decise = int((bilete_zi["status"] != "deschis").sum())
                profit_zi = float(bilete_zi["profit"].fillna(0).sum())
                st.markdown(
                    f"#### {grupa_zi} · {castigate}/{decise} câștigate · "
                    f"{profit_zi:+.0f} lei")

                for rand in bilete_zi.itertuples():
                    stare = str(rand.status)
                    culoare = CULORI.get(stare, "#4a4a4a")
                    ponturi = pe_bilet.get(rand.ticket_id)

                    with st.container(border=True):
                        # Titlul pe rândul lui: în coloane, etichetele lungi
                        # („Bilet cu cotă mare") se rupeau pe două rânduri.
                        profit = (0.0 if pd.isna(rand.profit)
                                  else float(rand.profit))
                        bani = (f"<b style='color:{culoare}'>{profit:+.2f} "
                                f"lei</b>" if stare != "deschis"
                                else f"miză {rand.stake:.0f} lei")
                        st.markdown(
                            f"<div style='display:flex;align-items:center;"
                            f"gap:10px;flex-wrap:wrap'>"
                            f"<span style='background:{culoare};color:#fff;"
                            f"padding:2px 10px;border-radius:10px;"
                            f"font-size:.85em;white-space:nowrap'>"
                            f"{ICONS.get(stare, '')} {stare}</span>"
                            f"<b style='font-size:1.05em'>{rand.label}</b>"
                            f"<span style='margin-left:auto;opacity:.8;"
                            f"white-space:nowrap'>cotă <b>"
                            f"{rand.total_odds:.2f}</b> &nbsp;·&nbsp; șansă <b>"
                            f"{rand.probability:.0%}</b> &nbsp;·&nbsp; {bani}"
                            f"</span></div>",
                            unsafe_allow_html=True)

                        iesite = int(rand.legs_won or 0)
                        total = int(rand.legs_total or 0)
                        if total:
                            st.progress(iesite / total,
                                        text=f"{iesite} din {total} ponturi ieșite")

                        if ponturi is not None and not ponturi.empty:
                            for pont in ponturi.itertuples():
                                scor = ("—" if pd.isna(pont.home_goals)
                                        else f"{int(pont.home_goals)}-"
                                             f"{int(pont.away_goals)}")
                                semn = ICONS.get(str(pont.result), "⏳")
                                meci = f"{pont.home_team} — {pont.away_team}"
                                detaliu = (f" · *{pont.actual_value}*"
                                           if pont.actual_value else "")
                                st.markdown(
                                    f"{semn} &nbsp;{pont.description}"
                                    f" &nbsp;·&nbsp; <span style='opacity:.65'>"
                                    f"{meci} &nbsp;{scor}{detaliu}</span>",
                                    unsafe_allow_html=True)

        with st.expander("Vezi ca tabel"):
            st.dataframe(
                shown[["ticket_id", "match_day", "label", "total_odds",
                       "probability", "stake", "status", "legs_won",
                       "legs_total", "payout", "profit"]].rename(columns={
                    "ticket_id": "id", "match_day": "ziua", "label": "bilet",
                    "total_odds": "cotă", "probability": "șansă",
                    "stake": "miză", "status": "stare",
                    "legs_won": "ponturi ieșite", "legs_total": "ponturi",
                    "payout": "încasat"}),
                hide_index=True, width="stretch", height=380,
                column_config={
                    "șansă": st.column_config.ProgressColumn(
                        format="percent", min_value=0.0, max_value=1.0),
                    "cotă": st.column_config.NumberColumn(format="%.2f"),
                    "profit": st.column_config.NumberColumn(format="%.2f")})

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
    st.subheader("Își cunoaște fiecare strategie șansele?")
    calib = strategy_calibration(db)
    if calib.empty:
        st.caption("Încă nu sunt bilete decontate.")
    else:
        show = calib[["label", "bilete", "prezis", "real", "castigate",
                      "așteptate", "neobișnuit"]].rename(
            columns={"label": "strategie", "castigate": "câștigate"})
        st.dataframe(
            show, hide_index=True, width="stretch",
            column_config={
                "prezis": st.column_config.NumberColumn(format="percent"),
                "real": st.column_config.NumberColumn(format="percent"),
                "neobișnuit": st.column_config.ProgressColumn(
                    "cât de neobișnuit", format="percent",
                    min_value=0.0, max_value=1.0)})
        st.caption(
            "**Ultima coloană e cea care contează.** Spune cât de des ar ieși "
            "un rezultat cel puțin la fel de ciudat, dacă modelul ar avea "
            "dreptate. Valori mari = normal. Sub ~5% = strategia chiar nu "
            "își cunoaște șansele."
        )
        neobisnuite = calib[calib["neobișnuit"] < 0.05]
        if neobisnuite.empty:
            st.info(
                "Nicio strategie nu deviază neobișnuit. „Bilet cu cotă mare” "
                "iese 0% — dar la o șansă prezisă de 14% pe bilet, să nu iasă "
                "niciunul din 16 e ghinion obișnuit, nu defect.", icon="✅")
        else:
            st.warning(
                "Deviază neobișnuit: "
                + ", ".join(f"**{r.label}** ({r.real:.0%} față de "
                            f"{r.prezis:.0%} prezis)"
                            for r in neobisnuite.itertuples()))

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
        "Cotele se aduc **automat de la Flashscore**, de la cinci case din "
        "România, pentru meciurile din următoarele zece zile. Introducerea "
        "manuală rămâne pentru casele pe care Flashscore nu le listează."
    )

    cov = odds_coverage(db)
    have = automatic_odds(db)
    cols = st.columns(4)
    cols[0].metric("Meciuri viitoare", cov["meciuri_viitoare"])
    cols[1].metric("Cu cote", cov["cu_cote"])
    cols[2].metric("Case listate", have["case"])
    if have["marja"] is not None:
        cols[3].metric("Marja medie", f"{have['marja']:.1%}",
                       help="Cât își ia casa. Sub 5% e o piață strânsă.")

    st.caption(
        f"Cele **{cov['meciuri_viitoare']}** de meciuri viitoare includ tot "
        f"calendarul sezonului; casele publică cote abia cu câteva zile "
        f"înainte, deci acoperirea de **{cov['cu_cote']}** e normală, nu o "
        f"lipsă."
    )

    if st.button("Adu cotele acum", type="primary", key="fetch_odds"):
        from predictiescor.data.flashscore import FlashscoreProvider
        with st.status("Interoghez Flashscore…", expanded=True) as status:
            provider = FlashscoreProvider(db=db)
            if not provider.odds_available:
                status.update(label="Sursa e oprită din .env "
                                    "(FLASHSCORE_ODDS)", state="error")
            else:
                try:
                    result = provider.fetch_odds()
                    if result.errors:
                        status.update(label=result.errors[0], state="error")
                    else:
                        st.write(f"{result.matches} meciuri, "
                                 f"{result.odds} cote scrise.")
                        status.update(label="Gata", state="complete")
                        st.cache_data.clear()
                except Exception as exc:  # noqa: BLE001
                    status.update(label=f"A eșuat: {exc}", state="error")
    st.caption("Durează câteva minute: fiecare meci costă `case × piețe` "
               "cereri, iar între ele se așteaptă. Rulează oricum zilnic, "
               "la actualizarea completă.")

    if not have["pe_casa"].empty:
        st.subheader("Ce avem acum, pe casă")
        st.dataframe(have["pe_casa"], hide_index=True, width="stretch")
        st.caption(
            "`fs_avg` și `fs_max` nu sunt case, ci media și cel mai bun preț "
            "peste toate — cel mai bun preț e ce ai obține alegând de fiecare "
            "dată casa cea mai generoasă."
        )

    st.divider()
    st.subheader("Unde s-a mutat piața")
    st.caption(
        "Singurul semnal din aplicație care vine din informație pe care "
        "modelul **nu** o are. Când o casă mută cota mult de la deschidere, a "
        "aflat ceva — de obicei o accidentare sau formația anunțată. Mutarea e "
        "măsurată în șansă implicită, nu în cotă: de la 1.20 la 1.30 contează "
        "mult mai mult decât de la 6.00 la 6.10."
    )
    prag = st.slider("Mutare minimă (puncte de șansă)", 2, 20, 5, step=1,
                     format="%d%%", key="prag_miscare")
    moved = line_movement(db, min_move=prag / 100.0)
    if moved.empty:
        st.info("Nicio mutare peste prag. Coboară pragul sau adu cotele.")
    else:
        show = moved.copy()
        show["ora"] = local_series(show["utc_date"], "%d.%m %H:%M")
        show["mutare"] = show["mutare"].abs()
        st.dataframe(
            show[["ora", "meci", "bookmaker", "market", "selection",
                  "opening", "price", "mutare", "sens"]]
            .rename(columns={"bookmaker": "casă", "market": "piață",
                             "selection": "selecție", "opening": "deschidere",
                             "price": "acum"}),
            hide_index=True, width="stretch", height=340,
            column_config={
                "deschidere": st.column_config.NumberColumn(format="%.2f"),
                "acum": st.column_config.NumberColumn(format="%.2f"),
                "mutare": st.column_config.ProgressColumn(
                    format="percent", min_value=0.0, max_value=0.25)})

    st.divider()
    st.subheader("Completează manual (opțional)")
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
        "**Cât de sigură e sursa automată?** Cotele vin dintr-un endpoint "
        "nedocumentat al Flashscore. Merge azi și e verificat pe date reale, "
        "dar se poate opri oricând, fără preaviz — codul semnalează explicit "
        "când formatul se schimbă, în loc să scrie tăcut zero rânduri. "
        "Introducerea manuală rămâne aici tocmai ca plasă de siguranță, și "
        "pentru casele care nu apar la Flashscore.",
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
