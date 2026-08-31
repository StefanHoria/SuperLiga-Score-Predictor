"""Cat de bun e modelul, testat cronologic pe meciuri pe care nu le-a vazut."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from common import (
    data_version, load_matches, refresh_button, setup_page, sidebar_footer,
)
from predictiescor.config import get_config
from predictiescor.modeling.dixon_coles import DixonColesModel
from predictiescor.modeling.disagreement import (
    bootstrap_roi, disagreement_table, edge_sweep, favourite_duel, verdict,
)
from predictiescor.modeling.evaluate import (
    roi_summary, value_bets, walk_forward_backtest,
)
from predictiescor.modeling.tracking import scoreboard
from predictiescor.storage.db import Database

setup_page("Performanța modelului", "🎯")
version = data_version()
refresh_button()
sidebar_footer()

cfg = get_config()
st.title("🎯 Performanța modelului")
st.caption(
    "Backtest cronologic: pentru fiecare meci testat, modelul este reantrenat "
    "folosind doar meciurile jucate înaintea lui. Nicio informație din viitor "
    "nu ajunge în model."
)

matches = load_matches(version)

# ----------------------------------------------------------------------
# Jurnalul predictiilor scrise INAINTE de meci
# ----------------------------------------------------------------------
st.subheader("Predicții înregistrate înainte de meci")
st.caption(
    "Backtestul de mai jos reface istoria cu un model reantrenat — corect, dar "
    "tot o simulare. Aici e altceva: ce a spus modelul **înainte** de fluierul "
    "de start, scris în jurnal și necorectabil ulterior."
)

board = scoreboard(Database())
if board.get("inregistrate", 0) == 0:
    st.info("Niciun meci înregistrat încă.")
    st.code("python scripts/update.py --quick", language="bash")
elif board.get("evaluate", 0) == 0:
    st.info(f"{board['inregistrate']} meciuri înregistrate, "
            f"niciunul jucat încă. Revino după etapă.")
else:
    cols = st.columns(5)
    cols[0].metric("Meciuri prezise", board["inregistrate"])
    cols[1].metric("Deja jucate", board["evaluate"])
    if "RPS" in board:
        cols[2].metric("RPS", f"{board['RPS']:.4f}")
        cols[3].metric("Log loss", f"{board['log loss']:.4f}")
        cols[4].metric("Acuratețe", f"{board['acuratete']:.1%}")

    detalii = board.get("detalii")
    if detalii is not None and not detalii.empty:
        with st.expander("Meci cu meci"):
            st.dataframe(
                detalii.rename(columns={
                    "p_rezultat": "șansa dată rezultatului",
                    "surpriza": "surpriză (biți)"}),
                hide_index=True, width="stretch")

st.divider()

if len(matches) < 200:
    st.warning(f"Prea puține meciuri pentru un backtest util ({len(matches)}).")
    st.stop()

with st.sidebar:
    st.subheader("Parametri backtest")
    min_train = st.slider("Meciuri de antrenare inițiale", 100, 600,
                          int(cfg.backtest["min_train_matches"]), step=20)
    refit_every = st.slider("Reantrenare la fiecare N meciuri", 1, 50,
                            int(cfg.backtest["refit_every"]), step=1)
    half_life = st.slider("Timp de înjumătățire (zile)", 60, 720,
                          int(cfg.model["dixon_coles"]["half_life_days"]), step=30)
    shrinkage = st.slider("Meciuri fictive (shrinkage)", 0, 60,
                          int(cfg.model["dixon_coles"].get(
                              "shrinkage_matches", 20)), step=2)
    spread = st.slider(
        "Avantaj de teren: cât de mult pot devia echipele", 5, 200,
        int(cfg.model["dixon_coles"].get("home_advantage_spread", 30)), step=5,
        help="Valori mari = un singur avantaj comun pentru toată liga. "
             "Valori mici = fiecare echipă îl are pe al ei, dar cu mai mult "
             "zgomot.")
    # valoarea din config, ca backtestul sa masoare modelul care chiar face
    # predictiile - nu unul fara Elo, care iese mai slab degeaba
    elo_weight = st.slider(
        "Cât cântărește Elo în amestec", 0.0, 0.5,
        float(cfg.model.get("elo_ensemble_weight", 0.0)), step=0.05,
        help="0 = doar Dixon-Coles. Amestecul e logaritmic, adică media "
             "geometrică ponderată a celor două opinii. Valoarea implicită e "
             "cea folosită de model în producție.")

run = st.button("Rulează backtest", type="primary")

if not run and "backtest" not in st.session_state:
    st.info("Apasă **Rulează backtest** ca să evaluezi modelul. "
            "Durează câteva zeci de secunde.")
    st.stop()

if run:
    def factory() -> DixonColesModel:
        return DixonColesModel(
            half_life_days=half_life,
            max_goals=int(cfg.model["dixon_coles"]["max_goals"]),
            l2_penalty=float(cfg.model["dixon_coles"]["l2_penalty"]),
            min_matches=int(cfg.model["dixon_coles"]["min_matches"]),
            shrinkage_matches=shrinkage,
            home_advantage_spread=spread,
        )

    bar = st.progress(0.0, text="Rulez backtest...")
    result = walk_forward_backtest(
        matches, model_factory=factory, min_train_matches=min_train,
        refit_every=refit_every, odds=Database().odds_df(),
        elo_weight=elo_weight,
        progress=lambda done, total: bar.progress(
            min(1.0, done / max(1, total)), text=f"Meciul {done} din {total}"),
    )
    bar.empty()
    st.session_state["backtest"] = result

result = st.session_state["backtest"]

# ----------------------------------------------------------------------
st.subheader("Metrici")
summary = result.summary_frame()
cols = st.columns(len(summary))
for col, row in zip(cols, summary.itertuples()):
    delta = None
    if "reper_cote" in summary.columns and pd.notna(getattr(row, "reper_cote", None)):
        diff = row.model - row.reper_cote
        # la RPS / log loss / Brier, mai mic = mai bine
        better = diff < 0 if row.metrica in ("RPS", "log loss", "Brier") else diff > 0
        delta = f"{diff:+.4f} vs piață" if row.metrica != "meciuri testate" else None
    col.metric(row.metrica,
               f"{row.model:.4f}" if row.model < 100 else f"{row.model:.0f}",
               delta=delta, delta_color="inverse"
               if row.metrica in ("RPS", "log loss", "Brier") else "normal")

with st.expander("Ce înseamnă metricile"):
    st.markdown("""
