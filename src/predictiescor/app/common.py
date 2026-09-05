"""Utilitare comune pentru paginile Streamlit: incarcare cu cache si formatare."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Paginile Streamlit sunt rulate ca scripturi independente, deci ne asiguram
# ca pachetul `predictiescor` e importabil indiferent de unde se porneste.
SRC = Path(__file__).resolve().parents[2]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from predictiescor.config import get_config, reload_config  # noqa: E402
from predictiescor.modeling.pipeline import PredictionPipeline  # noqa: E402
from predictiescor.storage.db import Database  # noqa: E402

PAGE_ICON = "⚽"
APP_TITLE = "PredictieScor - SuperLiga"



def setup_page(title: str, icon: str = PAGE_ICON) -> None:
    st.set_page_config(page_title=f"{title} | PredictieScor", page_icon=icon,
                       layout="wide", initial_sidebar_state="expanded")


@st.cache_resource(show_spinner="Antrenez modelele...")
def load_pipeline(_version: int = 0) -> PredictionPipeline:
    """Modelele se antreneaza o singura data per sesiune (cateva secunde)."""
    return PredictionPipeline().train()


@st.cache_data(show_spinner=False)
def load_matches(_version: int = 0, finished_only: bool = True) -> pd.DataFrame:
    return Database().matches_df(finished_only=finished_only)


@st.cache_data(show_spinner=False)
def load_team_stats(_version: int = 0) -> pd.DataFrame:
    return Database().team_stats_df()


@st.cache_data(show_spinner=False)
def load_player_season_stats(_version: int = 0) -> pd.DataFrame:
    return Database().player_season_stats_df()


@st.cache_data(show_spinner=False)
def load_summary(_version: int = 0) -> dict:
    return Database().summary()


def data_version() -> int:
    """Versiunea datelor: se schimba la ORICE scriere in baza.

    Derivata din ora ultimei modificari a fisierelor bazei, nu dintr-un simplu
    contor de sesiune. Actualizarile din linia de comanda - sarcina programata
    sau `update.py` rulat de mana - scriu in aceeasi baza, iar aplicatia trebuie
    sa le observe. Altfel arata cifre vechi pana la o repornire, exact ce s-a
    intamplat dupa recuperarea zilelor pierdute.

    Ne uitam si la fisierul `-wal`: in modul WAL scrierile ajung acolo intai,
    deci `.db` poate ramane neatins minute intregi.
    """
    stamp = 0
    base = get_config().db_path
    for path in (base, base.with_name(base.name + "-wal")):
        try:
            stamp = max(stamp, int(path.stat().st_mtime))
        except OSError:
            continue
    # contorul de sesiune ramane, ca butonul de reincarcare sa functioneze
    # chiar daca ora fisierului nu s-a schimbat
    return stamp + int(st.session_state.get("data_version", 0))


def bump_data_version() -> None:
    """Invalideaza toate cache-urile: datele din baza s-au schimbat."""
    st.session_state["data_version"] = data_version() + 1
    st.cache_data.clear()
    st.cache_resource.clear()


def run_data_refresh(*, quick: bool = True, with_players: bool = True,
                     api_budget: int | None = None,
                     with_tickets: bool = False, container=None):
    """Descarca date noi, aratand progresul pas cu pas.

    Intoarce raportul (`RefreshReport`) sau None daca a picat complet.
    Ruleaza acelasi cod ca `python scripts/update.py`.
    """
    from predictiescor.data.refresh import run_refresh

    target = container or st
    label = "Actualizare rapidă" if quick else "Actualizare completă"
    with target.status(f"{label} în curs...", expanded=True) as status:
        placeholder = st.empty()

        def progress(index: int, total: int, step_label: str) -> None:
            placeholder.write(f"**{index}/{total}** · {step_label}")

        try:
            report = run_refresh(
                quick=quick, with_players=with_players, api_budget=api_budget,
                with_tickets=with_tickets, progress=progress,
            )
        except Exception as exc:  # noqa: BLE001
            status.update(label=f"{label} a eșuat", state="error")
            st.error(f"Actualizarea nu a putut rula: {exc}")
            return None

        placeholder.empty()
        for step in report.steps:
            st.write(f"{step.icon} **{step.name}** — {step.detail}")

        state = "complete" if report.ok else "error"
        note = "" if report.ok else f" · {len(report.failures)} pași cu probleme"
        status.update(
            label=f"{label} gata în {report.seconds:.0f}s{note}", state=state)

    st.session_state["last_refresh_rows"] = report.as_rows()
    st.session_state["last_refresh_fresh"] = True
    bump_data_version()
    return report


def refresh_button(label: str = "Reîncarcă modelele") -> None:
    """Butoanele din bara laterală, disponibile pe orice pagină."""
    with st.sidebar:
        if st.button("⬇️ Actualizează datele", width="stretch",
                     type="primary",
                     help="Aduce rezultatele noi, calendarul și statisticile "
                          "oficiale LPF (~10 secunde). Pentru actualizarea "
                          "completă, vezi pagina «Date și surse»."):
            report = run_data_refresh(quick=True)
            if report is not None:
                st.rerun()

        if st.button(label, width="stretch",
                     help="Reantrenează modelele pe datele deja existente, "
                          "fără să descarce nimic."):
            bump_data_version()
            reload_config()
            st.rerun()


def sidebar_footer(pipeline: PredictionPipeline | None = None) -> None:
    cfg = get_config()
    st.sidebar.divider()
    st.sidebar.caption(
        f"Sezon curent: **{cfg.current_season}/{str(cfg.current_season + 1)[-2:]}**"
    )
    if pipeline is not None and pipeline.trained_at:
        st.sidebar.caption(
            "Modele antrenate: "
            + local_time(pipeline.trained_at) + f" ({tz_note()})"
        )
    if pipeline is not None and pipeline.warnings:
        with st.sidebar.expander(f"Avertismente ({len(pipeline.warnings)})"):
            for warning in pipeline.warnings:
                st.warning(warning, icon="⚠️")


def require_pipeline() -> PredictionPipeline:
    """Incarca modelele si opreste pagina cu un mesaj clar daca nu sunt gata."""
    pipeline = load_pipeline(data_version())
    if not pipeline.ready:
        st.error("Modelele nu sunt antrenate - lipsesc datele.")
        for warning in pipeline.warnings:
            st.warning(warning)
        st.code("python scripts/fetch_data.py", language="bash")
        st.stop()
    return pipeline


# ----------------------------------------------------------------------
# Formatare
# ----------------------------------------------------------------------
def pct(value: float) -> str:
    return f"{value:.1%}"


def odds(value: float) -> str:
    return "-" if value <= 0 else f"{1.0 / value:.2f}"


def probability_bar(data: dict[str, float], title: str = "") -> None:
    """Afiseaza un set de probabilitati ca bare orizontale."""
    frame = pd.DataFrame({
        "selectie": list(data.keys()),
        "probabilitate": list(data.values()),
    })
    frame["cota corecta"] = frame["probabilitate"].map(
        lambda p: round(1 / p, 2) if p > 0 else float("inf"))
    if title:
        st.caption(title)
    st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        column_config={
            "probabilitate": st.column_config.ProgressColumn(
                "probabilitate", format="percent", min_value=0.0, max_value=1.0),
            "cota corecta": st.column_config.NumberColumn(
                "cota corecta", format="%.2f"),
        },
    )


def season_label(season: int) -> str:
    return f"{season}/{str(season + 1)[-2:]}"


# ----------------------------------------------------------------------
# Ora locala
# ----------------------------------------------------------------------
# Implementarea sta in `utils/localtime.py`, ca sa o poata folosi si modulele
# de dedesubt - generatorul de bilete si scripturile din linia de comanda
# afisau UTC fiindca de aici nu aveau cum sa importe.
from predictiescor.utils.localtime import (  # noqa: E402,F401
    LOCAL_TZ, local_series, local_time, now_local, to_local, tz_note,
)
