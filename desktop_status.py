"""Present Codex rollouts and Claude Code status files as plain desktop-strip status data."""

from __future__ import annotations

from collections import OrderedDict
import json
import math
from pathlib import Path
import time
import uuid

import codex_status

STALE_AFTER_SECONDS = 120
CLAUDE_STATUS_PATH = Path("~/.claude/bifrost/claude-status.json")
_DETAILS = {
    "Codex": {
        "waiting": "Waiting for Codex telemetry",
        "stale": "Rollout has not changed for {age} seconds",
        "ready": "Live Codex status",
    },
    "Claude Code": {
        "waiting": "Waiting for Claude Code telemetry",
        "stale": "Claude Code status has not changed for {age} seconds",
        "ready": "Live Claude Code status",
    },
}
_USAGE_LABELS = {"five_hour": "5h", "seven_day": "Weekly"}
_MODEL_HISTORY_BYTES = 8 * 1024 * 1024
_MODEL_CACHE_LIMIT = 64
_MODEL_CACHE: OrderedDict[tuple[str, int, int], tuple[str | None, int]] = OrderedDict()


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _empty_status(state: str, detail: str) -> dict[str, object]:
    return {
        "state": state,
        "context_percent": None,
        "used_tokens": None,
        "window_tokens": None,
        "model": "Model unavailable",
        "usage": [],
        "detail": detail,
    }


def _reset_text(resets_at: float | None, now: float, stale: bool) -> str:
    if resets_at is None:
        return "Reset time passed" if stale else "Reset time unknown"
    remaining = int(resets_at - now)
    if remaining <= 0:
        return "Reset time passed"
    hours, remainder = divmod(remaining, 3600)
    minutes = remainder // 60
    if hours:
        return f"Resets in {hours}h {minutes}m" if minutes else f"Resets in {hours}h"
    return f"Resets in {max(1, minutes)}m"


def _model_from_snapshot(snapshot: dict[str, object]) -> str | None:
    model = snapshot.get("model")
    return model.strip() if isinstance(model, str) and model.strip() else None


def _remember_model(key: tuple[str, int, int], model: str | None, size: int) -> None:
    _MODEL_CACHE[key] = (model, size)
    _MODEL_CACHE.move_to_end(key)
    while len(_MODEL_CACHE) > _MODEL_CACHE_LIMIT:
        _MODEL_CACHE.popitem(last=False)


def _snapshot_model(
    rollout: Path,
    snapshot: dict[str, object],
    size: int,
    device: int,
    inode: int,
) -> str | None:
    key = (str(rollout.absolute()), device, inode)
    prior = _MODEL_CACHE.get(key)
    if prior is not None and size < prior[1]:
        del _MODEL_CACHE[key]
        prior = None

    tail_model = _model_from_snapshot(snapshot)
    if tail_model is not None:
        _remember_model(key, tail_model, size)
        return tail_model

    if prior is not None:
        if prior[0] is not None or size == prior[1]:
            _MODEL_CACHE.move_to_end(key)
            _remember_model(key, prior[0], max(size, prior[1]))
            return prior[0]

    history_model = None
    try:
        history = codex_status.read_rollout(str(rollout), max_bytes=_MODEL_HISTORY_BYTES)
        history_model = _model_from_snapshot(history)
    except OSError:
        history_model = None
    _remember_model(key, history_model, size)
    return history_model


def find_rollout(codex_home: Path | str, session_id: str) -> Path | None:
    """Find the unique Codex rollout whose filename ends in this session UUID."""
    if not isinstance(session_id, str):
        raise ValueError("session id must be a UUID")
    try:
        normalized_id = str(uuid.UUID(session_id))
    except (ValueError, AttributeError) as exc:
        raise ValueError("session id must be a UUID") from exc

    sessions_dir = Path(codex_home).expanduser() / "sessions"
    suffix = f"-{normalized_id}.jsonl"
    try:
        matches = sorted(
            path for path in sessions_dir.rglob(f"rollout-*-{normalized_id}.jsonl")
            if path.is_file() and path.name.startswith("rollout-") and path.name.endswith(suffix)
        )
    except OSError as exc:
        raise OSError(f"unable to search Codex sessions under {sessions_dir}") from exc
    if len(matches) > 1:
        raise ValueError(f"multiple Codex rollouts found for session {normalized_id}")
    return matches[0] if matches else None


def find_latest_rollout(codex_home: Path | str) -> Path | None:
    """Find the Codex rollout written most recently, the session in use now."""
    sessions_dir = Path(codex_home).expanduser() / "sessions"
    newest: tuple[float, Path] | None = None
    try:
        for path in sessions_dir.rglob("rollout-*.jsonl"):
            try:
                modified = path.stat().st_mtime
            except OSError:
                continue
            if path.is_file() and (newest is None or modified > newest[0]):
                newest = (modified, path)
    except OSError as exc:
        raise OSError(f"unable to search Codex sessions under {sessions_dir}") from exc
    return None if newest is None else newest[1]


