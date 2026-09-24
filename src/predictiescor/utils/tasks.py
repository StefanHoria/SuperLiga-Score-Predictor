"""Setarile sarcinilor din Task Scheduler.

`schtasks /create` lasa implicite doua conditii care, pe un calculator de acasa,
fac sarcina sa nu ruleze aproape niciodata:

* **o rulare ratata nu se recupereaza.** Daca PC-ul e oprit la 04:30, rularea
  zilei se sare de tot, nu porneste cand se deschide calculatorul. Asa a trecut
  septembrie 2026 fara nicio actualizare completa intre 9 si 25 - rezultatele au
  intrat doar pentru ca le aducea verificatorul de bilete, nu si cotele,
  statisticile sau loturile;
* **nu porneste pe baterie**, si se opreste daca laptopul trece pe baterie in
  timpul rularii. Pe 24 septembrie sarcina a fost refuzata exact asa
  (0x800710E0).

`schtasks` nu stie sa schimbe nici una, deci se trece prin PowerShell dupa ce
sarcina e creata.
"""
from __future__ import annotations

import subprocess

#: Peste atat, o rulare e blocata, nu lenta: actualizarea completa ia ~16 min.
TIME_LIMIT_HOURS = 2


def relax_conditions(task_name: str) -> tuple[bool, str]:
    """Sarcina porneste si dupa o rulare ratata, si pe baterie.

    Intoarce (reusit, mesaj). Esecul nu e fatal: sarcina exista oricum, doar
    cu conditiile implicite.
    """
    script = (
        "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable "
        "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        f"-ExecutionTimeLimit (New-TimeSpan -Hours {TIME_LIMIT_HOURS}) "
        "-MultipleInstances IgnoreNew; "
        f"Set-ScheduledTask -TaskName '{task_name}' -Settings $s | Out-Null"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return True, ("pornește și după o rulare ratată (PC oprit la ora "
                      "programată) și pe baterie")
    return False, (result.stderr or result.stdout).strip()