- **RPS** (Ranked Probability Score) — standardul pentru fotbal. Ține cont că
  „egal" e între „victorie gazdă" și „victorie oaspete". **Mai mic = mai bine.**
  Valori tipice pentru modele bune: 0.19–0.21.
- **Log loss** — pedepsește dur încrederea greșită. Mai mic = mai bine.
- **Brier** — eroarea pătratică medie pe probabilități. Mai mic = mai bine.
- **Acuratețe** — cât de des rezultatul cel mai probabil s-a și întâmplat.
  Atenție: un model bun poate avea acuratețe mică dacă meciurile sunt echilibrate.
- **Reper piață** — aceleași metrici calculate din cotele caselor de pariuri,
  cu marja eliminată. Piața e un adversar foarte greu de bătut: dacă modelul se
  apropie de ea, e deja bun.
""")

st.divider()

# ----------------------------------------------------------------------
left, right = st.columns(2)

with left:
    st.subheader("Calibrare")
    calibration = result.calibration
    if not calibration.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=calibration["prezis_mediu"], y=calibration["observat"],
            mode="markers+lines", name="model",
            marker=dict(size=calibration["n"] / calibration["n"].max() * 24 + 6)))
        fig.add_trace(go.Scatter(
            x=[0, 1], y=[0, 1], mode="lines", name="calibrare perfectă",
            line=dict(dash="dot")))
        fig.update_layout(
            height=420, margin=dict(l=0, r=0, t=10, b=0),
            xaxis_title="probabilitate prezisă", yaxis_title="frecvență observată")
        st.plotly_chart(fig, width="stretch")
        deviation = ((calibration["observat"] - calibration["prezis_mediu"]).abs()
                     * calibration["n"] / calibration["n"].sum()).sum()
        st.caption(
            f"Punctele pe diagonală = model calibrat: când spune 30%, se "
            f"întâmplă în 30% din cazuri. **Abatere medie: {deviation:.3f}** "
            f"({deviation:.1%} puncte procentuale)."
        )

        from predictiescor.modeling.calibration import evaluate_gain

        gain = evaluate_gain(result.predictions)
        with st.expander("Ar ajuta un strat de calibrare?"):
            if not gain.get("posibil"):
                st.caption(f"Nu se poate verifica: {gain.get('motiv')}")
            else:
                comparison = pd.DataFrame({
                    "metrică": list(gain["inainte"]),
                    "acum": list(gain["inainte"].values()),
                    "cu calibrare": list(gain["dupa"].values()),
                    "câștig": list(gain["castig"].values()),
                })
                st.dataframe(comparison, hide_index=True, width="stretch",
                             column_config={c: st.column_config.NumberColumn(
                                 format="%.4f") for c in
                                 ("acum", "cu calibrare", "câștig")})
                if gain["ajuta"]:
                    st.warning(
                        "Calibrarea ar îmbunătăți modelul — merită activată.")
                else:
                    st.success(
                        "**Nu ajută** — modelul e deja calibrat. Un strat în "
                        "plus ar adăuga varianță fără să scadă eroarea, de "
                        "aceea nu e activat.")
                st.caption(
                    f"Testat corect: calibratorul e antrenat pe primele "
                    f"{gain['antrenat_pe']} meciuri și evaluat pe următoarele "
                    f"{gain['testat_pe']}, pe care nu le-a văzut."
                )

with right:
    st.subheader("Distribuția predicțiilor")
    predictions = result.predictions
    long = predictions.melt(
        id_vars=["rezultat"], value_vars=["p_gazda", "p_egal", "p_oaspete"],
        var_name="selecție", value_name="probabilitate")
    long["selecție"] = long["selecție"].str.replace("p_", "", regex=False)
    fig = px.box(long, x="selecție", y="probabilitate", color="selecție",
                 height=420)
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), showlegend=False)
    st.plotly_chart(fig, width="stretch")

st.divider()

# ----------------------------------------------------------------------
st.subheader("Comparație cu piața: pariuri de valoare")
if "piata_gazda" not in result.predictions.columns:
    st.info("Nu există cote în baza de date pentru meciurile testate.")
else:
    edge = st.slider("Avantaj minim față de piață", 0.01, 0.20, 0.05, step=0.01,
                     format="%.2f")
    bets = value_bets(result.predictions, edge=edge)
    if bets.empty:
        st.info(f"Niciun pariu cu avantaj de cel puțin {edge:.0%}.")
    else:
        roi = roi_summary(bets)
        interval = bootstrap_roi(bets)
        cols = st.columns(4)
        cols[0].metric("Pariuri", int(roi["pariuri"]))
        cols[1].metric("Profit (miză 1u)", f"{roi['profit']:+.1f}u")
        cols[2].metric(
            "ROI", f"{roi['ROI %']:+.1f}%",
            delta=f"interval {interval['interval jos %']:+.1f}% … "
                  f"{interval['interval sus %']:+.1f}%",
            delta_color="off")
        cols[3].metric("Rată reușită", f"{roi['rata_reusita %']:.1f}%")

        if interval["distinct de zero"]:
            st.warning(
                f"Intervalul de încredere de 90% **nu conține zero** "
                f"({interval['interval jos %']:+.1f}% … "
                f"{interval['interval sus %']:+.1f}%) — rezultatul nu e noroc, "
                f"e un efect real.")
        else:
            st.info(
                f"Intervalul de încredere de 90% **conține zero** "
                f"({interval['interval jos %']:+.1f}% … "
                f"{interval['interval sus %']:+.1f}%) — pe atâtea pariuri, "
                f"rezultatul nu se distinge de noroc.")

        fig = px.line(bets.reset_index(), x="index", y="profit_cumulat",
                      height=360, labels={"index": "pariu",
                                          "profit_cumulat": "profit cumulat"})
        fig.add_hline(y=0, line_dash="dot")
        fig.update_layout(margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, width="stretch")

        st.caption(
            "Aceasta este o simulare pe date istorice, nu o recomandare. "
            "Intervalul e calculat prin reeșantionare pe **meciuri**, nu pe "
            "pariuri: ponturile de pe același meci ies sau pică împreună, iar "
            "tratarea lor ca independente ar da un interval fals de îngust."
        )
        with st.expander("Vezi pariurile"):
            st.dataframe(bets, hide_index=True, width="stretch", height=400)

st.divider()

# ----------------------------------------------------------------------
# Cand modelul nu e de acord cu piata, cine are dreptate?
# ----------------------------------------------------------------------
st.subheader("Când modelul nu e de acord cu piața, cine are dreptate?")
st.caption(
    "Întrebarea de fond a biletelor. Pe istoricul de bilete nu se poate "
    "răspunde — câteva sute de ponturi sunt dominate de noroc. Aici se pune "
    "aceeași întrebare pe toate meciurile cu cote de închidere, adică pe de "
    "câteva ori mai multă informație."
)

if "piata_gazda" not in result.predictions.columns:
    st.info("Nu există cote în baza de date pentru meciurile testate.")
else:
    duel = favourite_duel(result.predictions)
    table = disagreement_table(result.predictions)

    if not duel.get("meciuri"):
        st.info("Modelul și piața aleg același favorit peste tot — nimic de comparat.")
    else:
        st.markdown("**Duelul favoriților** — meciurile unde aleg favoriți diferiți")
        cols = st.columns(4)
        cols[0].metric("Meciuri în dezacord", duel["meciuri"])
        cols[1].metric("Modelul a nimerit", f"{duel['rata model %']:.1f}%")
        cols[2].metric("Piața a nimerit", f"{duel['rata piață %']:.1f}%")
        cols[3].metric("Niciunul (a ieșit al treilea)", duel["niciunul"])

    if not table.empty:
        st.markdown("**Unde se abate modelul, cât îl costă abaterea**")
        left, right = st.columns([3, 2])
        with left:
            fig = px.bar(table, x="dezacord", y="avantaj model", height=340,
                         labels={"avantaj model": "avantaj model (RPS)"})
            fig.add_hline(y=0, line_dash="dot")
            fig.update_layout(margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig, width="stretch")
        with right:
            st.dataframe(
                table, hide_index=True, width="stretch",
                column_config={
                    c: st.column_config.NumberColumn(format="%.4f")
                    for c in ("RPS model", "RPS piață", "avantaj model")})
        st.caption(
            "Bare peste zero = modelul e mai bun pe felia aceea. Dacă barele "
            "coboară pe măsură ce dezacordul crește, abaterile modelului sunt "
            "eroare, nu informație — iar ponturile de valoare pariază exact "
            "pe erorile lui."
        )

    if st.checkbox("Calculează ROI-ul pe mai multe praguri (durează ~10 s)"):
        with st.spinner("Reeșantionez…"):
            sweep = edge_sweep(result.predictions, samples=1500)
        st.dataframe(
            sweep, hide_index=True, width="stretch",
            column_config={
                "prag": st.column_config.NumberColumn(format="%.2f"),
                "ROI %": st.column_config.NumberColumn(format="%.1f"),
                "interval jos %": st.column_config.NumberColumn(format="%.1f"),
                "interval sus %": st.column_config.NumberColumn(format="%.1f"),
                "rata reușită %": st.column_config.NumberColumn(format="%.1f")})
        st.caption(
            "Un avantaj real ar trebui să **crească** odată cu pragul: cerând "
            "o diferență mai mare față de piață, pariurile rămase ar trebui să "
            "fie mai bune. Un profil plat sau care scade e semn de zgomot. "
            "Nu alege pragul din tabelul ăsta — mai multe praguri încercate "
            "deodată fac ca cel mai bun să arate mereu mai bine decât merită."
        )
        st.info(verdict(duel, sweep))
    elif not table.empty:
        st.info(verdict(duel, pd.DataFrame()))

st.divider()
with st.expander("Toate predicțiile din backtest"):
    st.dataframe(result.predictions, hide_index=True, width="stretch", height=420)
    st.download_button(
        "Descarcă CSV",
        result.predictions.to_csv(index=False).encode("utf-8"),
        file_name="backtest.csv", mime="text/csv")
