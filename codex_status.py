#!/usr/bin/env python3
"""
Codex CLI parity layer for bifrost-statusline.

Translates a Codex CLI rollout snapshot into the same payload shape Claude
Code feeds statusline.py, so the existing renderer can draw both without a
second color/bar implementation. Also reads Codex rollout JSONL files and
converts ANSI SGR codes into tmux-native format strings for launchers that
embed this statusline in a tmux status bar.
"""

from __future__ import annotations

import json
import math
import os
import re
import time

_WINDOW_NAMES = {300: "five_hour", 10080: "seven_day"}

# CSI syntax per ECMA-48: ESC "[", then parameter bytes 0x30-0x3F (digits
# plus ; : < = > ?), intermediate bytes 0x20-0x2F, and one final byte
# 0x40-0x7E. Matching the full range -- not just [0-9;] plus a letter --
# is what lets an unsupported or private sequence (e.g. `\x1b[?25h`) be
# consumed whole instead of leaving its tail behind as visible garbage.
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9:;<=>?]*[ -/]*[@-~]")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_CSI_RE = re.compile(r"\x1b\[([0-9:;<=>?]*)([ -/]*)([@-~])")


def _finite_nonneg_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and value >= 0


def _sanitize_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    stripped = _ANSI_ESCAPE_RE.sub("", value)
    return _CONTROL_RE.sub("", stripped)


def _normalize_context_window(info: object) -> dict:
    info = info if isinstance(info, dict) else {}
    window_raw = info.get("model_context_window")
    window = window_raw if _finite_nonneg_number(window_raw) and window_raw > 0 else None

    usage = info.get("last_token_usage")
    usage = usage if isinstance(usage, dict) else {}
    total_raw = usage.get("total_tokens")
    total = total_raw if _finite_nonneg_number(total_raw) else None

    used_percentage = total / window * 100 if window is not None and total is not None else None

    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    has_valid_split = (
        total is not None
        and _finite_nonneg_number(input_tokens)
        and _finite_nonneg_number(output_tokens)
        and input_tokens + output_tokens == total
    )
    if has_valid_split:
        total_input_tokens, total_output_tokens = input_tokens, output_tokens
    elif total is not None:
        total_input_tokens, total_output_tokens = total, 0
    else:
        total_input_tokens, total_output_tokens = 0, 0

    return {
        "used_percentage": used_percentage,
        "context_window_size": window,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
    }


def _normalize_window(entry: dict, now: float) -> dict | None:
    used_percentage = entry.get("used_percent")
    if not _finite_nonneg_number(used_percentage):
        return None
    resets_at_raw = entry.get("resets_at")
    resets_at = int(resets_at_raw) if _finite_nonneg_number(resets_at_raw) else None
    stale = resets_at is not None and resets_at <= now
    return {"used_percentage": used_percentage, "resets_at": resets_at, "stale": stale}


def _normalize_rate_limits(raw: object, now: float) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    windows: dict = {}
    for entry in raw.values():
        if not isinstance(entry, dict):
            continue
        name = _WINDOW_NAMES.get(entry.get("window_minutes"))
        if name is None:
            continue
        window = _normalize_window(entry, now)
        if window is not None:
            windows[name] = window
    return windows


def normalize_snapshot(snapshot: dict, now: float | None = None) -> dict:
    """Convert a Codex CLI status snapshot into the Claude Code payload shape.

    statusline.py's renderers key off Claude's schema, so this is the one
    place Codex's field names (last_token_usage, used_percent, ...) get
    translated into it. `now` defaults to the wall clock; pass it explicitly
    in tests so staleness checks are deterministic.
    """
    now = time.time() if now is None else now
    snapshot = snapshot if isinstance(snapshot, dict) else {}

    return {
        "model": {"display_name": _sanitize_text(snapshot.get("model"))},
        "cwd": _sanitize_text(snapshot.get("cwd")),
        "context_window": _normalize_context_window(snapshot.get("info")),
        "rate_limits": _normalize_rate_limits(snapshot.get("rate_limits"), now),
    }


def read_rollout(path: str, max_bytes: int = 1_048_576) -> dict:
    """Parse the tail of a Codex rollout JSONL file into a status snapshot.

    Reads at most the last `max_bytes` bytes so a large rollout never costs a
    full-file read on every statusline refresh. A missing or unreadable file
    raises OSError as-is; the caller decides how to show "unavailable".
    """
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        start = max(0, size - max_bytes)
        aligned = start == 0
        if not aligned:
            fh.seek(start - 1)
            aligned = fh.read(1) == b"\n"
        fh.seek(start)
        # Bound the physical read to the window captured above (size - start)
        # rather than reading to whatever EOF happens to be by the time this
        # runs, so a writer appending to the file mid-read can never push the
        # result past max_bytes.
        chunk = fh.read(size - start)

    lines = chunk.split(b"\n")
    if lines:
        lines.pop()  # trailing empty marker, or an incomplete final record
    if not aligned and lines:
        lines.pop(0)  # first line may be truncated at the front by the seek

    model = cwd = info = rate_limits = None
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        if record.get("type") == "turn_context":
            if payload.get("model") is not None:
                model = payload["model"]
            if payload.get("cwd") is not None:
                cwd = payload["cwd"]
        elif record.get("type") == "event_msg" and payload.get("type") == "token_count":
            if payload.get("info") is not None:
                info = payload["info"]
            if payload.get("rate_limits") is not None:
                rate_limits = payload["rate_limits"]

    snapshot: dict = {}
    if model is not None:
        snapshot["model"] = model
    if cwd is not None:
        snapshot["cwd"] = cwd
    if info is not None:
        snapshot["info"] = info
    if rate_limits is not None:
        snapshot["rate_limits"] = rate_limits
    return snapshot


def _sgr_to_tmux(params: str) -> str:
    parts = params.split(";") if params else [""]
    if parts in ([""], ["0"]):
        return "#[default]"
    if parts == ["1"]:
        return "#[bold]"
    if parts == ["2"]:
        return "#[dim]"
    if len(parts) == 5 and parts[0] == "38" and parts[1] == "2":
        try:
            r, g, b = (int(p) for p in parts[2:5])
        except ValueError:
            return ""
        if all(0 <= channel <= 255 for channel in (r, g, b)):
            return f"#[fg=#{r:02x}{g:02x}{b:02x}]"
    return ""


def _translate_csi(match: re.Match) -> str:
    params, intermediate, final = match.group(1), match.group(2), match.group(3)
    # Only a plain, non-private SGR sequence (final byte 'm', no intermediate
    # byte, no private parameter prefix) is a color/style code we recognize;
    # every other CSI sequence -- cursor moves, private DEC modes, anything
    # with an unfamiliar prefix -- is consumed and dropped, never passed through.
    if final == "m" and not intermediate and not any(ch in "<=>?" for ch in params):
        return _sgr_to_tmux(params)
    return ""


def ansi_to_tmux(text: str) -> str:
    """Translate ANSI SGR color/style codes into tmux `#[...]` directives.

    Escapes every literal '#' first, so untrusted status text (a model name,
    a git branch) can never spell out a real tmux directive; only sequences
    generated here keep a single '#'. Unsupported escapes and raw control
    characters are dropped rather than passed through.
    """
    escaped = text.replace("#", "##")
    translated = _CSI_RE.sub(_translate_csi, escaped)
    return _CONTROL_RE.sub("", translated)
