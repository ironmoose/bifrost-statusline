"""Stdlib-only tests for codex_status.py (Codex CLI statusline parity).

Loads codex_status.py and statusline.py straight from the repo root, not a
package import, matching tests/test_statusline.py, so behavior can never
drift from an installed copy.
"""

import copy
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODEX_STATUS_PATH = os.path.join(ROOT, "codex_status.py")
STATUSLINE_PATH = os.path.join(ROOT, "statusline.py")


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


codex_status = _load("codex_status", CODEX_STATUS_PATH)
statusline = _load("statusline", STATUSLINE_PATH)


# ---------------------------------------------------------------------------
# Shared fixtures for normalize_snapshot
# ---------------------------------------------------------------------------
_LAST_TOKEN_USAGE = {
    "total_tokens": 4200,
    "input_tokens": 4000,
    "output_tokens": 200,
    "cached_input_tokens": 1500,
}
# Deliberately far from _LAST_TOKEN_USAGE so a test that accidentally reads
# cumulative totals instead of the last-turn snapshot fails loudly.
_TOTAL_TOKEN_USAGE = {
    "total_tokens": 980000,
    "input_tokens": 900000,
    "output_tokens": 80000,
    "cached_input_tokens": 400000,
}
_MODEL_CONTEXT_WINDOW = 128000

_RATE_LIMITS = {
    "primary": {"used_percent": 12.5, "window_minutes": 300, "resets_at": 1700003600},
    "secondary": {"used_percent": 40.0, "window_minutes": 10080, "resets_at": 1700007200},
}
_NOW = 1700000000

_BASE_SNAPSHOT = {
    "model": "gpt-5-codex",
    "cwd": "/workspace/demo-project",
    "info": {
        "last_token_usage": dict(_LAST_TOKEN_USAGE),
        "total_token_usage": dict(_TOTAL_TOKEN_USAGE),
        "model_context_window": _MODEL_CONTEXT_WINDOW,
    },
    "rate_limits": copy.deepcopy(_RATE_LIMITS),
}


def _snapshot(**overrides) -> dict:
    """A deep-copied baseline Codex snapshot with top-level keys overridden."""
    snap = copy.deepcopy(_BASE_SNAPSHOT)
    snap.update(overrides)
    return snap


class NormalizeSnapshotContextTests(unittest.TestCase):
    def test_percentage_is_raw_last_turn_occupancy(self):
        result = codex_status.normalize_snapshot(_snapshot(), now=_NOW)
        expected = 4200 / 128000 * 100
        self.assertAlmostEqual(result["context_window"]["used_percentage"], expected, places=6)

    def test_percentage_uses_last_usage_not_cumulative_totals(self):
        # If this used total_token_usage instead of last_token_usage, the
        # result would be near 765%, not ~3.28%.
        result = codex_status.normalize_snapshot(_snapshot(), now=_NOW)
        self.assertLess(result["context_window"]["used_percentage"], 10)

    def test_numerator_sums_to_last_total_without_double_counting_cache(self):
        ctx = codex_status.normalize_snapshot(_snapshot(), now=_NOW)["context_window"]
        self.assertEqual(ctx["total_input_tokens"], 4000)
        self.assertEqual(ctx["total_output_tokens"], 200)
        self.assertEqual(ctx["total_input_tokens"] + ctx["total_output_tokens"], 4200)

    def test_context_window_size_reflects_model_context_window(self):
        ctx = codex_status.normalize_snapshot(_snapshot(), now=_NOW)["context_window"]
        self.assertEqual(ctx["context_window_size"], _MODEL_CONTEXT_WINDOW)

    def test_zero_usage_is_a_valid_percentage_not_unknown(self):
        snap = _snapshot()
        snap["info"]["last_token_usage"] = {
            "total_tokens": 0, "input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0,
        }
        ctx = codex_status.normalize_snapshot(snap, now=_NOW)["context_window"]
        self.assertEqual(ctx["used_percentage"], 0.0)

    def test_usage_above_window_size_is_not_clamped(self):
        snap = _snapshot()
        snap["info"]["last_token_usage"] = {
            "total_tokens": 130000, "input_tokens": 129000, "output_tokens": 1000, "cached_input_tokens": 0,
        }
        pct = codex_status.normalize_snapshot(snap, now=_NOW)["context_window"]["used_percentage"]
        self.assertGreater(pct, 100)

    def test_missing_model_context_window_yields_unknown_percentage(self):
        snap = _snapshot()
        del snap["info"]["model_context_window"]
        ctx = codex_status.normalize_snapshot(snap, now=_NOW)["context_window"]
        self.assertIsNone(ctx["used_percentage"])

    def test_missing_last_token_usage_yields_unknown_percentage(self):
        snap = _snapshot()
        del snap["info"]["last_token_usage"]
        ctx = codex_status.normalize_snapshot(snap, now=_NOW)["context_window"]
        self.assertIsNone(ctx["used_percentage"])


