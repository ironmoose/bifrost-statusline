"""Render Codex and Claude Code status in a small, movable PySide6 window."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import math
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFontDatabase, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import statusline

_COLORS = {
    "surface": "#19171f",
    "text": "#f0eaf7",
    "dim": "#b8afc6",
    "track": "#352b46",
    "cyan": "#00e5ff",
    "purple": "#a25eff",
    "pink": "#ff2e97",
    "orange": "#ff6c3a",
    "red": "#ff3a5c",
}

# Every metric column is this wide, so context, 5h and weekly line up.
_METRIC_WIDTH = 124


def _label(text: str, name: str, parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setAccessibleName(name)
    label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return label


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _percent_text(value: object) -> str:
    number = _number(value)
    return "—" if number is None else f"{round(number, 1):g}%"


def _bar_value(value: object) -> int:
    number = _number(value)
    return 0 if number is None else int(max(0, min(100, round(number))))


def _rgb(color: tuple) -> str:
    return "#{:02x}{:02x}{:02x}".format(*color)


def _block_colors(percent: int, blocks: int) -> list[tuple]:
    """Colors of the lit blocks, each by its position on the ramp, so a bar heats up only as it fills."""
    lit = math.ceil(max(0, min(100, percent)) * blocks / 100)
    return [statusline._ramp((index + 0.5) * 100 / blocks) for index in range(lit)]


def _short_count(count: int) -> str:
    if count >= 999_500:
        return f"{count / 1_000_000:.3g}M"
    if count >= 1_000:
        return f"{round(count / 1_000)}k"
    return str(count)


def _tokens_text(used: object, window: object) -> str:
    used_number = _number(used)
    window_number = _number(window)
    if used_number is None or window_number is None:
        return "Tokens unavailable"
    return f"{_short_count(int(used_number))} / {_short_count(int(window_number))}"


class _PixelBar(QProgressBar):
    """A progress bar drawn as square blocks, like an 8-bit health bar."""

    _BLOCK = 6
    _GAP = 2

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(self._BLOCK + 4)

    def paintEvent(self, event) -> None:
        step = self._BLOCK + self._GAP
        blocks = max(1, (self.width() + self._GAP) // step)
        lit = _block_colors(self.value(), blocks)
        painter = QPainter(self)
        for index in range(blocks):
            x = index * step
            if index >= len(lit):
                painter.fillRect(x, 0, self._BLOCK, self.height(), QColor(_COLORS["track"]))
                continue
            color = QColor(_rgb(lit[index]))
            painter.fillRect(x, 0, self._BLOCK, self.height(), color)
            painter.fillRect(x, 0, self._BLOCK, 2, color.lighter(150))
        painter.end()


class _DragHandle(QWidget):
    def mousePressEvent(self, event) -> None:
        """Start a desktop-managed move from the brand area."""
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window().windowHandle()
            if window is not None:
                window.startSystemMove()
            event.accept()
            return
        super().mousePressEvent(event)


class SourcePanel(QFrame):
    """Show one source's model, context, usage windows and state."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("source")
        self.setAccessibleName(f"{title} status")
        self._usage_labels: dict[str, tuple[QLabel, QProgressBar, QLabel]] = {}
        self._usage_panels: dict[str, QWidget] = {}
        self._build_ui(title)

    def _build_ui(self, title: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        header = QHBoxLayout()
        self._title = _label(title, "Source title")
        self._title.setObjectName("source-title")
        header.addWidget(self._title, 1)
        self._model_label = _label(title, f"{title} model")
        self._model_label.setObjectName("model")
        self._model_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self._model_label)
        layout.addLayout(header)

        metrics = QHBoxLayout()
        metrics.setSpacing(16)

        context_panel, self._context_percent, self._context_bar, self._tokens = self._metric_panel(
            "Context", "context", "Tokens unavailable"
        )
        metrics.addWidget(context_panel)
        self._add_usage_panel(metrics, "5h", "five_hour", "usage-five")
        self._add_usage_panel(metrics, "Weekly", "seven_day", "usage-week")
        metrics.addStretch(1)
        layout.addLayout(metrics)

        footer = QHBoxLayout()
        footer.setSpacing(12)
        self._detail = _label(f"Waiting for {title} telemetry", "Status details")
        self._detail.setObjectName("detail")
        # Wrap long details instead of letting them widen the strip.
        self._detail.setWordWrap(True)
        self._detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._detail.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._state = _label("WAITING", "Status state")
        self._state.setObjectName("state")
        footer.addWidget(self._detail, 1)
        footer.addWidget(self._state)
        layout.addLayout(footer)

    @staticmethod
    def _bar(object_name: str) -> QProgressBar:
        bar = _PixelBar()
        bar.setObjectName(object_name)
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(False)
        bar.setAccessibleName(f"{object_name.replace('-', ' ')} percentage")
        return bar

    def _metric_panel(
        self, label: str, bar_name: str, caption: str
    ) -> tuple[QWidget, QLabel, QProgressBar, QLabel]:
        """One fixed-width column: heading, percent, bar and a caption line."""
        panel_widget = QWidget(self)
        panel_widget.setFixedWidth(_METRIC_WIDTH)
        panel = QVBoxLayout(panel_widget)
        panel.setContentsMargins(0, 0, 0, 0)
        panel.setSpacing(3)
        heading = _label(label, f"{label} usage heading")
        heading.setObjectName("metric-title")
        panel.addWidget(heading)
        percent = _label("—", f"{label} usage percent")
        percent.setObjectName("metric-percent")
        panel.addWidget(percent)
        bar = self._bar(bar_name)
        panel.addWidget(bar)
        caption_label = _label(caption, f"{label} caption")
        caption_label.setObjectName("caption")
        panel.addWidget(caption_label)
        return panel_widget, percent, bar, caption_label

    def _add_usage_panel(self, metrics: QHBoxLayout, label: str, key: str, bar_name: str) -> None:
        panel_widget, percent, bar, reset = self._metric_panel(label, bar_name, "Usage unavailable")
        metrics.addWidget(panel_widget)
        self._usage_labels[key] = (percent, bar, reset)
        self._usage_panels[key] = panel_widget

    def update_status(self, status: dict[str, object]) -> None:
        """Update every displayed metric from one normalized status snapshot."""
        status = status if isinstance(status, dict) else {}
        self._model_label.setText(str(status.get("model") or "Model unavailable"))

        context_percent = _number(status.get("context_percent"))
        self._context_percent.setText(_percent_text(context_percent))
        self._context_bar.setValue(_bar_value(context_percent))
        self._context_bar.setVisible(context_percent is not None)
        self._tokens.setText(_tokens_text(status.get("used_tokens"), status.get("window_tokens")))

        usage_values = status.get("usage")
        usage_by_label = {}
        if isinstance(usage_values, list):
            for entry in usage_values:
                if isinstance(entry, dict) and isinstance(entry.get("label"), str):
                    usage_by_label[entry["label"].casefold()] = entry
        for key, labels in self._usage_labels.items():
            entry = usage_by_label.get({"five_hour": "5h", "seven_day": "weekly"}[key].casefold())
            percent_label, bar, reset_label = labels
            if entry is None:
                self._usage_panels[key].hide()
                percent_label.setText("—")
                bar.setValue(0)
                reset_label.setText("Usage unavailable")
                continue
            self._usage_panels[key].show()
            percentage = _number(entry.get("used_percent"))
            percent_label.setText(_percent_text(percentage))
            bar.setValue(_bar_value(percentage))
            bar.setVisible(percentage is not None)
            reset_text = str(entry.get("reset_text") or "Reset time unknown")
            stale = entry.get("stale") is True
            if stale and "stale" not in reset_text.casefold() and "expired" not in reset_text.casefold():
                reset_text = f"Expired · {reset_text}"
            reset_label.setText(reset_text)
            reset_label.setStyleSheet(
                f"color: {_COLORS['orange']};" if stale else ""
            )

        state = status.get("state")
        state_label = state.upper() if isinstance(state, str) else "UNAVAILABLE"
        state_colors = {
            "READY": _COLORS["cyan"],
            "STALE": _COLORS["orange"],
            "WAITING": _COLORS["dim"],
            "UNAVAILABLE": _COLORS["red"],
        }
        color = state_colors.get(state_label, _COLORS["red"])
        self._state.setText(state_label)
        self._state.setStyleSheet(f"color: {color};")
        self._detail.setText(str(status.get("detail") or ""))


