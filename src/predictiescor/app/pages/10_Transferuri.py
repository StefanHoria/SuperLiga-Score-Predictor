"""Transferuri: cine a venit, cine a plecat și cu ce bani."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from common import data_version, refresh_button, setup_page, sidebar_footer
from predictiescor.config import get_config
from predictiescor.storage.db import Database

setup_page("Transferuri", "🔁")
version = data_version()
refresh_button()
sidebar_footer()

db = Database()
cfg = get_config()
st.title("🔁 Transferuri")


def bani(value, *, zero: str = "0 €") -> str:
    """Suma în formă citibilă. Gol înseamnă «nepublicată», nu «zero».

    `zero` diferă după context: pentru o cotă de transfer, 0 înseamnă «gratis»;
    pentru un total de club, «gratis» n-ar avea niciun sens.
    """
    if value is None or pd.isna(value):
        return "—"
    value = float(value)
    if value == 0:
        return zero
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f} mil. €"
    return f"{value / 1_000:.0f}k €"


sezoane = sorted(
    {int(s) for s in db.transfers_df()["season"].unique()}, reverse=True) \
    if not db.transfers_df().empty else []
if not sezoane:
    st.warning("Nu există date despre transferuri.")
    st.code("python scripts/update.py", language="bash")
    st.caption("Se aduc de pe pagina de transferuri a fiecărui club, la "
               "actualizarea completă.")
    st.stop()

sezon = st.selectbox("Sezon", sezoane, format_func=lambda s: f"{s}/{str(s+1)[-2:]}")
toate = db.transfers_df(sezon)
bilant = db.transfer_balance(sezon)

# ----------------------------------------------------------------------
total_platit = float(toate[toate["direction"] == "venire"]["fee_eur"].fillna(0).sum())
total_incasat = float(toate[toate["direction"] == "plecare"]["fee_eur"].fillna(0).sum())
nepublicate = int(toate["fee_eur"].isna().sum())

cols = st.columns(5)
cols[0].metric("Transferuri", len(toate))
cols[1].metric("Plătit în ligă", bani(total_platit))
cols[2].metric("Încasat în ligă", bani(total_incasat))
cols[3].metric("Sold", bani(total_incasat - total_platit))
cols[4].metric("Sume nepublicate", nepublicate)

st.caption(
    "**Gratis** și **nepublicată** sunt lucruri diferite. Un transfer liber "
    "intră în calcul cu zero; unul cu suma nedezvăluită nu intră deloc — "
    "altfel un club care și-a vândut jucătorii pe sume secrete ar părea că "
    "i-a dat degeaba. Sunt numărate separat, ca să știi cât de completă e "
    "socoteala."
)

st.divider()

# ----------------------------------------------------------------------
st.subheader("Bilanțul cluburilor")

grafic = bilant.copy()
grafic["profit_mil"] = grafic["profit"] / 1_000_000
figura = px.bar(
    grafic, x="profit_mil", y="echipă", orientation="h", height=520,
    labels={"profit_mil": "profit (mil. €)", "echipă": ""},
    color=grafic["profit"].map(lambda v: "câștig" if v >= 0 else "cheltuială"),
    color_discrete_map={"câștig": "#1a7f37", "cheltuială": "#8b2c2c"})
figura.update_layout(margin=dict(l=0, r=0, t=10, b=0), showlegend=False,
                     yaxis={"categoryorder": "total ascending"})
st.plotly_chart(figura, width="stretch")

afisat = bilant.copy()
for coloana in ("plătit", "încasat", "profit"):
    afisat[coloana] = afisat[coloana].map(bani)
st.dataframe(afisat, hide_index=True, width="stretch")

st.divider()

# ----------------------------------------------------------------------
st.subheader("Pe club")
cluburi = sorted(toate["echipa"].unique())
club = st.selectbox("Echipa", cluburi)
ale_lui = toate[toate["echipa"] == club]

rand = bilant[bilant["echipă"] == club]
if not rand.empty:
    r = rand.iloc[0]
    m = st.columns(4)
    m[0].metric("A plătit", bani(r["plătit"]))
    m[1].metric("A încasat", bani(r["încasat"]))
    m[2].metric("Profit", bani(r["profit"]),
                delta=f"{r['veniri']} veniri / {r['plecări']} plecări",
                delta_color="off")
    m[3].metric("Împrumuturi", int(r["împrumuturi"]))

stanga, dreapta = st.columns(2)
for coloana, directie, titlu in ((stanga, "venire", "⬅️ Veniri"),
                                 (dreapta, "plecare", "➡️ Plecări")):
    with coloana:
        parte = ale_lui[ale_lui["direction"] == directie]
        st.markdown(f"**{titlu}** ({len(parte)})")
        if parte.empty:
            st.caption("Niciunul.")
            continue
        tabel = parte.copy()
        tabel["sumă"] = tabel["fee_eur"].map(
            lambda v: bani(v, zero="gratis"))
        tabel["de la / la"] = tabel["other_club"].fillna("—")
        tabel["jucător"] = [
            f"{n} 🔄" if loan else n
            for n, loan in zip(tabel["player_name"], tabel["is_loan"])]
        st.dataframe(
            tabel[["jucător", "position", "de la / la", "sumă"]]
            .rename(columns={"position": "poziție"}),
            hide_index=True, width="stretch", height=340)
st.caption("🔄 = împrumut")

st.divider()

# ----------------------------------------------------------------------
st.subheader("Cele mai scumpe mutări din ligă")
scumpe = toate.dropna(subset=["fee_eur"])
scumpe = scumpe[scumpe["fee_eur"] > 0].nlargest(15, "fee_eur").copy()
if scumpe.empty:
    st.caption("Nicio sumă publicată.")
else:
    scumpe["sumă"] = scumpe["fee_eur"].map(bani)
    scumpe["mișcare"] = [
        (f"{o} → {e}" if d == "venire" else f"{e} → {o}")
        for d, e, o in zip(scumpe["direction"], scumpe["echipa"],
                           scumpe["other_club"].fillna("—"))]
    st.dataframe(
        scumpe[["player_name", "mișcare", "sumă", "fee_text"]].rename(
            columns={"player_name": "jucător", "fee_text": "cum scrie sursa"}),
        hide_index=True, width="stretch")
    st.caption(
        "Coloana din dreapta e textul original de la sursă, ca să poți verifica "
        "interpretarea: „Loan fee: €1.50m” e un împrumut cu taxă, nu un "
        "transfer definitiv."
    )
