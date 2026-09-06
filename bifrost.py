#!/usr/bin/env python3
"""
Codex CLI launcher and tmux status renderer for bifrost-statusline.

Binds a Codex SessionStart hook event to on-disk state, renders that state
through the shared `statusline` module, and launches Codex inside an
isolated tmux server whose bottom status line calls back into this script.
See README.md for the exact hook and tmux wiring this replaces.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import codex_status
import statusline

STATE_DIR_PREFIX = "bifrost-codex-"
_REQUIRED_STRING_FIELDS = ("session_id", "transcript_path", "model", "cwd")


def bind_session(state_dir: Path, event: object) -> None:
    """Validate a Codex SessionStart event and persist it as session.json.

    Only session_id, transcript_path, model, and cwd are ever written; a
    prompt or any other field on the event is discarded. An invalid event
    raises ValueError and never touches an existing binding.
    """
    if not isinstance(event, dict) or event.get("hook_event_name") != "SessionStart":
        raise ValueError("event is not a SessionStart hook payload")

    session_id = event.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session_id must be a nonempty string")

    transcript_path = event.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path or not os.path.isabs(transcript_path):
        raise ValueError("transcript_path must be an absolute, nonempty string")

    model = event.get("model")
    if not isinstance(model, str):
        raise ValueError("model must be a string")

    cwd = event.get("cwd")
    if not isinstance(cwd, str):
        raise ValueError("cwd must be a string")

    payload = {
        "session_id": session_id,
        "transcript_path": transcript_path,
        "model": model,
        "cwd": cwd,
    }

    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / "session.json"
    fd, tmp_name = tempfile.mkstemp(dir=str(state_dir), prefix=".session-", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(payload, stream)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _rendered(text: str, tmux: bool) -> str:
    return codex_status.ansi_to_tmux(text) if tmux else text


def status_text(state_dir: Path, tmux: bool = False, now: float | None = None) -> str:
    """Render one status line from whatever is bound in `state_dir`.

    Every failure mode (no binding yet, a malformed binding, an unreadable
    transcript) returns a plain descriptive line instead of raising, so a
    tmux status-format callback never shows a traceback in the status bar.
    """
    state_dir = Path(state_dir)
    session_path = state_dir / "session.json"
    if not session_path.exists():
        return _rendered("waiting for Codex session...", tmux)

    try:
        binding = json.loads(session_path.read_text())
    except (OSError, ValueError):
        return _rendered("Codex session binding unavailable", tmux)

    if not isinstance(binding, dict):
        return _rendered("Codex session binding unavailable", tmux)

    values = {name: binding.get(name) for name in _REQUIRED_STRING_FIELDS}
    if not all(isinstance(v, str) for v in values.values()) or not values["session_id"] or not values["transcript_path"]:
        return _rendered("Codex session binding unavailable", tmux)

    try:
        snapshot = codex_status.read_rollout(values["transcript_path"])
    except OSError:
        return _rendered("Codex transcript unavailable", tmux)

    normalized = codex_status.normalize_snapshot(snapshot, now=now)
    if not (normalized.get("model") or {}).get("display_name"):
        normalized = {**normalized, "model": {"display_name": values["model"]}}
    if not normalized.get("cwd"):
        normalized = {**normalized, "cwd": values["cwd"]}

    return _rendered(statusline.render(normalized), tmux)


def _toml_basic_string(value: str) -> str:
    """Encode `value` as a TOML basic string.

    json.dumps already matches TOML basic-string quoting: the same
    backslash, quote-mark, and control-character escapes. ensure_ascii=False
    keeps Unicode scalars literal instead of emitting invalid surrogate
    escapes, and DEL (U+007F) needs one escape of its own since JSON's own
    spec does not require it but TOML forbids the literal byte.
    """
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def codex_command(args: list[str], state_dir: Path) -> list[str]:
    """Build the argv that launches Codex with Bifrost's hook wired in.

    The native footer is disabled (`tui.status_line=[]`) since tmux owns
    the status bar instead, and `--dangerously-bypass-hook-trust` is scoped
    to this one invocation so the injected SessionStart hook actually runs.
    """
    bifrost_path = Path(__file__).resolve()
    bind_argv = [sys.executable, str(bifrost_path), "bind", "--state", str(state_dir)]
    hook_command = shlex.join(bind_argv)
    # Codex's real SessionStart shape: a list of matchers, each a table of
    # {type, command} hook entries. _toml_basic_string quotes the command as
    # a safe TOML basic string (same trick install-codex.py uses for arrays).
    hook_entry = "[{hooks=[{type=\"command\",command=" + _toml_basic_string(hook_command) + "}]}]"
    hook_value = "hooks.SessionStart=" + hook_entry

    return [
        "codex",
        "--dangerously-bypass-hook-trust",
        "-c", "tui.status_line=[]",
        "-c", hook_value,
        *args,
    ]


def cleanup_state(state_dir: Path) -> None:
    """Remove a Bifrost-owned tmux state directory, and nothing else.

    Refuses to act unless the path lives directly under the system temp
    directory and carries this launcher's own prefix, so a corrupted
    argument can never trigger a recursive delete of an arbitrary path.
    """
    resolved = Path(state_dir).resolve()
    tmp_root = Path(tempfile.gettempdir()).resolve()
    if resolved.parent != tmp_root or not resolved.name.startswith(STATE_DIR_PREFIX):
        return
    shutil.rmtree(resolved, ignore_errors=True)


def _require_executable(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(f"bifrost: '{name}' not found on PATH; install it and retry.")


def _tmux_session_alive(server_name: str) -> bool:
    check = subprocess.run(
        ["tmux", "-L", server_name, "has-session", "-t", "codex"],
        capture_output=True,
    )
    return check.returncode == 0


def launch_codex(codex_args: list[str]) -> int:
    """Run Codex inside an isolated tmux server with a live Bifrost footer.

    The server is named per-launch (-L) and started with -f /dev/null, so
    it never touches the user's default tmux server or ~/.tmux.conf. State
    survives a detach; a session-closed hook cleans it up once Codex exits,
    whether that happens in the foreground or after the user detached.
    """
    _require_executable("tmux")
    _require_executable("codex")

    state_dir: Path | None = None
    try:
        state_dir = Path(tempfile.mkdtemp(prefix=STATE_DIR_PREFIX))
        os.chmod(state_dir, 0o700)
    except OSError as exc:
        if state_dir is not None:
            cleanup_state(state_dir)
        raise SystemExit(f"bifrost: failed to prepare state directory: {exc}") from exc

    server_name = f"bifrost-{os.getpid()}-{uuid.uuid4().hex[:8]}"

    bifrost_path = Path(__file__).resolve()
    status_cmd = shlex.join(
        [sys.executable, str(bifrost_path), "status", "--state", str(state_dir), "--tmux"])
    codex_cmd = shlex.join(codex_command(codex_args, state_dir))

    # exit-empty defaults to on, which tears the server down the instant the
    # last session closes -- before the session-closed hook's cleanup below
    # gets a chance to run. Off keeps the (isolated) server alive long enough
    # for that hook to remove our state dir and kill the server itself.
    closed_cmd = shlex.join(
        [sys.executable, str(bifrost_path), "_cleanup", "--state", str(state_dir)])
    closed_cmd += "; " + shlex.join(["tmux", "-L", server_name, "kill-server"])
    # shlex.quote wraps the whole shell command in single quotes, which tmux's
    # own command parser treats as a literal run of bytes (no #{...} format
    # expansion, no re-splitting on spaces), so paths with spaces/$/quotes in
    # TMPDIR or the install directory survive both tmux and the shell intact.
    quoted_closed_cmd = shlex.quote(closed_cmd)

    env = dict(os.environ)
    if env.get("TMUX"):
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)

    tmux_cmd = [
        "tmux", "-L", server_name, "-f", "/dev/null",
        "new-session", "-s", "codex", codex_cmd,
        ";", "set-option", "-g", "exit-empty", "off",
        ";", "set-option", "-g", "status-position", "bottom",
        ";", "set-option", "-g", "status-interval", "1",
        ";", "set-option", "-g", "status-format[0]", f"#({status_cmd})",
        ";", "set-option", "-g", "default-terminal", "tmux-256color",
        ";", "set-option", "-ga", "terminal-overrides", ",*:Tc",
        ";", "set-hook", "-g", "session-closed", f"run-shell {quoted_closed_cmd}",
    ]

    print(f"bifrost: launching Codex in tmux (server '{server_name}')")
    print(f"bifrost: if you detach, reconnect with: tmux -L {server_name} attach -t codex")

    try:
        result = subprocess.run(tmux_cmd, env=env)
    except OSError as exc:
        cleanup_state(state_dir)
        raise SystemExit(f"bifrost: failed to launch tmux: {exc}") from exc

    if _tmux_session_alive(server_name):
        print(f"bifrost: session detached and still running. State kept at {state_dir}.")
        print(f"bifrost: reattach with: tmux -L {server_name} attach -t codex")
        return result.returncode

    cleanup_state(state_dir)
    return result.returncode


def _cmd_status(args: argparse.Namespace) -> int:
    print(status_text(Path(args.state), tmux=args.tmux))
    return 0


def _cmd_bind(args: argparse.Namespace) -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"bifrost: invalid bind input: {exc}", file=sys.stderr)
        return 1
    try:
        bind_session(Path(args.state), event)
    except (ValueError, OSError) as exc:
        print(f"bifrost: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_codex(args: argparse.Namespace) -> int:
    return launch_codex(args.codex_args)


def _cmd_cleanup(args: argparse.Namespace) -> int:
    cleanup_state(Path(args.state))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bifrost", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="Print one rendered status line.")
    p_status.add_argument("--state", required=True)
    p_status.add_argument("--tmux", action="store_true")
    p_status.set_defaults(func=_cmd_status)

    p_bind = sub.add_parser("bind", help="Read a SessionStart event from stdin and bind it.")
    p_bind.add_argument("--state", required=True)
    p_bind.set_defaults(func=_cmd_bind)

    p_codex = sub.add_parser("codex", help="Launch Codex inside a Bifrost tmux session.")
    p_codex.add_argument("codex_args", nargs=argparse.REMAINDER)
    p_codex.set_defaults(func=_cmd_codex)

    p_cleanup = sub.add_parser("_cleanup", help=argparse.SUPPRESS)
    p_cleanup.add_argument("--state", required=True)
    p_cleanup.set_defaults(func=_cmd_cleanup)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch bifrost's CLI, forwarding raw `codex` argv before argparse runs."""
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "codex":
        return launch_codex(raw[1:])
    parser = _build_parser()
    args = parser.parse_args(raw)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
