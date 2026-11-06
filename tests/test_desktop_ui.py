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
                ("Claude Code", {"state": "ready", "model": "claude-opus-5-5", "context_percent": 27.0, "session_name": "IO-2484"}),
                ("Codex", {"state": "waiting", "model": "gpt-5-codex", "context_percent": None}),
            ])
            window.show()
            app.processEvents()
            claude, codex = window.panels
            assert claude._title.text() == "Claude Code"
            assert claude._session_label.text() == "IO-2484"
            assert "claude-opus-5-5" in claude._session_label.toolTip()
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
            window.show()
            app.processEvents()
            panel = window.panels[0]

            def reds(bar):
                image = bar.grab().toImage()
                row = image.height() - 1
                block_centers = range(3, image.width() - 6, 8)  # 6px blocks, 2px gaps
                return {image.pixelColor(x, row).red() for x in block_centers}

            assert max(reds(panel._context_bar)) < 100
            assert max(reds(panel._usage_labels["five_hour"][1])) > 200
            window.close()
            '''
        )

    def test_context_column_is_as_wide_as_the_weekly_column(self):
        self._run_qt_assertions(
            '''
            import importlib.util
            import sys
            from PySide6.QtWidgets import QApplication

            spec = importlib.util.spec_from_file_location("bifrost_ui_widths", sys.argv[1])
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            app = QApplication([])
            window = module.StripWindow([("Codex", {
                "state": "ready", "context_percent": 21.2,
                "used_tokens": 175578, "window_tokens": 828400,
                "usage": [{"label": "Weekly", "used_percent": 1.0, "reset_text": "Resets in 126h 51m"}],
                "detail": "Rollout has not changed for 456 seconds",
            })])
            window.show()
            app.processEvents()
            panel = window.panels[0]
            weekly_bar = panel._usage_labels["seven_day"][1]
            assert panel._context_bar.width() == weekly_bar.width()
            assert panel._tokens.text() == "176k / 828k"
            window.close()
            '''
        )

    def test_a_closed_source_hides_its_panel_until_it_runs_again(self):
        self._run_qt_assertions(
            '''
            import importlib.util
            import sys
            from PySide6.QtWidgets import QApplication

            spec = importlib.util.spec_from_file_location("bifrost_ui_closed", sys.argv[1])
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            app = QApplication([])
            ready = {"state": "ready", "model": "claude-opus-5-5", "context_percent": 27.0, "session_name": "IO-2484"}
            closed = {"state": "closed", "detail": "Codex is not running"}
            window = module.StripWindow([("Claude Code", ready), ("Codex", closed)])
            window.show()
            app.processEvents()
            claude, codex = window.panels
            assert claude.isVisible()
            assert codex.isHidden()
            assert window._idle.isHidden()
            width_with_one = window.width()

            window.update_sources([closed, closed])
            app.processEvents()
            assert claude.isHidden() and codex.isHidden()
            assert window._idle.isVisible()
            assert window._idle.text() == "Waiting for Claude Code or Codex to start"

            window.update_sources([ready, ready])
            app.processEvents()
            assert claude.isVisible() and codex.isVisible()
            assert window._idle.isHidden()
            assert window.width() > width_with_one
            window.close()
            '''
        )

    def test_two_sources_fit_the_dock_band_without_clipped_metrics(self):
        self._run_qt_assertions(
            """
            import desktop
            from PySide6.QtWidgets import QApplication, QLabel
            app = QApplication([])
            status = {
                "state": "ready", "model": "claude-opus-5-5",
                "context_percent": 21.2, "used_tokens": 175578, "window_tokens": 828400,
                "usage": [
                    {"label": "5h", "used_percent": 90, "reset_text": "Resets in 2h 10m"},
                    {"label": "Weekly", "used_percent": 7, "reset_text": "Resets in 125h 30m"},
                ],
                "detail": "Live Claude Code status",
                "session_name": "Bifrost · Enter the Grid with a very long session title",
                "session_id": "123e4567-e89b-12d3-a456-426614174000",
            }
            window = desktop.StripWindow([("Claude Code", status), ("Codex", status)])
            window.show()
            app.processEvents()
            left, right = window.panels
            assert left.geometry().right() < right.geometry().left()
            assert window.height() <= 96, window.size()
            assert "Bifrost" in left._session_label.text()
            assert "very long session title" in left._session_label.toolTip()
            assert "claude-opus-5-5" in left._session_label.toolTip()
            assert "123e4567-e89b-12d3-a456-426614174000" in left._session_label.toolTip()
            for panel in window.panels:
                for label in panel.findChildren(QLabel):
                    if label.isVisible():
                        assert label.width() >= label.sizeHint().width(), (label.text(), label.size())
                        assert label.height() >= label.sizeHint().height(), (label.text(), label.size())
            window.close()
            """
        )

    def test_height_stays_fixed_when_session_names_contain_emojis(self):
        self._run_qt_assertions(
            """
            import desktop
            from PySide6.QtWidgets import QApplication
            app = QApplication([])
            status = {"state": "ready", "context_percent": 21, "session_name": "Bifrost",
                      "usage": [{"label": "Weekly", "used_percent": 7}]}
            window = desktop.StripWindow([("Claude Code", status), ("Codex", status)])
            window.show()
            for name in ["Bifrost", "🧠 PRs Brain", "✅ review", "👩🏽‍💻 Coding", "Bifrost"]:
                window.update_sources([dict(status, session_name=name)] * 2)
                for _ in range(3):
                    app.processEvents()
                assert window.height() == 96, (name, window.height())
                for panel in window.panels:
                    label = panel._session_label
                    assert label.height() >= label.sizeHint().height(), (name, label.size())
            window.update_sources([{"state": "closed"}] * 2)
            app.processEvents()
            assert window.height() == 96
            window.close()
            """
        )

    def test_percentages_heat_up_with_usage_and_unknown_values_stay_neutral(self):
        self._run_qt_assertions(
            """
            import desktop
            from PySide6.QtGui import QPalette
            from PySide6.QtWidgets import QApplication
            app = QApplication([])
            window = desktop.StripWindow([("Codex", {"state": "ready"})])
            window.show()
            panel = window.panels[0]
            for percent in [10, 65, 98, None]:
                panel.update_status({"state": "ready", "context_percent": percent, "usage": [
                    {"label": "5h", "used_percent": percent},
                    {"label": "Weekly", "used_percent": percent},
                ]})
                app.processEvents()
                for label in [panel._context_percent, *[values[0] for values in panel._usage_labels.values()]]:
                    color = label.palette().color(QPalette.ColorRole.WindowText)
                    if percent == 10:
                        assert color.blue() > color.red() + 100, color.name()
                    elif percent == 65:
                        assert color.red() > 200 and 75 < color.green() < 150 and color.blue() < 100, color.name()
                    elif percent == 98:
                        assert color.red() > 200 and color.blue() > 150 and color.green() < 100, color.name()
                    else:
                        assert label.text() == "—"
                        assert abs(color.red() - color.blue()) < 50, color.name()
            window.close()
            """
        )

    def test_the_strip_reopens_where_it_was_last_moved(self):
        self._run_qt_assertions(
            '''
            import importlib.util
            import os
            import sys
            import tempfile
            from PySide6.QtCore import QPoint, QSettings
            from PySide6.QtWidgets import QApplication

            spec = importlib.util.spec_from_file_location("bifrost_ui_position", sys.argv[1])
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            app = QApplication([])
            path = os.path.join(tempfile.mkdtemp(), "desktop.ini")
            status = [("Codex", {"state": "waiting"})]

            def settings():
                return QSettings(path, QSettings.Format.IniFormat)

            first = module.StripWindow(status, settings())
            first.show()
            module.place_window(first)
            default = first.pos()
            first.move(40, 50)
            app.processEvents()
            first.close()

            second = module.StripWindow(status, settings())
            second.show()
            module.place_window(second)
            assert second.pos() == QPoint(40, 50), second.pos()

            off_screen = settings()
            off_screen.setValue("horizontal-position", QPoint(-9000, -9000))
            off_screen.sync()
            third = module.StripWindow(status, settings())
            third.show()
            module.place_window(third)
            assert third.pos() == default, (third.pos(), default)
            '''
        )


if __name__ == "__main__":
    unittest.main()
