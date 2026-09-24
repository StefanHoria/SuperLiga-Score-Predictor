"""Verifica automat biletele dupa fiecare meci.

Ruleaza in doua feluri:

  * **o singura verificare** (pentru Task Scheduler, care porneste procesul):

        python scripts/watch.py --once

  * **continuu**, ca proces care ramane pornit:

        python scripts/watch.py

Ciclul e ieftin cand nu are ce face: daca niciun meci nu s-a incheiat de la
ultima verificare, nu se face nicio cerere de retea.

Instalare ca sarcina Windows (ruleaza din 15 in 15 minute, in fundal):

    python scripts/watch.py --install

Dezinstalare:

    python scripts/watch.py --uninstall
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from predictiescor.storage.db import Database  # noqa: E402
from predictiescor.utils.localtime import (  # noqa: E402
    local_time, tz_note,
)
from predictiescor.tickets.watcher import (  # noqa: E402
    next_check_at, run_cycle, status,
)

TASK_NAME = "PredictieScor - verificare bilete"


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



def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Verificare automata a biletelor")
    p.add_argument("--once", action="store_true",
                   help="o singura verificare, apoi iese")
    p.add_argument("--interval", type=int, default=15,
                   help="minute intre verificari, in modul continuu")
    p.add_argument("--force", action="store_true",
                   help="verifica acum, fara sa astepte intervalul minim")
    p.add_argument("--status", action="store_true",
                   help="arata ce asteapta si cand e urmatoarea verificare")
    p.add_argument("--install", action="store_true",
                   help="inregistreaza sarcina in Windows Task Scheduler")
    p.add_argument("--uninstall", action="store_true",
                   help="sterge sarcina din Task Scheduler")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args()


def show_status(db: Database) -> None:
    info = status(db)
    print(f"Bilete deschise      : {info['bilete_deschise']}")
    print(f"Ultima verificare    : "
          f"{local_time(info['ultima_verificare']) or 'niciodată'}")

    due = info["de_verificat"]
    if due:
        print(f"\nDe verificat acum ({len(due)}):")
        for match in due:
            mark = "rezultat lipsă" if not match["has_result"] else \
                f"{match['open_legs']} ponturi deschise"
            when = local_time(match["kickoff"], "%d.%m %H:%M")
            print(f"  {when}  {match['label']:42s} {mark}")
    else:
        print("\nNimic de verificat acum.")

    upcoming = info["urmatoarea_verificare"]
    if upcoming:
        delta = upcoming - datetime.now(timezone.utc)
        hours = delta.total_seconds() / 3600
        print(f"\nUrmătoarea verificare utilă: {local_time(upcoming)} "
              f"({tz_note()})  (peste {hours:.1f} ore)")
    else:
        print("\nNu sunt meciuri programate în așteptare.")


def install_task() -> int:
    """Inregistreaza sarcina in Task Scheduler, la fiecare 15 minute."""
    script = ROOT / "scripts" / "watch.py"
    command = f'"{_background_python()}" "{script}" --once --quiet'

    result = subprocess.run(
        ["schtasks", "/create", "/tn", TASK_NAME, "/tr", command,
         "/sc", "minute", "/mo", "15", "/f"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        from predictiescor.utils.tasks import relax_conditions
        print(f"Sarcina „{TASK_NAME}” a fost creată: rulează din 15 în 15 minute.")
        ok, detaliu = relax_conditions(TASK_NAME)
        print(("  " if ok else "  Atenție: nu am putut schimba condițiile — ")
              + detaliu)
        print("Rulează în fundal, fără fereastră de consolă.")
        print(f"Jurnalul: {ROOT / 'data' / 'watch.log'}")
        return 0
    print("Nu am putut crea sarcina:")
    print((result.stderr or result.stdout).strip())
    print("\nÎncearcă dintr-un terminal pornit ca administrator, sau creeaz-o "
          "manual cu:")
    print(f'  schtasks /create /tn "{TASK_NAME}" /tr {command} '
          f'/sc minute /mo 15')
    return 1


def uninstall_task() -> int:
    result = subprocess.run(
        ["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"Sarcina „{TASK_NAME}” a fost ștearsă.")
        return 0
    print((result.stderr or result.stdout).strip())
    return 1


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s")
    if args.once:
        _setup_file_logging("watch.log")

    if args.install:
        return install_task()
    if args.uninstall:
        return uninstall_task()

    db = Database()

    if args.status:
        show_status(db)
        return 0

    if args.once:
        result = run_cycle(db, force=args.force)
        stamp = local_time(result.checked_at, "%H:%M:%S")
        if result.did_work:
            logging.getLogger("watch").warning(result.describe())
        print(f"[{stamp}] {result.describe()}")
        if result.message and not args.quiet:
            print(f"          {result.message}")
        return 0

    print(f"Verific biletele din {args.interval} în {args.interval} minute. "
          f"Oprește cu Ctrl+C.")
    show_status(db)
    print()
    try:
        while True:
            result = run_cycle(db, force=args.force)
            stamp = local_time(result.checked_at, "%H:%M:%S")
            if result.did_work or result.due:
                print(f"[{stamp}] {result.describe()}")
                if result.message:
                    print(f"          {result.message}")
            time.sleep(max(60, args.interval * 60))
    except KeyboardInterrupt:
        print("\nOprit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
