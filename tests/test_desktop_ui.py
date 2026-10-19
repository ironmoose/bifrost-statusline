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
            window = module.StripWindow({
                "state": "ready", "model": "gpt-5-codex", "context_percent": 21.0,
                "used_tokens": 26880, "window_tokens": 128000, "usage": [],
                "detail": "Live Codex status",
            })
            window.update_status({
                "state": "ready", "model": "gpt-5-codex", "context_percent": None,
                "used_tokens": None, "window_tokens": None, "usage": [],
                "detail": "Live Codex status",
            })
            window.show()
            app.processEvents()
            assert window.isVisible()
            assert window._context_percent.text() == "—"
            assert window._context_bar.isHidden()

            window.update_status({
                "state": "ready", "model": "gpt-5-codex", "context_percent": 0.0,
                "used_tokens": 0, "window_tokens": 128000, "usage": [],
                "detail": "Live Codex status",
            })
            app.processEvents()
            assert window.isVisible()
            assert window._context_percent.text() == "0%"
            assert not window._context_bar.isHidden()
            assert window._context_bar.value() == 0
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
            window = module.StripWindow({
                "state": "ready", "model": "gpt-5-codex", "context_percent": 42.5,
                "used_tokens": 54400, "window_tokens": 128000,
                "usage": [{
                    "label": "Weekly", "used_percent": 40.0,
                    "reset_text": "Reset time passed", "stale": True,
                }],
                "detail": "Live Codex status",
            })
            window.show()
            app.processEvents()
            percent, bar, reset = window._usage_labels["seven_day"]
            assert window.isVisible()
            assert window._state.text() == "READY"
            assert window._context_percent.text() == "42.5%"
            assert percent.text() == "40%"
            assert bar.value() == 40
            assert "stale" in reset.text().casefold() or "expired" in reset.text().casefold()
            window.close()
            '''
        )


if __name__ == "__main__":
    unittest.main()
