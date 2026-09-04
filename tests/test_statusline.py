"""Stdlib-only tests for statusline.py.

Runs against the real module (loaded straight from the repo root, not a
package import) so behavior can never drift from an installed copy.
"""

import importlib.util
import json
import os
import subprocess
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUSLINE_PATH = os.path.join(ROOT, "statusline.py")

_spec = importlib.util.spec_from_file_location("statusline", STATUSLINE_PATH)
statusline = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(statusline)


class FilledCellsTests(unittest.TestCase):
    def test_zero_is_empty(self):
        self.assertEqual(statusline.filled_cells(0), 0)

    def test_small_nonzero_lights_at_least_one_cell(self):
        for pct in (1, 3, 5):
            self.assertGreaterEqual(statusline.filled_cells(pct), 1)

    def test_hundred_fills_the_whole_bar(self):
        self.assertEqual(statusline.filled_cells(100), statusline.BAR_WIDTH)

    def test_mid_value_rounds_to_nearest_cell(self):
        # 55% of a 10-cell bar rounds up to 6 filled cells.
        self.assertEqual(statusline.filled_cells(55), 6)

    def test_clamps_above_100(self):
        self.assertEqual(statusline.filled_cells(150), statusline.BAR_WIDTH)

    def test_clamps_below_zero(self):
        self.assertEqual(statusline.filled_cells(-10), 0)


class UntilTests(unittest.TestCase):
    def test_future_timestamp_yields_a_countdown(self):
        future = int(time.time()) + 3600
        self.assertNotEqual(statusline.until(future), "")

    def test_past_timestamp_yields_empty_string(self):
        # Claude Code sometimes ships a stale window with a reset already in
        # the past; showing no countdown there beats a confident wrong one.
        past = int(time.time()) - 3600
        self.assertEqual(statusline.until(past), "")


class RampTests(unittest.TestCase):
    def test_channels_stay_in_byte_range(self):
        for pct in (-10, 0, 50, 100, 150):
            r, g, b = statusline._ramp(pct)
            for channel in (r, g, b):
                self.assertGreaterEqual(channel, 0)
                self.assertLessEqual(channel, 255)

    def test_zero_percent_matches_first_stop(self):
        self.assertEqual(statusline._ramp(0), statusline.SYNTH_STOPS[0][1])

    def test_hundred_percent_matches_last_stop(self):
        self.assertEqual(statusline._ramp(100), statusline.SYNTH_STOPS[-1][1])


class RenderRobustnessTests(unittest.TestCase):
    """Feeds the module real stdin as a subprocess, matching how Claude
    Code actually invokes it, so missing-field handling is tested end to
    end rather than only against hand-picked helper calls."""

    def _run(self, payload_text):
        return subprocess.run(
            [sys.executable, STATUSLINE_PATH],
            input=payload_text,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_empty_stdin_does_not_crash(self):
        result = self._run("")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    def test_empty_object_does_not_crash(self):
        result = self._run("{}")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    def test_missing_rate_limits_still_renders_context(self):
        payload = json.dumps({
            "model": {"display_name": "Opus 4.8"},
            "context_window": {
                "used_percentage": 10,
                "total_input_tokens": 100,
                "total_output_tokens": 5,
                "context_window_size": 1000,
            },
        })
        result = self._run(payload)
        self.assertEqual(result.returncode, 0)
        self.assertIn("ctx", result.stdout)

    def test_missing_context_window_still_renders_five_hour(self):
        payload = json.dumps({
            "model": {"display_name": "Opus 4.8"},
            "rate_limits": {
                "five_hour": {
                    "used_percentage": 20,
                    "resets_at": int(time.time()) + 3600,
                },
            },
        })
        result = self._run(payload)
        self.assertEqual(result.returncode, 0)
        self.assertIn("5h", result.stdout)


if __name__ == "__main__":
    unittest.main()
