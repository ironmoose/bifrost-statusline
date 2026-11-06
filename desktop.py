"""Render Codex and Claude Code status in a small, movable PySide6 window."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import math
import sys

from PySide6.QtCore import QPoint, QPointF, QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QPainter, QPainterPath, QPen
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


_COLORS = {
    "surface": "#090f20",
    "text": "#f0eaf7",
    "dim": "#b8afc6",
    "track": "#25304a",
    "cyan": "#00e5ff",
    "purple": "#a25eff",
    "pink": "#ff2e97",
    "orange": "#ff6c3a",
    "red": "#ff3a5c",
}

# Every metric column is this wide, so context, 5h and weekly line up.
_METRIC_WIDTH = 124
_STRIP_HEIGHT = 96


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


def _usage_rgb(percent: float) -> tuple[int, int, int]:
    """Interpolate cyan through orange to magenta as usage increases."""
    stops = ((0, (0, 229, 255)), (65, (255, 108, 58)), (100, (255, 46, 209)))
    percent = max(0, min(100, percent))
    for (start, left), (end, right) in zip(stops, stops[1:]):
        if percent <= end:
            fraction = (percent - start) / (end - start)
            return tuple(round(a + fraction * (b - a)) for a, b in zip(left, right))
    return stops[-1][1]


def _update_percent(label: QLabel, value: object) -> None:
    number = _number(value)
    label.setText(_percent_text(number))
    color = _COLORS["dim"] if number is None else _rgb(_usage_rgb(number))
    label.setStyleSheet(f"color: {color};")


def _block_colors(percent: int, blocks: int) -> list[tuple]:
    """Colors of the lit blocks, each by its position on the ramp, so a bar heats up only as it fills."""
    lit = math.ceil(max(0, min(100, percent)) * blocks / 100)
    return [_usage_rgb((index + 0.5) * 100 / blocks) for index in range(lit)]


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


class _GridFrame(QFrame):
    """Static clipped frame and a faint perspective grid beneath the instruments."""

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width() - 1, self.height() - 1
        outline = QPainterPath(QPointF(10, 0))
        for x, y in [(w, 0), (w, h - 10), (w - 10, h), (0, h), (0, 10)]:
            outline.lineTo(x, y)
        outline.closeSubpath()
        painter.fillPath(outline, QColor(_COLORS["surface"]))
        painter.setClipPath(outline)
        painter.setPen(QPen(QColor("#123047"), 0.7))
        horizon = h - 15
        for x in range(-w, w * 2, 60):
            painter.drawLine(QPointF(w * 0.65, horizon), QPointF(x, h))
        for y in [horizon, h - 11, h - 6, h - 1]:
            painter.drawLine(QPointF(0, y), QPointF(w, y))
        painter.setPen(QPen(QColor(_COLORS["cyan"]), 1))
        painter.drawPath(outline)
        painter.setPen(QPen(QColor(_COLORS["purple"]), 1))
        painter.drawLine(QPointF(w * 0.7, h), QPointF(w - 10, h))
        painter.drawLine(QPointF(w - 10, h), QPointF(w, h - 10))
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
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(8)
        self._title = _label(title, "Source title")
        self._title.setObjectName("source-title")
        header.addWidget(self._title, 1)
        self._session_label = _label("Session unavailable", f"{title} context session")
        self._session_label.setObjectName("session")
        self._session_label.setFixedWidth(196)
        # Emoji fallback fonts have taller metrics than plain monospace text.
        self._session_label.setFixedHeight(22)
        self._session_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self._session_label)
        self._state = _label("WAITING", "Status state")
        self._state.setObjectName("state")
        header.addWidget(self._state)
        layout.addLayout(header)

        metrics = QHBoxLayout()
        metrics.setSpacing(12)

        context_panel, self._context_percent, self._context_bar, self._tokens = self._metric_panel(
            "Context", "context", "Tokens unavailable"
        )
        metrics.addWidget(context_panel)
        self._add_usage_panel(metrics, "5h", "five_hour", "usage-five")
        self._add_usage_panel(metrics, "Weekly", "seven_day", "usage-week")
        metrics.addStretch(1)
        layout.addLayout(metrics)

        self._detail = _label(f"Waiting for {title} telemetry", "Status details", self)
        self._detail.hide()

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
        values = QHBoxLayout()
        values.setContentsMargins(0, 0, 0, 0)
        values.addWidget(heading)
        percent = _label("—", f"{label} usage percent")
        percent.setObjectName("metric-percent")
        percent.setAlignment(Qt.AlignmentFlag.AlignRight)
        values.addWidget(percent)
        panel.addLayout(values)
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
        session_name = str(status.get("session_name") or "Session unavailable")
        model = str(status.get("model") or "Model unavailable")
        session_id = str(status.get("session_id") or "Unknown session ID")
        self._session_label.setText(self._session_label.fontMetrics().elidedText(
            session_name, Qt.TextElideMode.ElideRight, self._session_label.width()
        ))
        self._session_label.setToolTip(f"{session_name}\nModel: {model}\nSession: {session_id}\n{status.get('cwd') or ''}")

        context_percent = _number(status.get("context_percent"))
        _update_percent(self._context_percent, context_percent)
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
                _update_percent(percent_label, None)
                bar.setValue(0)
                reset_label.setText("Usage unavailable")
                continue
            self._usage_panels[key].show()
            percentage = _number(entry.get("used_percent"))
            _update_percent(percent_label, percentage)
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
        self.setHidden(state_label == "CLOSED")
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
        self.setToolTip(self._detail.text())


class StripWindow(QWidget):
    """Show a live, frameless status strip with one panel per source."""

    def __init__(
        self,
        sources: Sequence[tuple[str, dict[str, object]]],
        settings: QSettings | None = None,
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setWindowTitle("Bifrost desktop status")
        self.setAccessibleName("Bifrost status strip")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.panels: list[SourcePanel] = []
        self.settings = settings
        # Save once a drag settles, so a killed strip still reopens where it was.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save_position)
        self._build_ui([title for title, _ in sources])
        self.update_sources([status for _, status in sources])

    def _save_position(self) -> None:
        if self.settings is not None:
            self.settings.setValue("horizontal-position", self.pos())
            self.settings.sync()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if self.isVisible():
            self._save_timer.start()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Sources and quota columns can return while the strip sits at the right edge.
        if self.isVisible() and self.screen() is not None:
            bounds = self.screen().geometry()
            self.move(
                max(bounds.left(), min(self.x(), bounds.right() - self.width() + 1)),
                max(bounds.top(), min(self.y(), bounds.bottom() - self.height() + 1)),
            )

    def closeEvent(self, event) -> None:
        self._save_timer.stop()
        self._save_position()
        super().closeEvent(event)

    def update_sources(self, statuses: Sequence[dict[str, object]]) -> None:
        """Update each panel, showing the idle line only when every source is closed."""
        for panel, status in zip(self.panels, statuses):
            panel.update_status(status or {})
        self._idle.setHidden(any(not panel.isHidden() for panel in self.panels))

    def _build_ui(self, titles: list[str]) -> None:
        # The platform's own fixed-width family, named exactly so Qt needn't resolve an alias.
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
        self.setStyleSheet(f"""
            QWidget#strip {{
                background: transparent; color: {_COLORS['text']};
                font-family: "{mono}"; font-size: 11px;
            }}
            QLabel {{
                color: {_COLORS['text']}; background: transparent; border: 0;
                font-family: "{mono}";
            }}
            QLabel#brand {{ color: {_COLORS['cyan']}; font-size: 11px; font-weight: 600; letter-spacing: 2px; }}
            QLabel#session {{ color: {_COLORS['dim']}; font-size: 11px; }}
            QLabel#metric-title {{ color: {_COLORS['dim']}; font-size: 11px; }}
            QLabel#metric-percent {{ font-size: 14px; font-weight: 600; }}
            QLabel#detail, QLabel#caption {{
                color: {_COLORS['dim']}; font-size: 10px;
            }}
            QLabel#state {{ color: {_COLORS['cyan']}; font-size: 10px; font-weight: 600; }}
            QPushButton {{
                background: transparent; border: 0; border-radius: 0px;
                color: {_COLORS['text']}; min-width: 26px; min-height: 20px;
                font-size: 16px;
            }}
            QPushButton:hover {{ background: {_COLORS['track']}; }}
            QPushButton:checked {{ color: {_COLORS['cyan']}; }}
            QPushButton#pin {{ font-size: 10px; min-width: 38px; padding: 0 4px; }}
            QPushButton#close {{ min-width: 26px; }}
            QFrame#source {{ border: 0; border-left: 1px solid {_COLORS['track']}; }}
            QLabel#source-title {{ color: {_COLORS['text']}; font-size: 11px; font-weight: 600; }}
        """)
        self.setObjectName("strip")

        outer = QVBoxLayout(self)
        # Width follows visible columns; the card keeps height constant across titles and idle states.
        outer.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(0)

        self._card = _GridFrame(self)
        self._card.setObjectName("card")
        self._card.setFixedHeight(_STRIP_HEIGHT - 4)
        card_layout = QHBoxLayout(self._card)
        card_layout.setContentsMargins(12, 5, 12, 9)
        card_layout.setSpacing(12)
        outer.addWidget(self._card)

        header = QVBoxLayout()
        header.setSpacing(4)
        brand = _label("BIFROST", "Bifrost brand")
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
        controls = QHBoxLayout()
        controls.setSpacing(0)
        controls.addWidget(self._pin_button)

        close_button = QPushButton("×", self._card)
        close_button.setObjectName("close")
        close_button.setAccessibleName("Close Bifrost strip")
        close_button.setToolTip("Close")
        close_button.clicked.connect(self.close)
        controls.addWidget(close_button)
        header.addLayout(controls)
        card_layout.addLayout(header)

        for title in titles:
            panel = SourcePanel(title, self._card)
            card_layout.addWidget(panel)
            self.panels.append(panel)

        self._idle = _label(f"Waiting for {' or '.join(titles)} to start", "Idle status")
        self._idle.setObjectName("detail")
        card_layout.addWidget(self._idle)

    def _set_pinned(self, pinned: bool) -> None:
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, pinned)
        self._pin_button.setText("Unpin" if pinned else "Pin")
        self._pin_button.setAccessibleName(
            "Unpin window from always-on-top" if pinned else "Pin window above others"
        )
        self._pin_button.setToolTip("Always on top" if pinned else "Pin above other windows")
        self.show()


StatusProvider = Callable[[], dict[str, object]]


def place_window(window: StripWindow) -> None:
    """Restore the horizontal position, keeping the whole strip on its screen."""
    window.adjustSize()
    saved = window.settings.value("horizontal-position") if window.settings is not None else None
    screen = QGuiApplication.screenAt(saved + QPoint(20, 20)) if isinstance(saved, QPoint) else None
    if screen is not None:
        bounds = screen.geometry()
        window.move(
            max(bounds.left(), min(saved.x(), bounds.right() - window.width() + 1)),
            max(bounds.top(), min(saved.y(), bounds.bottom() - window.height() + 1)),
        )
        return
    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        bounds = screen.geometry()
        window.move(bounds.right() - window.width() - 7, bounds.bottom() - window.height() - 7)


def run_desktop(sources: Sequence[tuple[str, dict[str, object], StatusProvider]]) -> int:
    """Run the strip with one panel per `(title, initial_status, provider)`, refreshed every two seconds."""
    app = QApplication.instance() or QApplication(sys.argv[:1])
    window = StripWindow(
        [(title, initial) for title, initial, _ in sources], QSettings("bifrost", "desktop-strip")
    )
    # Place after showing: before that, macOS Qt still allows for a title bar, and the strip creeps up 20px a launch.
    window.show()
    place_window(window)

    def refresh() -> None:
        window.update_sources([provider() for _, _, provider in sources])

    timer = QTimer(window)
    timer.setInterval(2000)
    timer.timeout.connect(refresh)
    timer.start()
    return app.exec()