def present_status(
    snapshot: dict[str, object],
    now: float | None = None,
    updated_at: float | None = None,
    source: str = "Codex",
) -> dict[str, object]:
    """Normalize a statusLine-shaped snapshot from `source` for the desktop strip."""
    details = _DETAILS[source]
    now = time.time() if now is None else now
    snapshot = snapshot if isinstance(snapshot, dict) else {}

    model_data = snapshot.get("model")
    model = model_data.get("display_name") if isinstance(model_data, dict) else model_data
    model = model.strip() if isinstance(model, str) and model.strip() else "Model unavailable"

    context = snapshot.get("context_window")
    context = context if isinstance(context, dict) else {}
    context_percent = _number(context.get("used_percentage"))
    if context_percent is not None and context_percent < 0:
        context_percent = None

    input_tokens = _number(context.get("total_input_tokens"))
    output_tokens = _number(context.get("total_output_tokens"))
    token_total = None
    if (
        input_tokens is not None
        and output_tokens is not None
        and input_tokens >= 0
        and output_tokens >= 0
    ):
        candidate_total = input_tokens + output_tokens
        if math.isfinite(candidate_total) and (candidate_total > 0 or context_percent is not None):
            token_total = int(candidate_total)

    window_tokens = _number(context.get("context_window_size"))
    if window_tokens is not None:
        window_tokens = int(window_tokens) if window_tokens > 0 else None

    rate_limits = snapshot.get("rate_limits")
    rate_limits = rate_limits if isinstance(rate_limits, dict) else {}
    usage = []
    for key, entry in rate_limits.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            continue
        used_percent = _number(entry.get("used_percentage"))
        if used_percent is None or used_percent < 0:
            continue
        resets_at = _number(entry.get("resets_at"))
        expired = resets_at is not None and resets_at <= now
        stale = entry.get("stale") is True or expired
        usage.append({
            "label": _USAGE_LABELS.get(key, key.replace("_", " ").title()),
            "used_percent": used_percent,
            "reset_text": _reset_text(resets_at, now, stale),
            "stale": stale,
        })

    age = None if updated_at is None else max(0.0, now - updated_at)
    has_model = model != "Model unavailable"
    has_data = has_model or context_percent is not None or token_total is not None or bool(usage)
    if not has_data:
        state = "waiting"
        detail = details["waiting"]
    elif age is None:
        state = "waiting"
        detail = "Waiting for rollout timestamp"
    elif age > STALE_AFTER_SECONDS:
        state = "stale"
        detail = details["stale"].format(age=int(age))
    else:
        state = "ready"
        detail = details["ready"]

    return {
        "state": state,
        "context_percent": context_percent,
        "used_tokens": token_total,
        "window_tokens": window_tokens,
        "model": model,
        "usage": usage,
        "detail": detail,
    }


def load_status(path: Path | str | None, now: float | None = None) -> dict[str, object]:
    """Read and normalize a rollout, retaining unknown metrics as `None`."""
    if path is None:
        return _empty_status("waiting", "Waiting for Codex session")
    try:
        rollout = Path(path).expanduser()
        first_stat = rollout.stat()
        raw_snapshot = codex_status.read_rollout(str(rollout))
        stat = rollout.stat()
        if (stat.st_dev, stat.st_ino) != (first_stat.st_dev, first_stat.st_ino):
            raw_snapshot = codex_status.read_rollout(str(rollout))
            stat = rollout.stat()
        model = _snapshot_model(rollout, raw_snapshot, stat.st_size, stat.st_dev, stat.st_ino)
        if _model_from_snapshot(raw_snapshot) is None and model is not None:
            raw_snapshot = {**raw_snapshot, "model": model}
        snapshot = codex_status.normalize_snapshot(raw_snapshot, now=now)
        updated_at = stat.st_mtime
    except OSError:
        return _empty_status("unavailable", "Unable to read Codex rollout")
    return present_status(snapshot, now=now, updated_at=updated_at)


def load_claude_status(path: Path | str, now: float | None = None) -> dict[str, object]:
    """Read the statusLine-shaped file a Claude Code session writes for the strip."""
    try:
        status_file = Path(path).expanduser()
        payload = json.loads(status_file.read_text(encoding="utf-8"))
        updated_at = status_file.stat().st_mtime
    except FileNotFoundError:
        return _empty_status("waiting", "Waiting for a Claude Code session")
    except (OSError, ValueError):
        return _empty_status("unavailable", "Unable to read Claude Code status")
    return present_status(payload, now=now, updated_at=updated_at, source="Claude Code")


def unavailable_status(detail: str = "Codex rollout selection unavailable") -> dict[str, object]:
    """Build an unavailable status when a bound session cannot be resolved."""
    return _empty_status("unavailable", detail)
