"""Ce scrie presa: absențe, reveniri și transferuri, cu evidența la vedere."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from common import (
    data_version, local_time, refresh_button, setup_page, sidebar_footer,
)
from predictiescor.data.newsfacts import CONFIRMED, LIKELY, RUMOUR, corroboration
from predictiescor.storage.db import Database

setup_page("Știri", "📰")
version = data_version()
refresh_button()
sidebar_footer()

db = Database()
st.title("📰 Ce scrie presa")

BADGE = {CONFIRMED: "✅ confirmat", LIKELY: "🟡 probabil", RUMOUR: "⚪ zvon"}
KIND_LABEL = {
    "absenta": "🚑 Absențe",
    "revenire": "💪 Reveniri",
    "transfer_plecare": "➡️ Plecări",
    "transfer_venire": "⬅️ Veniri",
    "formatie_probabila": "📋 Formații probabile",
}

st.info(
    "**Nimic de aici nu intră în predicții.** Datele se strâng ca să poată fi "
    "măsurate mai târziu: presa publică doar prezentul, deci nu putem ști ce "
    "se scria înaintea meciurilor deja jucate. Când vor fi câteva luni de "
    "istoric, se va putea verifica dacă ajută — până atunci ar fi o presupunere."
)

summary = db.news_summary()
if not summary["fapte"]:
    st.warning("Încă nu s-a citit niciun flux de știri.")
    st.code("python scripts/update.py --quick", language="bash")
    st.stop()

cols = st.columns(4)
cols[0].metric("Articole citite", summary["articole"])
cols[1].metric("Fapte extrase", summary["fapte"])
cols[2].metric("Confirmate", summary["pe_incredere"].get(CONFIRMED, 0))
if summary["cea_mai_noua"]:
    latest = pd.to_datetime(summary["cea_mai_noua"], utc=True, errors="coerce")
    if latest is not pd.NaT:
        hours = (datetime.now(timezone.utc) - latest.to_pydatetime()
                 ).total_seconds() / 3600
        cols[3].metric("Cea mai nouă", f"acum {hours:.0f} h")

st.divider()

# ----------------------------------------------------------------------
left, right = st.columns([1, 2])
days = left.slider("Din ultimele … zile", 1, 30, 7)
levels = right.multiselect(
    "Cât de sigure", [CONFIRMED, LIKELY, RUMOUR], default=[CONFIRMED],
    format_func=lambda v: BADGE[v],
    help="Implicit se arată doar faptele spuse ca sigure în articol. "
         "„Zvon” înseamnă că articolul însuși e prudent — sau că regulile "
         "n-au găsit nicio marcă de confirmare.")

facts = db.news_facts_df(days=days)
if facts.empty:
    st.info("Nimic în intervalul ales.")
    st.stop()

support = corroboration(facts.to_dict("records"))
facts["surse"] = [support.get((k, n), 1)
                  for k, n in zip(facts["kind"], facts["player_name"])]

shown = facts[facts["certainty"].isin(levels)] if levels else facts
if shown.empty:
    st.info("Nimic la nivelul de încredere ales. Coboară filtrul.")
    st.stop()

st.caption(
    f"**{len(shown)}** fapte din **{shown['url'].nunique()}** articole. "
    "„Surse” înseamnă câte publicații diferite spun același lucru — două sunt "
    "mult mai convingătoare decât una."
)

# ----------------------------------------------------------------------
for kind, group in shown.groupby("kind", sort=False):
    st.subheader(f"{KIND_LABEL.get(kind, kind)} ({len(group)})")
    ordered = group.sort_values(["surse", "published_at"], ascending=False)

    for row in ordered.itertuples():
        title = f"**{row.player_name}**"
        if row.team:
            title += f" · {row.team}"
        if row.player_id is None:
            title += " · ⚠️ nelegat de un jucător din baza de date"
        if row.surse > 1:
            title += f" · 🔁 {row.surse} surse"

        with st.container(border=True):
            head, meta = st.columns([3, 1])
            head.markdown(f"{title}  \n{BADGE.get(row.certainty, row.certainty)}")
            when = local_time(row.published_at, "%d.%m %H:%M")
            meta.caption(f"{row.source}  \n{when}")
            if row.evidence:
                st.markdown(f"> „{row.evidence}”")
            st.caption(f"[{str(row.title)[:110]}]({row.url})")

st.divider()
with st.expander("Cum sunt citite știrile, și ce poate greși"):
    st.markdown(
        """
Extragerea se face **pe reguli**, nu cu un model de limbă. Consecința: preferă
să nu știe decât să ghicească.

* Numele complet leagă sigur. Numele de familie singur leagă doar dacă e unic
  în ligă **și** nu e precedat de alt prenume — „Florinel Coman" nu e jucătorul
  nostru „Marius Coman", deci rămâne **nelegat**.
* Numele echipelor se șterg din text înainte de căutare, altfel „UTA Arad" era
  citit ca jucătorul „Ofri Arad".
* Într-o propoziție cu doi jucători și un singur verb, faptul merge la cel mai
  apropiat de verb.
* Cea mai slabă marcă din propoziție dă tonul: „surse spun că a semnat oficial"
  rămâne **zvon**.

**Ce greșește:** nu înțelege ironia, citatele din altcineva („X a negat că
pleacă") sau întrebările din titluri. „Cine vine la FCSB după plecarea lui
Tănase" poate ieși ca venire pe numele lui Tănase — de aceea astfel de cazuri
cad aproape întotdeauna în „zvon", nu în „confirmat".

**„Confirmat" înseamnă că articolul o spune ca sigură**, nu că am verificat-o
noi. De aceea evidența e mereu la vedere: citește propoziția și decide singur.
        """
    )
