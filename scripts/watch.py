"""Verifica biletele dupa meciuri, cand il pornesti tu.

  * **o singura verificare**:

        python scripts/watch.py --once

  * **continuu, in terminal**, pana la Ctrl+C:

        python scripts/watch.py

Ciclul e ieftin cand nu are ce face: daca niciun meci nu s-a incheiat de la
ultima verificare, nu se face nicio cerere de retea.

Nu mai exista varianta de sarcina Windows. A fost stearsa pe 29 septembrie 2026,
la cerere: nimic din proiect nu ruleaza in fundal.
"""
from __future__ import annotations

import argparse
import logging
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


def _setup_file_logging(name: str) -> Path:
    """Pastreaza un jurnal al verificarilor facute cu `--once`."""
    log_dir = ROOT / "data"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / name
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    return path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Verificarea biletelor")
    p.add_argument("--once", action="store_true",
                   help="o singura verificare, apoi iese")
    p.add_argument("--interval", type=int, default=15,
                   help="minute intre verificari, in modul continuu")
    p.add_argument("--force", action="store_true",
                   help="verifica acum, fara sa astepte intervalul minim")
    p.add_argument("--status", action="store_true",
                   help="arata ce asteapta si cand e urmatoarea verificare")
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


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s")
    if args.once:
        _setup_file_logging("watch.log")

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
