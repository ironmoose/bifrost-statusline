"""Render Codex rollout status in a small, movable PySide6 window."""

from __future__ import annotations

from collections.abc import Callable
import math
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

_COLORS = {
    "surface": "#19171f",
    "text": "#f0eaf7",
    "dim": "#b8afc6",
    "track": "#352b46",
    "edge": "#50405f",
    "cyan": "#00e5ff",
    "purple": "#a25eff",
    "pink": "#ff2e97",
    "orange": "#ff6c3a",
    "red": "#ff3a5c",
}


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


def _tokens_text(used: object, window: object) -> str:
    used_number = _number(used)
    window_number = _number(window)
    if used_number is None or window_number is None:
        return "Token count unavailable"
    return f"{int(used_number):,} / {int(window_number):,} tokens"


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


class StripWindow(QWidget):
    """Show a live, frameless status strip for one Codex session."""

    def __init__(self, status: dict[str, object] | None = None, title: str = "Codex") -> None:
        super().__init__(
            None,
            Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setWindowTitle("Bifrost desktop status")
        self.setFixedWidth(610)
        self.setAccessibleName("Bifrost Codex status strip")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._title_text = title
        self._usage_labels: dict[str, tuple[QLabel, QProgressBar, QLabel]] = {}
        self._usage_panels: dict[str, QWidget] = {}
        self._build_ui()
        self.update_status(status or {})

    def _build_ui(self) -> None:
        self.setStyleSheet(f"""
            QWidget#strip {{
                background: transparent; color: {_COLORS['text']};
                font-family: monospace; font-size: 12px;
            }}
            QFrame#card {{
                background: {_COLORS['surface']}; border: 1px solid {_COLORS['edge']};
                border-radius: 13px;
            }}
            QLabel {{ color: {_COLORS['text']}; background: transparent; border: 0; }}
            QLabel#brand {{ color: {_COLORS['dim']}; font-size: 11px; letter-spacing: 1px; }}
            QLabel#model {{ color: {_COLORS['dim']}; font-size: 12px; }}
            QLabel#metric-title {{ color: {_COLORS['dim']}; font-size: 11px; }}
            QLabel#context-percent {{ font-size: 29px; font-weight: 600; }}
            QLabel#usage-percent {{ font-size: 20px; font-weight: 600; }}
            QLabel#detail, QLabel#tokens, QLabel#reset {{
                color: {_COLORS['dim']}; font-size: 11px;
            }}
            QLabel#chat-title {{ color: {_COLORS['dim']}; font-size: 11px; }}
            QLabel#state {{ color: {_COLORS['cyan']}; font-size: 10px; font-weight: 600; }}
            QProgressBar {{
                background: {_COLORS['track']}; border: 0; border-radius: 3px;
                min-height: 9px; max-height: 9px; text-align: left;
            }}
            QProgressBar::chunk {{
                border-radius: 3px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {_COLORS['cyan']}, stop:0.35 {_COLORS['purple']},
                    stop:0.65 {_COLORS['pink']}, stop:0.85 {_COLORS['orange']},
                    stop:1 {_COLORS['red']});
            }}
            QProgressBar#usage-five::chunk {{ background: {_COLORS['cyan']}; }}
            QProgressBar#usage-week::chunk {{ background: {_COLORS['purple']}; }}
            QPushButton {{
                background: transparent; border: 0; border-radius: 5px;
                color: {_COLORS['text']}; min-width: 26px; min-height: 26px;
                font-size: 16px;
            }}
            QPushButton:hover {{ background: {_COLORS['track']}; }}
            QPushButton:checked {{ color: {_COLORS['cyan']}; }}
            QPushButton#pin {{ font-size: 10px; min-width: 38px; padding: 0 4px; }}
            QPushButton#close {{ min-width: 26px; }}
            QFrame#footer {{ border-top: 1px solid {_COLORS['track']}; }}
        """)
        self.setObjectName("strip")

        outer = QVBoxLayout(self)
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

        self._model_label = _label("Codex", "Codex model")
        self._model_label.setObjectName("model")
        self._model_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self._model_label)

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

        metrics = QHBoxLayout()
        metrics.setSpacing(20)

        context_panel_widget = QWidget(self._card)
        context_panel = QVBoxLayout(context_panel_widget)
        context_panel.setContentsMargins(0, 0, 0, 0)
        context_panel.setSpacing(3)
        context_top = QHBoxLayout()
        context_name = _label("Context", "Context usage heading")
        context_name.setObjectName("metric-title")
        context_top.addWidget(context_name)
        self._context_percent = _label("—", "Context percent")
        self._context_percent.setObjectName("context-percent")
        self._context_percent.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        context_top.addWidget(self._context_percent)
        context_panel.addLayout(context_top)
        self._context_bar = self._bar("context")
        context_panel.addWidget(self._context_bar)
        self._tokens = _label("Token count unavailable", "Context token count")
        self._tokens.setObjectName("tokens")
        context_panel.addWidget(self._tokens)
        metrics.addWidget(context_panel_widget, 3)

        self._add_usage_panel(metrics, "5h", "five_hour", "usage-five")
        self._add_usage_panel(metrics, "Weekly", "seven_day", "usage-week")
        card_layout.addLayout(metrics)

        footer = QFrame(self._card)
        footer.setObjectName("footer")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 8, 0, 0)
        footer_layout.setSpacing(12)
        self._chat_title = _label(self._title_text, "Codex session title")
        self._chat_title.setObjectName("chat-title")
        self._detail = _label("Waiting for Codex telemetry", "Status details")
        self._detail.setObjectName("detail")
        self._detail.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._state = _label("WAITING", "Status state")
        self._state.setObjectName("state")
        footer_layout.addWidget(self._chat_title, 1)
        footer_layout.addWidget(self._detail)
        footer_layout.addWidget(self._state)
        card_layout.addWidget(footer)

    @staticmethod
    def _bar(object_name: str) -> QProgressBar:
        bar = QProgressBar()
        bar.setObjectName(object_name)
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(False)
        bar.setAccessibleName(f"{object_name.replace('-', ' ')} percentage")
        return bar

    def _add_usage_panel(self, metrics: QHBoxLayout, label: str, key: str, bar_name: str) -> None:
        panel_widget = QWidget(self._card)
        panel = QVBoxLayout(panel_widget)
        panel.setContentsMargins(0, 0, 0, 0)
        panel.setSpacing(3)
        heading = _label(label, f"{label} usage heading")
        heading.setObjectName("metric-title")
        panel.addWidget(heading)
        percent = _label("—", f"{label} usage percent")
        percent.setObjectName("usage-percent")
        panel.addWidget(percent)
        bar = self._bar(bar_name)
        panel.addWidget(bar)
        reset = _label("Usage unavailable", f"{label} reset time")
        reset.setObjectName("reset")
        panel.addWidget(reset)
        metrics.addWidget(panel_widget, 1)
        self._usage_labels[key] = (percent, bar, reset)
        self._usage_panels[key] = panel_widget

    def _set_pinned(self, pinned: bool) -> None:
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, pinned)
        self._pin_button.setText("Unpin" if pinned else "Pin")
        self._pin_button.setAccessibleName(
            "Unpin window from always-on-top" if pinned else "Pin window above others"
        )
        self._pin_button.setToolTip("Always on top" if pinned else "Pin above other windows")
        self.show()

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


def run_desktop(
    initial_status: dict[str, object],
    status_provider: Callable[[], dict[str, object]],
    title: str = "Codex",
) -> int:
    """Run the status strip and refresh its bound rollout every two seconds."""
    app = QApplication.instance() or QApplication(sys.argv[:1])
    window = StripWindow(initial_status, title)
    window.adjustSize()
    screen = app.primaryScreen()
    if screen is not None:
        available = screen.availableGeometry()
        x = available.x() + (available.width() - window.width()) // 2
        y = available.y() + available.height() - window.height() - 30
        window.move(x, y)
    window.show()

    timer = QTimer(window)
    timer.setInterval(2000)
    timer.timeout.connect(lambda: window.update_status(status_provider()))
    timer.start()
    return app.exec()
