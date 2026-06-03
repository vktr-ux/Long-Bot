from __future__ import annotations

from app.storage.models import SignalCandidate


def should_alert(signal: SignalCandidate, previous_state: dict | None, cooldown_config: dict) -> tuple[bool, str]:
    if signal.level == "NO_SIGNAL":
        return False, "no signal"
    if previous_state is None:
        return True, "first signal"
    old_state = previous_state.get("state")
    old_score = int(previous_state.get("score") or previous_state.get("last_score") or 0)
    old_ts = int(previous_state.get("timestamp_ms") or 0)
    elapsed_minutes = (signal.timestamp_ms - old_ts) / 60_000 if old_ts else 9999
    if cooldown_config.get("allow_immediate_state_transition_alert") and signal.state != old_state:
        return True, "state transition"
    if signal.score >= old_score + cooldown_config["score_increase_to_repeat"]:
        return True, "score increased"
    limit = cooldown_config["very_hot_minutes"] if signal.level == "VERY_HOT" else cooldown_config["default_minutes"]
    if elapsed_minutes >= limit:
        return True, "cooldown elapsed"
    return False, f"cooldown active ({elapsed_minutes:.1f}m)"

