"""Actualizarea periodica a datelor - scriptul de rulat automat.

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

Rulare manuala:
    python scripts/update.py
    python scripts/update.py --quick        # doar rezultate + calendar (~5s)
    python scripts/update.py --with-tickets # genereaza si biletele zilei

Rulare automata (Windows), la fiecare 3 ore:
    schtasks /create /tn "PredictieScor" /sc hourly /mo 3 ^
      /tr "python C:\\...\\scripts\\update.py --quick"
"""
from __future__ import annotations

import argparse
import logging
import subprocess
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
    p.add_argument("--install", action="store_true",
                   help="programeaza rularea completa zilnica (Windows)")
    p.add_argument("--uninstall", action="store_true",
                   help="sterge sarcina zilnica")
    p.add_argument("--at", default="04:30",
                   help="ora rularii zilnice (implicit 04:30)")
    return p.parse_args()


TASK_NAME = "PredictieScor - actualizare completa"


def _background_python() -> str:
    """Interpretorul care ruleaza FARA fereastra de consola.

    `python.exe` deschide o consola la fiecare pornire - la o sarcina care
    ruleaza din 15 in 15 minute, asta inseamna o fereastra care apare si
    dispare tot timpul. `pythonw.exe` face acelasi lucru, in tacere.
    """
    windowless = Path(sys.executable).with_name("pythonw.exe")
    return str(windowless if windowless.exists() else sys.executable)


def _setup_file_logging(name: str) -> Path:
    """Fara consola nu se mai vede nimic, deci scriem intr-un jurnal."""
    log_dir = ROOT / "data"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / name
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    return path



def install_task(at: str) -> int:
    """Sarcina zilnica: aduce datele noi si continua backfill-ul de statistici.

    Rulare noaptea, ca cele ~90 de cereri API sa nu se suprapuna cu folosirea
    aplicatiei; oricum dureaza minute din cauza limitei de 10 cereri pe minut.
    """
    command = (f'"{_background_python()}" '
               f'"{ROOT / "scripts" / "update.py"}" --quiet')
    result = subprocess.run(
        ["schtasks", "/create", "/tn", TASK_NAME, "/tr", command,
         "/sc", "daily", "/st", at, "/f"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"Sarcina „{TASK_NAME}” creată: rulează zilnic la {at}.")
        print("Aduce rezultatele noi și continuă completarea statisticilor "
              "istorice, în limita a ~90 de cereri API pe zi.")
        print("Rulează în fundal, fără fereastră de consolă.")
        print(f"Jurnalul: {ROOT / 'data' / 'update.log'}")
        return 0
    print("Nu am putut crea sarcina:")
    print((result.stderr or result.stdout).strip())
    return 1


def uninstall_task() -> int:
    result = subprocess.run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
                            capture_output=True, text=True)
    print((result.stdout or result.stderr).strip())
    return result.returncode


def main() -> int:
    args = parse_args()
    if args.install:
        return install_task(args.at)
    if args.uninstall:
        return uninstall_task()

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
