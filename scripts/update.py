"""Actualizarea datelor, pornita manual.

Aceeasi logica ruleaza si din butonul "Actualizează datele" de pe pagina
*Date și surse* a dashboard-ului; ambele apeleaza `predictiescor.data.refresh`.

Ce face, in ordine:
  1. **Rezultate si cote** de la football-data.co.uk (sezonul curent).
  2. **Calendar si rezultate live** de la Transfermarkt - inclusiv meciurile
     viitoare, necesare pentru bilete.
  3. **Loturi si statistici de jucatori** de la Transfermarkt.
  4. **Statistici detaliate pe meci** de la API-Football, in limita cotei
     zilnice ramase.
  5. **Curatare**: uneste eventualele echipe/meciuri duplicate.
  6. **Bilete**: deconteaza ce s-a jucat si reinvata din rezultate.

Rulare:
    python scripts/update.py
    python scripts/update.py --quick        # doar rezultate + calendar (~5s)
    python scripts/update.py --with-tickets # genereaza si biletele zilei

Nu exista rulare automata. Sarcinile Windows care o faceau au fost sterse pe 29
septembrie 2026, la cerere: nimic din proiect nu mai porneste fara sa-l pornesti
tu - butonul din aplicatie sau comanda de mai sus.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from predictiescor.config import get_config  # noqa: E402
from predictiescor.data.refresh import run_refresh  # noqa: E402
from predictiescor.storage.db import Database  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Actualizare date SuperLiga")
    p.add_argument("--quick", action="store_true",
                   help="doar rezultate si calendar (fara jucatori/API-Football)")
    p.add_argument("--no-players", action="store_true",
                   help="sare peste reimprospatarea loturilor")
    p.add_argument("--budget", type=int, default=None,
                   help="cereri API-Football permise (implicit: din config)")
    p.add_argument("--force-all", action="store_true",
                   help="reia si pasii proaspeti (ignora fereastra de "
                        "prospetime)")
    p.add_argument("--with-tickets", action="store_true",
                   help="genereaza biletele pentru urmatoarea zi cu meciuri")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args()


def _setup_file_logging(name: str) -> Path:
    """Cu `--quiet` nu se vede nimic in consola, deci scriem intr-un jurnal."""
    log_dir = ROOT / "data"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / name
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    return path


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s")
    if args.quiet:
        _setup_file_logging("update.log")

    def show(index: int, total: int, label: str) -> None:
        print(f"\n─── {index}/{total} {label} "
              + "─" * max(0, 52 - len(label)))

    report = run_refresh(
        Database(), get_config(),
        quick=args.quick,
        with_players=not args.no_players,
        api_budget=args.budget,
        with_tickets=args.with_tickets,
        force_all=args.force_all,
        progress=show,
    )

    for step in report.steps:
        print(f"{step.icon} {step.name}: {step.detail}")

    print("\n─── Rezumat " + "─" * 48)
    for key, value in report.summary.items():
        print(f"  {key:<24} {value}")
    print(f"\nGata în {report.seconds:.0f} secunde.")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
