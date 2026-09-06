"""Tests for bifrost.py: Codex SessionStart binding, status-line rendering,
and codex_command argv construction. Loads bifrost.py and statusline.py
directly from the repo root via importlib, matching
tests/test_codex_status.py, so behavior can never drift from an installed
copy.
"""

import errno
import fcntl
import io
import json
import os
import pty
import re
import select
import shlex
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import termios
import time
import tomllib
import unittest
from contextlib import redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BIFROST_PATH = ROOT / "bifrost.py"


def _load(name: str, path: Path):
    spec = spec_from_file_location(name, path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bifrost = _load("bifrost", BIFROST_PATH)
statusline = _load("statusline", ROOT / "statusline.py")


def _make_event(**overrides) -> dict:
    event = {
        "hook_event_name": "SessionStart",
        "session_id": "sess-abc123",
        "transcript_path": "/tmp/does-not-need-to-exist/rollout.jsonl",
        "model": "gpt-5-codex",
        "cwd": "/home/user/project",
    }
    event.update(overrides)
    return event


class BindSessionTests(unittest.TestCase):
    def setUp(self):
        self.state_dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.session_path = self.state_dir / "session.json"

    def test_valid_event_writes_only_allowed_fields_with_owner_only_mode(self):
        event = _make_event(prompt="must never be persisted", last_assistant_message="nor this")
        bifrost.bind_session(self.state_dir, event)

        written = json.loads(self.session_path.read_text())
        self.assertEqual(written, {
            "session_id": "sess-abc123",
            "transcript_path": "/tmp/does-not-need-to-exist/rollout.jsonl",
            "model": "gpt-5-codex",
            "cwd": "/home/user/project",
        })
        self.assertEqual(stat.S_IMODE(self.session_path.stat().st_mode), 0o600)

    def test_rejects_malformed_events_without_overwriting_prior_binding(self):
        good = _make_event()
        bifrost.bind_session(self.state_dir, good)
        before = self.session_path.read_text()

        bad_events = {
            "wrong_hook_type": _make_event(hook_event_name="Notification"),
            "missing_hook_type": {k: v for k, v in good.items() if k != "hook_event_name"},
            "empty_session_id": _make_event(session_id=""),
            "non_string_session_id": _make_event(session_id=42),
            "relative_transcript_path": _make_event(transcript_path="relative/rollout.jsonl"),
            "missing_transcript_path": {k: v for k, v in good.items() if k != "transcript_path"},
        }
        for name, bad_event in bad_events.items():
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    bifrost.bind_session(self.state_dir, bad_event)
                self.assertEqual(self.session_path.read_text(), before)

    def test_rejects_malformed_event_when_no_prior_binding_exists(self):
        with self.assertRaises(ValueError):
            bifrost.bind_session(self.state_dir, _make_event(hook_event_name="Notification"))
        self.assertFalse(self.session_path.exists())

    def test_does_not_execute_a_shell(self):
        with patch("subprocess.run") as run, patch("os.system") as system:
            bifrost.bind_session(self.state_dir, _make_event())
        run.assert_not_called()
        system.assert_not_called()


class StatusTextTests(unittest.TestCase):
    def setUp(self):
        self.state_dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.session_path = self.state_dir / "session.json"

    def test_missing_binding_reports_waiting(self):
        cases = [
            {"tmux": False, "now": None},
            {"tmux": True, "now": None},
            {"tmux": False, "now": 1_700_000_000},
        ]
        for kwargs in cases:
            with self.subTest(**kwargs):
                text = bifrost.status_text(self.state_dir, **kwargs)
                self.assertIn("waiting", text.lower())

    def test_malformed_binding_json_reports_unavailable_without_traceback(self):
        self.session_path.write_text("{not valid json")
        text = bifrost.status_text(self.state_dir)
        self.assertIn("unavailable", text.lower())
        self.assertNotIn("Traceback", text)

    def test_binding_missing_required_keys_reports_unavailable(self):
        self.session_path.write_text(json.dumps({"session_id": "sess-1"}))
        text = bifrost.status_text(self.state_dir)
        self.assertIn("unavailable", text.lower())

    def test_unreadable_transcript_reports_unavailable(self):
        missing_transcript = str(self.state_dir / "gone.jsonl")
        bifrost.bind_session(self.state_dir, _make_event(transcript_path=missing_transcript))
        text = bifrost.status_text(self.state_dir)
        self.assertIn("unavailable", text.lower())
        self.assertNotIn("Traceback", text)

    def test_valid_session_renders_real_gauges_from_bound_transcript(self):
        # Mirrors codex_status's own ReadRolloutTests wire fixtures (turn_context
        # + token_count event_msg) so this exercises the real adapter, not a
        # hand-rolled guess at its JSONL shape.
        now_ts = int(time.time())
        info = {
            "last_token_usage": {
                "total_tokens": 61000, "input_tokens": 60000,
                "output_tokens": 1000, "cached_input_tokens": 0,
            },
            "model_context_window": 100000,
        }
        rate_limits = {
            "primary": {"used_percent": 20.0, "window_minutes": 300, "resets_at": now_ts + 3600},
            "secondary": {"used_percent": 45.0, "window_minutes": 10080, "resets_at": now_ts + 3 * 86400},
        }
        transcript = self.state_dir / "rollout.jsonl"
        transcript.write_text(
            json.dumps({"type": "turn_context", "payload": {"model": "gpt-5-codex", "cwd": "/workspace/demo-project"}}) + "\n"
            + json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": info, "rate_limits": rate_limits}}) + "\n"
        )
        bifrost.bind_session(self.state_dir, _make_event(transcript_path=str(transcript)))

        text = bifrost.status_text(self.state_dir)

        self.assertNotIn("Traceback", text)
        self.assertIn("gpt-5-codex", text)
        self.assertIn(statusline.FULL_CELL, text)
        self.assertIn("61%", text)
        self.assertIn("20%", text)
        self.assertIn("45%", text)

    def test_supplements_bound_model_and_cwd_when_transcript_has_no_turn_context(self):
        # A real token_count event with no turn_context line anywhere in the
        # transcript, so read_rollout/normalize_snapshot run unmocked and the
        # model/cwd shown can only have come from bifrost's own binding.
        info = {
            "last_token_usage": {
                "total_tokens": 330, "input_tokens": 330,
                "output_tokens": 0, "cached_input_tokens": 0,
            },
            "model_context_window": 1000,
        }
        transcript = self.state_dir / "rollout.jsonl"
        transcript.write_text(
            json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": info, "rate_limits": None}}) + "\n"
        )
        bifrost.bind_session(self.state_dir, _make_event(
            transcript_path=str(transcript), model="bound-model", cwd="/bound/cwd"))

        text = bifrost.status_text(self.state_dir)

        self.assertIn("bound-model", text)
        self.assertIn("33%", text)
        self.assertIn(statusline.FULL_CELL, text)


class CodexCommandTests(unittest.TestCase):
    def test_includes_process_only_overrides_and_bypass_flag(self):
        result = bifrost.codex_command([], Path("/tmp/bifrost-state"))

        self.assertEqual(result[0], "codex")
        self.assertIn("--dangerously-bypass-hook-trust", result)
        c_values = [result[i + 1] for i, v in enumerate(result) if v == "-c"]
        self.assertIn("tui.status_line=[]", c_values)
        hook_entries = [v for v in c_values if v.startswith("hooks.SessionStart=")]
        self.assertEqual(len(hook_entries), 1)

    def test_session_start_hook_value_parses_as_toml_and_targets_exact_state_dir(self):
        state_dir = Path("/tmp/bifrost state with spaces")
        result = bifrost.codex_command([], state_dir)

        hook_entry = next(v for v in result if v.startswith("hooks.SessionStart="))
        doc = tomllib.loads(hook_entry)
        matcher = doc["hooks"]["SessionStart"][0]
        hook = matcher["hooks"][0]
        self.assertEqual(hook["type"], "command")
        argv = shlex.split(hook["command"])
        self.assertEqual(argv[-3:], ["bind", "--state", str(state_dir)])

    def test_preserves_user_args_as_literal_argv_without_shell_interpolation(self):
        state_dir = Path("/tmp/bifrost-state")
        user_args = ["--search", "a b; rm -rf /", "resume", "$(whoami)"]

        result = bifrost.codex_command(user_args, state_dir)

        for arg in user_args:
            self.assertIn(arg, result)
        for v in result:
            if v == "tui.status_line=[]" or v.startswith("hooks.SessionStart="):
                self.assertNotIn("rm -rf", v)

    def test_hook_value_round_trips_non_bmp_and_del_characters_through_toml(self):
        # json.dumps with ensure_ascii=True (the pre-fix behavior) encodes a
        # non-BMP codepoint as a UTF-16 surrogate pair, and TOML rejects a
        # lone surrogate half as an invalid Unicode scalar value; DEL
        # (U+007F) needs its own escape since TOML forbids the literal byte.
        state_dir = Path("/tmp/bifrost-\U0001F308-\x7f-state")

        result = bifrost.codex_command([], state_dir)

        hook_entry = next(v for v in result if v.startswith("hooks.SessionStart="))
        doc = tomllib.loads(hook_entry)
        command = doc["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        argv = shlex.split(command)
        self.assertEqual(argv[-1], str(state_dir))


class LaunchCodexTests(unittest.TestCase):
    @staticmethod
    def _launch_results():
        return [
            subprocess.CompletedProcess([], 0),  # tmux launch/client
            subprocess.CompletedProcess([], 1),  # no live Codex session
            subprocess.CompletedProcess([], 0),  # defensive kill-server
        ]

    def test_mouse_wheel_scrolls_tmux_history_instead_of_codex_input(self):
        state_dir = Path(tempfile.mkdtemp(prefix=bifrost.STATE_DIR_PREFIX))

        with patch.object(bifrost, "_require_executable"), patch.object(
            bifrost.tempfile, "mkdtemp", return_value=str(state_dir)
        ), patch.object(
            bifrost.subprocess, "run", side_effect=self._launch_results()
        ) as run, redirect_stdout(io.StringIO()):
            self.assertEqual(bifrost.launch_codex([]), 0)

        tmux_argv = run.call_args_list[0].args[0]
        mouse_index = tmux_argv.index("mouse")
        self.assertEqual(
            tmux_argv[mouse_index - 3:mouse_index + 2],
            [";", "set-option", "-g", "mouse", "on"],
        )

    def test_launch_uses_a_separate_tmux_server_and_private_config(self):
        state_dir = Path(tempfile.mkdtemp(prefix=bifrost.STATE_DIR_PREFIX))

        with patch.object(bifrost, "_require_executable"), patch.object(
            bifrost.tempfile, "mkdtemp", return_value=str(state_dir)
        ), patch.object(
            bifrost.subprocess, "run", side_effect=self._launch_results()
        ) as run, redirect_stdout(io.StringIO()):
            self.assertEqual(bifrost.launch_codex([]), 0)

        tmux_argv = run.call_args_list[0].args[0]
        self.assertEqual(tmux_argv[0:2], ["tmux", "-L"])
        self.assertRegex(tmux_argv[2], r"^bifrost-\d+-[0-9a-f]{8}$")
        self.assertEqual(tmux_argv[3:6], ["-f", "/dev/null", "start-server"])
        kill_argv = run.call_args_list[-1].args[0]
        self.assertEqual(kill_argv, ["tmux", "-L", tmux_argv[2], "kill-server"])

    def test_nested_launch_does_not_pass_outer_tmux_identity_to_private_server(self):
        state_dir = Path(tempfile.mkdtemp(prefix=bifrost.STATE_DIR_PREFIX))
        out = io.StringIO()

        with patch.dict(
            os.environ, {"TMUX": "/tmp/tmux-1000/default,1,0", "TMUX_PANE": "%7"}
        ), patch.object(bifrost, "_require_executable"), patch.object(
            bifrost.tempfile, "mkdtemp", return_value=str(state_dir)
        ), patch.object(
            bifrost.subprocess, "run", side_effect=self._launch_results()
        ) as run, redirect_stdout(out):
            self.assertEqual(bifrost.launch_codex([]), 0)

        launch_env = run.call_args_list[0].kwargs["env"]
        self.assertNotIn("TMUX", launch_env)
        self.assertNotIn("TMUX_PANE", launch_env)
        self.assertIn("outer tmux detected", out.getvalue())
        self.assertIn("Ctrl-b Ctrl-b [", out.getvalue())

    def test_scrollback_limit_is_set_before_the_initial_pane_is_created(self):
        state_dir = Path(tempfile.mkdtemp(prefix=bifrost.STATE_DIR_PREFIX))

        with patch.object(bifrost, "_require_executable"), patch.object(
            bifrost.tempfile, "mkdtemp", return_value=str(state_dir)
        ), patch.object(
            bifrost.subprocess, "run", side_effect=self._launch_results()
        ) as run, redirect_stdout(io.StringIO()):
            self.assertEqual(bifrost.launch_codex([]), 0)

        tmux_argv = run.call_args_list[0].args[0]
        history_index = tmux_argv.index("history-limit")
        session_index = tmux_argv.index("new-session")
        self.assertLess(history_index, session_index)
        self.assertEqual(tmux_argv[history_index + 1], str(bifrost.TMUX_HISTORY_LIMIT))

    def test_cleanup_hook_is_installed_before_codex_can_exit(self):
        state_dir = Path(tempfile.mkdtemp(prefix=bifrost.STATE_DIR_PREFIX))

        with patch.object(bifrost, "_require_executable"), patch.object(
            bifrost.tempfile, "mkdtemp", return_value=str(state_dir)
        ), patch.object(
            bifrost.subprocess, "run", side_effect=self._launch_results()
        ) as run, redirect_stdout(io.StringIO()):
            self.assertEqual(bifrost.launch_codex(["--version"]), 0)

        tmux_argv = run.call_args_list[0].args[0]
        hook_index = tmux_argv.index("session-closed")
        session_index = tmux_argv.index("new-session")
        self.assertLess(hook_index, session_index)

    def test_status_bar_inherits_terminal_background_and_foreground(self):
        state_dir = Path(tempfile.mkdtemp(prefix=bifrost.STATE_DIR_PREFIX))

        with patch.object(bifrost, "_require_executable"), patch.object(
            bifrost.tempfile, "mkdtemp", return_value=str(state_dir)
        ), patch.object(
            bifrost.subprocess, "run", side_effect=self._launch_results()
        ) as run, redirect_stdout(io.StringIO()):
            self.assertEqual(bifrost.launch_codex([]), 0)

        tmux_argv = run.call_args_list[0].args[0]
        style_index = tmux_argv.index("status-style")
        self.assertEqual(
            tmux_argv[style_index - 3:style_index + 2],
            [";", "set-option", "-g", "status-style", "bg=default,fg=default"],
        )

    def test_state_dir_creation_failure_exits_cleanly_without_launching_tmux(self):
        out = io.StringIO()
        with patch.object(
            bifrost.tempfile, "mkdtemp", side_effect=OSError(28, "No space left on device")
        ), patch.object(bifrost.subprocess, "run") as run, redirect_stdout(out):
            with self.assertRaises(SystemExit) as ctx:
                bifrost.launch_codex(["--version"])

        self.assertIn("bifrost: failed to prepare state directory", str(ctx.exception))
        run.assert_not_called()
        self.assertNotIn("launching Codex in tmux", out.getvalue())


class LaunchCodexTmuxLifecycleTests(unittest.TestCase):
    """F02: the session-closed hook that cleans up after a detached Codex
    session must survive a TMPDIR containing shell-hostile characters
    (quote, dollar, space), not just a plain path -- a corrupted `--state`
    argument in the hook otherwise leaves the tmux state directory (and its
    server) behind once nobody is left attached to run cleanup any other
    way. Adapted from the registered repro's minimal real-tmux fixture."""

    FAKE_CODEX_SRC = (
        "#!/usr/bin/env python3\n"
        "print('FAKE_CODEX_READY', flush=True)\n"
        "input()\n"
    )

    @unittest.skipUnless(shutil.which("tmux"), "tmux not on PATH")
    def test_hostile_tmpdir_path_is_cleaned_up_after_detach_then_close(self):
        scratch = Path(self.enterContext(tempfile.TemporaryDirectory()))
        tmpdir_root = scratch / 'lifecycle $ "quoted" dir'
        tmpdir_root.mkdir(parents=True)
        bindir = tmpdir_root / "fakebin"
        bindir.mkdir()
        fake_codex = bindir / "codex"
        fake_codex.write_text(self.FAKE_CODEX_SRC)
        fake_codex.chmod(0o700)

        # Short, relative TMUX_TMPDIR (cwd=scratch) keeps tmux's own AF_UNIX
        # socket path string under the kernel's sockaddr_un length limit
        # regardless of how long the system temp root happens to be. tmux
        # does not mkdir this directory itself, so the harness has to create
        # it under scratch before launch or the server never comes up.
        env = dict(os.environ, TMUX_TMPDIR="t")
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)
        env["TERM"] = "xterm-256color"
        env["TMPDIR"] = str(tmpdir_root)
        env["PATH"] = str(bindir) + os.pathsep + os.environ["PATH"]
        (scratch / env["TMUX_TMPDIR"]).mkdir(parents=True, exist_ok=True)

        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 180, 0, 0))

        child = subprocess.Popen(
            [sys.executable, str(BIFROST_PATH), "codex", "smoke"],
            stdin=slave, stdout=slave, stderr=slave, env=env, cwd=str(scratch),
            start_new_session=True,
        )
        os.close(slave)
        output = b""
        server = None
        state_dir = None
        try:
            end = time.time() + 8
            while time.time() < end:
                if select.select([master], [], [], 0.1)[0]:
                    # On Linux, reading a PTY master after every slave fd has
                    # closed raises EIO instead of returning b"" -- that is
                    # this kernel's EOF signal for a PTY, not a real read
                    # failure, so it ends the poll loop like any other EOF.
                    # Any other errno is a genuine failure and must propagate.
                    try:
                        chunk = os.read(master, 65536)
                    except OSError as exc:
                        if exc.errno != errno.EIO:
                            raise
                        break
                    if not chunk:
                        break
                    output += chunk
                match = re.search(rb"server '(bifrost-[^']+)'", output)
                if match:
                    server = match.group(1).decode()
                if b"FAKE_CODEX_READY" in output and server:
                    break

            self.assertTrue(
                server and b"FAKE_CODEX_READY" in output,
                f"launch never reached a ready fake-codex prompt: {output[-1000:]!r}",
            )

            mouse = subprocess.run(
                ["tmux", "-L", server, "show-options", "-gv", "mouse"],
                capture_output=True, text=True, check=True, cwd=str(scratch), env=env,
            ).stdout.strip()
            history_limit = subprocess.run(
                ["tmux", "-L", server, "show-options", "-gv", "history-limit"],
                capture_output=True, text=True, check=True, cwd=str(scratch), env=env,
            ).stdout.strip()
            self.assertEqual(mouse, "on")
            self.assertEqual(history_limit, str(bifrost.TMUX_HISTORY_LIMIT))

            state_dirs = list(tmpdir_root.glob("bifrost-codex-*"))
            self.assertTrue(state_dirs, f"no bifrost-codex-* state dir created under {tmpdir_root}")
            state_dir = state_dirs[0]

            # Detach (Ctrl-b d): bifrost's own launching process returns and
            # exits now, since the session is still alive. Cleanup for this
            # launch is now the session-closed hook's job alone.
            os.write(master, b"\x02d")
            child.wait(timeout=8)

            detached_alive = subprocess.run(
                ["tmux", "-L", server, "has-session", "-t", "codex"],
                capture_output=True, cwd=str(scratch), env=env,
            ).returncode == 0
            self.assertTrue(detached_alive, "session did not survive the detach as expected")

            # Close the session for real, with nobody attached and bifrost's
            # launching process already gone.
            subprocess.run(
                ["tmux", "-L", server, "send-keys", "-t", "codex", "Enter"],
                check=True, cwd=str(scratch), env=env,
            )
            time.sleep(2)  # let the session-closed hook's run-shell command finish

            alive = subprocess.run(
                ["tmux", "-L", server, "has-session", "-t", "codex"],
                capture_output=True, cwd=str(scratch), env=env,
            ).returncode == 0
            self.assertFalse(alive, "tmux session still reports alive after normal exit")
            self.assertFalse(state_dir.exists(), f"state directory was not cleaned up: {state_dir}")
        finally:
            if server:
                subprocess.run(
                    ["tmux", "-L", server, "kill-server"],
                    capture_output=True, cwd=str(scratch), env=env,
                )
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
            os.close(master)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.state_dir = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def _run(self, args, input_text=None):
        return subprocess.run(
            [sys.executable, str(BIFROST_PATH), *args],
            input=input_text, capture_output=True, text=True, timeout=5,
        )

    def test_status_missing_binding_prints_a_single_line(self):
        result = self._run(["status", "--state", str(self.state_dir)])
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("waiting", lines[0].lower())

    def test_bind_consumes_stdin_json_and_writes_file_without_report_noise(self):
        result = self._run(["bind", "--state", str(self.state_dir)], input_text=json.dumps(_make_event()))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")
        written = json.loads((self.state_dir / "session.json").read_text())
        self.assertEqual(written["session_id"], "sess-abc123")

    def test_bind_rejects_invalid_stdin_json_honestly(self):
        result = self._run(["bind", "--state", str(self.state_dir)], input_text="not json")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)

    def test_bind_reports_oserror_from_blocked_state_path_without_traceback(self):
        # mkdir() under a plain file -> NotADirectoryError, an OSError
        # subclass that _cmd_bind must handle as cleanly as it already
        # handles ValueError and invalid JSON.
        blocker = Path(self.enterContext(tempfile.TemporaryDirectory())) / "blocker"
        blocker.write_text("this is a plain file, not a directory\n")
        state_dir = blocker / "nested-state"

        result = self._run(["bind", "--state", str(state_dir)], input_text=json.dumps(_make_event()))

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("bifrost:", result.stderr)


class MainDispatchTests(unittest.TestCase):
    """F01: bifrost's own argparse layer must never see option-first Codex
    argv (e.g. `--version`, `--model VALUE`) before it reaches launch_codex,
    since argparse's own REMAINDER handling rejects an option-looking first
    token as "unrecognized arguments". Stubs launch_codex itself -- the real
    process boundary between bifrost's CLI and actually running Codex -- so
    this exercises the real main()/argparse dispatch path unmocked."""

    def test_codex_subcommand_forwards_raw_argv_before_argparse_runs(self):
        cases = {
            "option_first_version": (["codex", "--version"], ["--version"]),
            "option_first_value": (["codex", "--model", "gpt-5"], ["--model", "gpt-5"]),
            "help_flag": (["codex", "--help"], ["--help"]),
            "double_dash_passthrough": (["codex", "--", "--model", "gpt-5"], ["--", "--model", "gpt-5"]),
        }
        for name, (argv, expected_codex_args) in cases.items():
            with self.subTest(name=name):
                with patch.object(bifrost, "launch_codex", return_value=0) as launch:
                    result = bifrost.main(argv)
                launch.assert_called_once_with(expected_codex_args)
                self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