class NormalizeSnapshotInvalidNumericTests(unittest.TestCase):
    """Bool, non-finite, and negative numeric values must not crash and must
    not silently masquerade as valid usage figures."""

    _BAD_VALUES = (True, False, float("nan"), float("inf"), float("-inf"), -5)

    def test_bad_model_context_window_yields_unknown_percentage(self):
        for bad in self._BAD_VALUES:
            with self.subTest(bad=bad):
                snap = _snapshot()
                snap["info"]["model_context_window"] = bad
                ctx = codex_status.normalize_snapshot(snap, now=_NOW)["context_window"]
                self.assertIsNone(ctx["used_percentage"])

    def test_bad_last_total_tokens_yields_unknown_percentage(self):
        for bad in self._BAD_VALUES:
            with self.subTest(bad=bad):
                snap = _snapshot()
                snap["info"]["last_token_usage"]["total_tokens"] = bad
                ctx = codex_status.normalize_snapshot(snap, now=_NOW)["context_window"]
                self.assertIsNone(ctx["used_percentage"])

    def test_bad_rate_limit_used_percent_is_rejected_gracefully(self):
        for bad in self._BAD_VALUES:
            with self.subTest(bad=bad):
                snap = _snapshot()
                snap["rate_limits"]["primary"]["used_percent"] = bad
                result = codex_status.normalize_snapshot(snap, now=_NOW)
                window = result.get("rate_limits", {}).get("five_hour")
                self.assertTrue(window is None or window.get("used_percentage") is None)


class NormalizeSnapshotRateLimitTests(unittest.TestCase):
    def test_maps_five_hour_and_seven_day_by_window_minutes(self):
        result = codex_status.normalize_snapshot(_snapshot(), now=_NOW)
        five_hour = result["rate_limits"]["five_hour"]
        seven_day = result["rate_limits"]["seven_day"]
        self.assertEqual(five_hour["used_percentage"], 12.5)
        self.assertEqual(five_hour["resets_at"], 1700003600)
        self.assertEqual(seven_day["used_percentage"], 40.0)
        self.assertEqual(seven_day["resets_at"], 1700007200)

    def test_swapped_primary_secondary_positions_still_map_correctly(self):
        snap = _snapshot()
        snap["rate_limits"] = {
            "primary": {"used_percent": 40.0, "window_minutes": 10080, "resets_at": 1700007200},
            "secondary": {"used_percent": 12.5, "window_minutes": 300, "resets_at": 1700003600},
        }
        result = codex_status.normalize_snapshot(snap, now=_NOW)
        self.assertEqual(result["rate_limits"]["five_hour"]["used_percentage"], 12.5)
        self.assertEqual(result["rate_limits"]["seven_day"]["used_percentage"], 40.0)

    def test_unknown_window_duration_is_omitted(self):
        snap = _snapshot()
        snap["rate_limits"] = {
            "primary": {"used_percent": 50.0, "window_minutes": 60, "resets_at": 1700003600},
        }
        result = codex_status.normalize_snapshot(snap, now=_NOW)
        self.assertNotIn("five_hour", result.get("rate_limits", {}))
        self.assertNotIn("seven_day", result.get("rate_limits", {}))

    def test_zero_used_percent_is_valid_not_unknown(self):
        snap = _snapshot()
        snap["rate_limits"]["primary"]["used_percent"] = 0.0
        result = codex_status.normalize_snapshot(snap, now=_NOW)
        self.assertEqual(result["rate_limits"]["five_hour"]["used_percentage"], 0.0)

    def test_reset_at_or_before_now_marks_window_stale(self):
        for resets_at in (_NOW - 3600, _NOW):
            with self.subTest(resets_at=resets_at):
                snap = _snapshot()
                snap["rate_limits"]["primary"]["resets_at"] = resets_at
                window = codex_status.normalize_snapshot(snap, now=_NOW)["rate_limits"]["five_hour"]
                self.assertIs(window["stale"], True)

    def test_future_reset_at_is_not_stale(self):
        snap = _snapshot()
        snap["rate_limits"]["primary"]["resets_at"] = _NOW + 3600
        window = codex_status.normalize_snapshot(snap, now=_NOW)["rate_limits"]["five_hour"]
        self.assertFalse(window.get("stale"))