class StripWindow(QWidget):
    """Show a live, frameless status strip with one panel per source."""

    def __init__(self, sources: Sequence[tuple[str, dict[str, object]]]) -> None:
        super().__init__(
            None,
            Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setWindowTitle("Bifrost desktop status")
        self.setAccessibleName("Bifrost status strip")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.panels: list[SourcePanel] = []
        self._build_ui([title for title, _ in sources])
        for panel, (_, status) in zip(self.panels, sources):
            panel.update_status(status or {})

    def _build_ui(self, titles: list[str]) -> None:
        # The platform's own fixed-width family, named exactly so Qt needn't resolve an alias.
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
        self.setStyleSheet(f"""
            QWidget#strip {{
                background: transparent; color: {_COLORS['text']};
                font-family: "{mono}"; font-size: 12px;
            }}
            QFrame#card {{
                background: {_COLORS['surface']};
                border: 2px solid qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {_COLORS['cyan']}, stop:0.5 {_COLORS['purple']}, stop:1 {_COLORS['pink']});
                border-radius: 4px;
            }}
            QLabel {{
                color: {_COLORS['text']}; background: transparent; border: 0;
                font-family: "{mono}";
            }}
            QLabel#brand {{ color: {_COLORS['cyan']}; font-size: 11px; font-weight: 600; letter-spacing: 2px; }}
            QLabel#model {{ color: {_COLORS['dim']}; font-size: 12px; }}
            QLabel#metric-title {{ color: {_COLORS['dim']}; font-size: 11px; }}
            QLabel#metric-percent {{ font-size: 20px; font-weight: 600; }}
            QLabel#detail, QLabel#caption {{
                color: {_COLORS['dim']}; font-size: 11px;
            }}
            QLabel#state {{ color: {_COLORS['cyan']}; font-size: 10px; font-weight: 600; }}
            QPushButton {{
                background: transparent; border: 0; border-radius: 5px;
                color: {_COLORS['text']}; min-width: 26px; min-height: 26px;
                font-size: 16px;
            }}
            QPushButton:hover {{ background: {_COLORS['track']}; }}
            QPushButton:checked {{ color: {_COLORS['cyan']}; }}
            QPushButton#pin {{ font-size: 10px; min-width: 38px; padding: 0 4px; }}
            QPushButton#close {{ min-width: 26px; }}
            QFrame#source {{ border: 0; border-top: 1px solid {_COLORS['track']}; }}
            QLabel#source-title {{ color: {_COLORS['text']}; font-size: 11px; font-weight: 600; }}
        """)
        self.setObjectName("strip")

        outer = QVBoxLayout(self)
        # The window follows its content, so it shrinks when a usage column is hidden.
        outer.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        outer.setContentsMargins(17, 12, 17, 10)
        outer.setSpacing(0)

        self._card = QFrame(self)
        self._card.setObjectName("card")
        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(16, 10, 16, 10)
        card_layout.setSpacing(10)
        outer.addWidget(self._card)

        header = QHBoxLayout()
        header.setSpacing(9)
        brand = _label("⠿  BIFROST", "Bifrost brand")
        brand.setObjectName("brand")
        drag = _DragHandle(self._card)
        drag.setCursor(Qt.CursorShape.SizeAllCursor)
        drag_layout = QHBoxLayout(drag)
        drag_layout.setContentsMargins(0, 0, 0, 0)
        drag_layout.addWidget(brand)
        header.addWidget(drag, 1)

        self._pin_button = QPushButton("Unpin", self._card)
        self._pin_button.setObjectName("pin")
        self._pin_button.setCheckable(True)
        self._pin_button.setChecked(True)
        self._pin_button.setAccessibleName("Always on top")
        self._pin_button.setToolTip("Always on top")
        self._pin_button.toggled.connect(self._set_pinned)
        header.addWidget(self._pin_button)

        close_button = QPushButton("×", self._card)
        close_button.setObjectName("close")
        close_button.setAccessibleName("Close Bifrost strip")
        close_button.setToolTip("Close")
        close_button.clicked.connect(self.close)
        header.addWidget(close_button)
        card_layout.addLayout(header)

        for title in titles:
            panel = SourcePanel(title, self._card)
            card_layout.addWidget(panel)
            self.panels.append(panel)

    def _set_pinned(self, pinned: bool) -> None:
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, pinned)
        self._pin_button.setText("Unpin" if pinned else "Pin")
        self._pin_button.setAccessibleName(
            "Unpin window from always-on-top" if pinned else "Pin window above others"
        )
        self._pin_button.setToolTip("Always on top" if pinned else "Pin above other windows")
        self.show()


StatusProvider = Callable[[], dict[str, object]]


def run_desktop(sources: Sequence[tuple[str, dict[str, object], StatusProvider]]) -> int:
    """Run the strip with one panel per `(title, initial_status, provider)`, refreshed every two seconds."""
    app = QApplication.instance() or QApplication(sys.argv[:1])
    window = StripWindow([(title, initial) for title, initial, _ in sources])
    window.adjustSize()
    screen = app.primaryScreen()
    if screen is not None:
        available = screen.availableGeometry()
        x = available.x() + (available.width() - window.width()) // 2
        y = available.y() + available.height() - window.height() - 30
        window.move(x, y)
    window.show()

    def refresh() -> None:
        for panel, (_, _, provider) in zip(window.panels, sources):
            panel.update_status(provider())

    timer = QTimer(window)
    timer.setInterval(2000)
    timer.timeout.connect(refresh)
    timer.start()
    return app.exec()
