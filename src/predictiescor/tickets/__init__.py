"""Bilete de pariuri: generare, decontare, invatare si surprize."""
from .generator import (
    Leg, STRATEGIES, STRATEGY_BY_NAME, Strategy, Ticket, TicketGenerator,
    matches_in_range, matches_in_round, matches_on, next_match_days,
    next_rounds, recent_match_days,
)
from .learning import (
    Calibrator, accuracy_report, learn_calibration, load_calibration,
    overall_summary, strategy_performance, update_strategy_weights,
)
from .settlement import (
    legs_frame, settle_tickets, tickets_frame,
)
from .watcher import (
    due_matches, next_check_at, run_cycle,
)
from .surprises import (
    surprising_legs, surprising_results, surprising_stats, team_form_anomalies,
)

__all__ = [
    "Leg", "STRATEGIES", "STRATEGY_BY_NAME", "Strategy", "Ticket",
    "TicketGenerator", "matches_on", "matches_in_range", "matches_in_round",
    "next_match_days", "next_rounds", "recent_match_days",
    "Calibrator", "accuracy_report", "learn_calibration", "load_calibration",
    "overall_summary", "strategy_performance", "update_strategy_weights",
    "legs_frame", "settle_tickets", "tickets_frame",
    "surprising_legs", "surprising_results", "surprising_stats",
    "team_form_anomalies",
    "due_matches", "next_check_at", "run_cycle",
]