class NormalizeSnapshotSanitizationTests(unittest.TestCase):
    def test_strips_control_characters_from_model_and_cwd(self):
        snap = _snapshot(model="gpt-5\x07-codex\x1b[31m", cwd="/workspace\x00/demo-project")
        result = codex_status.normalize_snapshot(snap, now=_NOW)
        model_name = result["model"]["display_name"]
        cwd = result["cwd"]
        self.assertEqual(model_name, "gpt-5-codex")
        self.assertEqual(cwd, "/workspace/demo-project")
        for ch in model_name + cwd:
            self.assertGreaterEqual(ord(ch), 0x20)


class ReadRolloutTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "rollout.jsonl")

    def _write(self, text: str) -> None:
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(text)

    @staticmethod
    def _turn_context(model: str, cwd: str) -> str:
        return json.dumps({"type": "turn_context", "payload": {"model": model, "cwd": cwd}}) + "\n"

    @staticmethod
    def _token_event(info=None, rate_limits=None) -> str:
        payload = {"type": "token_count", "info": info, "rate_limits": rate_limits}
        return json.dumps({"type": "event_msg", "payload": payload}) + "\n"

    def test_happy_path_gathers_model_cwd_info_and_rate_limits(self):
        info = {"last_token_usage": dict(_LAST_TOKEN_USAGE), "model_context_window": _MODEL_CONTEXT_WINDOW}
        self._write(
            self._turn_context("gpt-5-codex", "/workspace/demo-project")
            + self._token_event(info=info, rate_limits=copy.deepcopy(_RATE_LIMITS))
        )
        snapshot = codex_status.read_rollout(self.path)
        self.assertEqual(snapshot["model"], "gpt-5-codex")
        self.assertEqual(snapshot["cwd"], "/workspace/demo-project")
        self.assertEqual(snapshot["info"], info)
        self.assertEqual(snapshot["rate_limits"], _RATE_LIMITS)

    def test_latest_turn_context_wins_over_earlier_one(self):
        self._write(
            self._turn_context("old-model", "/old/path")
            + self._turn_context("new-model", "/new/path")
        )
        snapshot = codex_status.read_rollout(self.path)
        self.assertEqual(snapshot["model"], "new-model")
        self.assertEqual(snapshot["cwd"], "/new/path")

    def test_rate_limits_only_event_preserves_earlier_info(self):
        info = {"last_token_usage": dict(_LAST_TOKEN_USAGE), "model_context_window": _MODEL_CONTEXT_WINDOW}
        self._write(
            self._token_event(info=info, rate_limits=copy.deepcopy(_RATE_LIMITS))
            + self._token_event(info=None, rate_limits={"primary": {"used_percent": 99.0, "window_minutes": 300, "resets_at": 1}})
        )
        snapshot = codex_status.read_rollout(self.path)
        self.assertEqual(snapshot["info"], info)
        self.assertEqual(snapshot["rate_limits"]["primary"]["used_percent"], 99.0)

    def test_info_only_event_preserves_earlier_rate_limits(self):
        old_info = {"last_token_usage": dict(_LAST_TOKEN_USAGE), "model_context_window": _MODEL_CONTEXT_WINDOW}
        new_info = {"last_token_usage": {"total_tokens": 1, "input_tokens": 1, "output_tokens": 0, "cached_input_tokens": 0},
                    "model_context_window": _MODEL_CONTEXT_WINDOW}
        self._write(
            self._token_event(info=old_info, rate_limits=copy.deepcopy(_RATE_LIMITS))
            + self._token_event(info=new_info, rate_limits=None)
        )
        snapshot = codex_status.read_rollout(self.path)
        self.assertEqual(snapshot["info"], new_info)
        self.assertEqual(snapshot["rate_limits"], _RATE_LIMITS)

    def test_skips_malformed_json_line_between_valid_ones(self):
        self._write(
            self._turn_context("model-a", "/a")
            + "{not valid json,,,\n"
            + self._turn_context("model-b", "/b")
        )
        snapshot = codex_status.read_rollout(self.path)
        self.assertEqual(snapshot["model"], "model-b")

    def test_skips_non_object_json_rows(self):
        for row in ("42", '"just a string"', "[1, 2, 3]"):
            with self.subTest(row=row):
                self._write(
                    self._turn_context("model-a", "/a")
                    + row + "\n"
                    + self._turn_context("model-b", "/b")
                )
                snapshot = codex_status.read_rollout(self.path)
                self.assertEqual(snapshot["model"], "model-b")

    def test_skips_incomplete_trailing_line(self):
        info = {"last_token_usage": dict(_LAST_TOKEN_USAGE), "model_context_window": _MODEL_CONTEXT_WINDOW}
        self._write(
            self._turn_context("model-a", "/a")
            + self._token_event(info=info, rate_limits=copy.deepcopy(_RATE_LIMITS))
            + '{"type": "event_msg", "payload": {"type": "token_count", "info": {"last_token'
        )
        snapshot = codex_status.read_rollout(self.path)
        self.assertEqual(snapshot["info"], info)
        self.assertEqual(snapshot["rate_limits"], _RATE_LIMITS)

    def test_missing_file_raises_actionable_oserror(self):
        missing = os.path.join(self._tmp.name, "does-not-exist.jsonl")
        with self.assertRaises(OSError) as ctx:
            codex_status.read_rollout(missing)
        self.assertIn(missing, str(ctx.exception))

    def test_max_bytes_bounds_the_read_to_the_tail(self):
        # The old turn_context sits only in the head of the file, outside the
        # tail window; a bounded read must not see it, unlike a full-file scan.
        head = self._turn_context("ancient-model-only-in-head", "/ancient/path")
        info = {"last_token_usage": dict(_LAST_TOKEN_USAGE), "model_context_window": _MODEL_CONTEXT_WINDOW}
        filler = self._token_event(info=None, rate_limits=None) * 50
        final = self._token_event(info=info, rate_limits=copy.deepcopy(_RATE_LIMITS))
        tail = filler + final
        self._write(head + tail)

        snapshot = codex_status.read_rollout(self.path, max_bytes=len(tail.encode("utf-8")))

        self.assertNotEqual(snapshot.get("model"), "ancient-model-only-in-head")
        self.assertEqual(snapshot["info"], info)
        self.assertEqual(snapshot["rate_limits"], _RATE_LIMITS)

    def test_max_bytes_bound_holds_against_growth_during_the_read(self):
        # read_rollout measures `size` via seek/tell, then must bound its
        # physical read to that measurement even if the file being tailed
        # (a live Codex rollout) grows in between -- otherwise a read with no
        # explicit length reads past max_bytes and leaks the newly appended,
        # out-of-window bytes into the snapshot. The growth is injected
        # deterministically at the exact moment read_rollout calls tell(),
        # via a thin wrapper around the real file handle -- no sleep, no
        # thread, no timing assumption.
        old_line = self._turn_context("old-model", "/old/path")
        self._write(old_line)
        initial_size = os.path.getsize(self.path)
        max_bytes = initial_size  # the whole existing file sits in the tail window
        growth_line = self._turn_context("grown-model", "/grown/path").encode("utf-8")
        path = self.path
        real_open = open

        class _GrowOnFirstTell:
            def __init__(self, real_fh):
                self._real = real_fh
                self._grown = False
                self.read_lengths = []

            def tell(self):
                pos = self._real.tell()
                if not self._grown:
                    self._grown = True
                    with real_open(path, "ab") as growth_fh:
                        growth_fh.write(growth_line)
                return pos

            def read(self, *args, **kwargs):
                chunk = self._real.read(*args, **kwargs)
                self.read_lengths.append(len(chunk))
                return chunk

            def seek(self, *args, **kwargs):
                return self._real.seek(*args, **kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return self._real.__exit__(*exc_info)

        wrappers = []

        def fake_open(file, mode="r", *args, **kwargs):
            real_fh = real_open(file, mode, *args, **kwargs)
            if file == path and mode == "rb":
                wrapper = _GrowOnFirstTell(real_fh)
                wrappers.append(wrapper)
                return wrapper
            return real_fh

        with patch("builtins.open", side_effect=fake_open):
            snapshot = codex_status.read_rollout(path, max_bytes=max_bytes)

        self.assertEqual(os.path.getsize(path), initial_size + len(growth_line))
        physical_bytes_read = wrappers[0].read_lengths[0]
        self.assertLessEqual(physical_bytes_read, max_bytes)
        self.assertNotEqual(snapshot.get("model"), "grown-model")


class AnsiToTmuxTests(unittest.TestCase):
    def test_translates_truecolor_foreground(self):
        result = codex_status.ansi_to_tmux("\x1b[38;2;0;229;255mcyan\x1b[0m")
        self.assertEqual(result, "#[fg=#00e5ff]cyan#[default]")

    def test_zero_pads_hex_channels(self):
        cases = {
            (5, 10, 250): "#050afa",
            (255, 255, 255): "#ffffff",
            (0, 0, 0): "#000000",
        }
        for (r, g, b), hex_color in cases.items():
            with self.subTest(rgb=(r, g, b)):
                result = codex_status.ansi_to_tmux(f"\x1b[38;2;{r};{g};{b}mx")
                self.assertEqual(result, f"#[fg={hex_color}]x")

    def test_translates_bold_and_dim(self):
        self.assertEqual(codex_status.ansi_to_tmux("\x1b[1mBOLD\x1b[0m"), "#[bold]BOLD#[default]")
        self.assertEqual(codex_status.ansi_to_tmux("\x1b[2mdim\x1b[0m"), "#[dim]dim#[default]")

    def test_escapes_literal_hash_in_plain_text(self):
        self.assertEqual(codex_status.ansi_to_tmux("feature/#123"), "feature/##123")

    def test_escapes_hash_that_looks_like_a_tmux_directive(self):
        # Untrusted model/git text must never be able to inject a real tmux
        # format directive by spelling out "#[...]" itself.
        result = codex_status.ansi_to_tmux("#[fg=red]")
        self.assertEqual(result, "##[fg=red]")
        self.assertNotIn("#[fg=red]", result.replace("##[fg=red]", ""))

    def test_strips_unsupported_or_control_sequences(self):
        cases = ["\x1b[4munderline\x1b[0m", "before\x1b[2Kafter", "bell\x07tail"]
        for text in cases:
            with self.subTest(text=text):
                result = codex_status.ansi_to_tmux(text)
                self.assertNotIn("\x1b", result)
                self.assertNotIn("\x07", result)
                self.assertNotIn("#[4]", result)

    def test_private_and_intermediate_csi_dropped_without_leaking_garbage(self):
        # A private-parameter CSI (cursor show/hide) is unsupported and must
        # be consumed whole, not leave its "[?25h"-shaped tail behind as
        # visible garbage once only the lone ESC byte gets stripped.
        result = codex_status.ansi_to_tmux("\x1b[?25hHello\x1b[?25lWorld")
        self.assertNotIn("[?25h", result)
        self.assertNotIn("[?25l", result)
        self.assertEqual(result, "HelloWorld")

        # The fix must not regress ordinary SGR translation or literal '#'
        # escaping alongside the wider CSI match.
        self.assertEqual(codex_status.ansi_to_tmux("\x1b[38;2;0;229;255mcyan\x1b[0m"), "#[fg=#00e5ff]cyan#[default]")
        self.assertEqual(codex_status.ansi_to_tmux("feature/#123"), "feature/##123")

    def test_visible_glyphs_survive_color_translation(self):
        source = "\x1b[1mOpus\x1b[0m \x1b[38;2;0;229;255m4.8\x1b[0m \x1b[2m(dim note)\x1b[0m"
        expected_visible = re.sub(r"\x1b\[[0-9;]*m", "", source)
        result = codex_status.ansi_to_tmux(source)
        visible = re.sub(r"#\[[^\]]*\]", "", result)
        self.assertEqual(visible, expected_visible)


class RenderParityTests(unittest.TestCase):
    """statusline.render(data) must match the CLI's own subprocess stdout, so
    the Codex tmux renderer can call the real function instead of a
    hand-mirrored copy of the rendering logic."""

    def test_render_matches_cli_stdout_for_sample_payload(self):
        payload_path = os.path.join(ROOT, "tools", "sample-payload.json")
        with open(payload_path, "r", encoding="utf-8") as fh:
            payload_text = fh.read()

        cli = subprocess.run(
            [sys.executable, STATUSLINE_PATH],
            input=payload_text,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(cli.returncode, 0)
        expected = cli.stdout.rstrip("\n")

        actual = statusline.render(json.loads(payload_text))
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
