"""Exercise real desktop-strip widgets in isolated offscreen Qt processes."""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
DESKTOP_PATH = ROOT / "desktop.py"


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 is optional")
class DesktopStripWidgetTests(unittest.TestCase):
    def _run_qt_assertions(self, source: str) -> None:
        environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source), str(DESKTOP_PATH)],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )

        self.assertEqual(result.returncode, 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")

    def test_unknown_context_hides_its_bar_and_zero_remains_visible(self):
        self._run_qt_assertions(
            '''
            import importlib.util
            import sys
            from PySide6.QtWidgets import QApplication

            spec = importlib.util.spec_from_file_location("bifrost_ui_unknown", sys.argv[1])
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            app = QApplication([])
            window = module.StripWindow([("Codex", {
                "state": "ready", "model": "gpt-5-codex", "context_percent": 21.0,
                "used_tokens": 26880, "window_tokens": 128000, "usage": [],
                "detail": "Live Codex status",
            })])
            panel = window.panels[0]
            panel.update_status({
                "state": "ready", "model": "gpt-5-codex", "context_percent": None,
                "used_tokens": None, "window_tokens": None, "usage": [],
                "detail": "Live Codex status",
            })
            window.show()
            app.processEvents()
            assert window.isVisible()
            assert panel._context_percent.text() == "—"
            assert panel._context_bar.isHidden()

            panel.update_status({
                "state": "ready", "model": "gpt-5-codex", "context_percent": 0.0,
                "used_tokens": 0, "window_tokens": 128000, "usage": [],
                "detail": "Live Codex status",
            })
            app.processEvents()
            assert window.isVisible()
            assert panel._context_percent.text() == "0%"
            assert not panel._context_bar.isHidden()
            assert panel._context_bar.value() == 0
            window.close()
            '''
        )

    def test_expired_quota_is_labeled_stale_while_fresh_context_stays_ready(self):
        self._run_qt_assertions(
            '''
            import importlib.util
            import sys
            from PySide6.QtWidgets import QApplication

            spec = importlib.util.spec_from_file_location("bifrost_ui_expired", sys.argv[1])
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            app = QApplication([])
            window = module.StripWindow([("Codex", {
                "state": "ready", "model": "gpt-5-codex", "context_percent": 42.5,
                "used_tokens": 54400, "window_tokens": 128000,
                "usage": [{
                    "label": "Weekly", "used_percent": 40.0,
                    "reset_text": "Reset time passed", "stale": True,
                }],
                "detail": "Live Codex status",
            })])
            panel = window.panels[0]
            window.show()
            app.processEvents()
            percent, bar, reset = panel._usage_labels["seven_day"]
            assert window.isVisible()
            assert panel._state.text() == "READY"
            assert panel._context_percent.text() == "42.5%"
            assert percent.text() == "40%"
            assert bar.value() == 40
            assert "stale" in reset.text().casefold() or "expired" in reset.text().casefold()
            window.close()
            '''
        )

    def test_one_window_shows_a_panel_per_source(self):
        self._run_qt_assertions(
            '''
            import importlib.util
            import sys
            from PySide6.QtWidgets import QApplication

            spec = importlib.util.spec_from_file_location("bifrost_ui_sources", sys.argv[1])
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            app = QApplication([])
            window = module.StripWindow([
                ("Claude Code", {"state": "ready", "model": "claude-opus-5-5", "context_percent": 27.0}),
                ("Codex", {"state": "waiting", "model": "gpt-5-codex", "context_percent": None}),
            ])
            window.show()
            app.processEvents()
            claude, codex = window.panels
            assert claude._title.text() == "Claude Code"
            assert claude._model_label.text() == "claude-opus-5-5"
            assert claude._context_percent.text() == "27%"
            assert codex._title.text() == "Codex"
            assert codex._state.text() == "WAITING"
            assert codex._context_bar.isHidden()
            window.close()
            '''
        )

    def test_bars_color_by_position_so_a_low_fill_stays_cool(self):
        self._run_qt_assertions(
            '''
            import importlib.util
            import sys
            from PySide6.QtWidgets import QApplication

            spec = importlib.util.spec_from_file_location("bifrost_ui_gradient", sys.argv[1])
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            app = QApplication([])
            window = module.StripWindow([("Claude Code", {
                "state": "ready", "context_percent": 10.0,
                "usage": [{"label": "5h", "used_percent": 90.0, "reset_text": "Resets in 1h"}],
            })])
            panel = window.panels[0]
            low = panel._context_bar.styleSheet()
            high = panel._usage_labels["five_hour"][1].styleSheet()
            red = module._rgb(module.statusline.SYNTH_STOPS[-1][1])
            magenta = module._rgb(module.statusline.SYNTH_STOPS[2][1])
            assert module._rgb(module.statusline._ramp(10)) in low
            assert magenta not in low and red not in low
            assert magenta in high
            window.close()
            '''
        )


if __name__ == "__main__":
    unittest.main()
