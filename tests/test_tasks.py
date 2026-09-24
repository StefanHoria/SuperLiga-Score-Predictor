"""Conditiile sarcinilor programate.

`schtasks /create` lasa o rulare ratata nerecuperata si refuza pornirea pe
baterie. Cu PC-ul oprit la 04:30, actualizarea completa n-a mai rulat deloc
intre 9 si 25 septembrie 2026. Testele nu ating Task Scheduler-ul adevarat:
verifica doar ce comanda s-ar trimite.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from predictiescor.utils import tasks


def _capture(monkeypatch, returncode=0, stderr=""):
    sent = {}

    def fake_run(args, **kwargs):
        sent["args"] = args
        return SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)

    monkeypatch.setattr(tasks.subprocess, "run", fake_run)
    return sent


def test_a_missed_run_is_caught_up(monkeypatch):
    """Rularea de la 04:30, ratata cu PC-ul oprit, porneste la deschidere."""
    sent = _capture(monkeypatch)
    tasks.relax_conditions("PredictieScor - actualizare completa")
    assert "-StartWhenAvailable" in sent["args"][-1]


def test_it_runs_on_battery_and_is_not_stopped_by_it(monkeypatch):
    """Pe 24 septembrie sarcina a fost refuzata cu laptopul pe baterie."""
    sent = _capture(monkeypatch)
    tasks.relax_conditions("x")
    script = sent["args"][-1]
    assert "-AllowStartIfOnBatteries" in script
    assert "-DontStopIfGoingOnBatteries" in script


def test_a_stuck_run_is_eventually_stopped(monkeypatch):
    """Implicit, o rulare blocata ar tine 72 de ore; actualizarea ia ~16 minute."""
    sent = _capture(monkeypatch)
    tasks.relax_conditions("x")
    assert f"-Hours {tasks.TIME_LIMIT_HOURS}" in sent["args"][-1]


def test_it_targets_the_named_task(monkeypatch):
    sent = _capture(monkeypatch)
    tasks.relax_conditions("PredictieScor - verificare bilete")
    assert "-TaskName 'PredictieScor - verificare bilete'" in sent["args"][-1]


def test_a_failure_is_reported_not_raised(monkeypatch):
    """Sarcina exista oricum; doar conditiile raman implicite."""
    _capture(monkeypatch, returncode=1, stderr="Access is denied.")
    ok, mesaj = tasks.relax_conditions("x")
    assert ok is False
    assert "Access is denied" in mesaj
