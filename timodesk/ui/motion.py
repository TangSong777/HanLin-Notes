"""统一的动效参数与小工具。

动画只做「让状态变化看得见」这件事：时长都在 100–260ms 之间，切换页面、悬停、
弹出对话框各用一种节奏；系统关掉动画（Windows「显示动画」）时全部退化为瞬时切换。
"""
from __future__ import annotations

import sys

from PyQt5.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation, QVariantAnimation, Qt
from PyQt5.QtGui import QColor, QCursor
from PyQt5.QtWidgets import QApplication, QGraphicsOpacityEffect

# 时长（毫秒）：FAST 用于 hover / 状态微调，BASE 用于页面与弹窗，SLOW 用于较大的位移。
FAST = 130
BASE = 190
SLOW = 240


def enabled() -> bool:
    """系统是否允许播放动画。"""
    if sys.platform == "win32":
        from ctypes import byref, windll
        from ctypes.wintypes import BOOL

        flag = BOOL()
        # SPI_GETCLIENTAREAANIMATION = 0x1042
        if windll.user32.SystemParametersInfoW(0x1042, 0, byref(flag), 0):
            return bool(flag.value)
    return QApplication.isEffectEnabled(Qt.UI_General)


def duration(ms: int) -> int:
    return ms if enabled() else 0


def mix(a: str, b: str, t: float) -> QColor:
    """两个颜色按 t（0–1）线性混合。"""
    t = max(0.0, min(1.0, float(t)))
    ca, cb = QColor(a), QColor(b)
    return QColor(
        round(ca.red() + (cb.red() - ca.red()) * t),
        round(ca.green() + (cb.green() - ca.green()) * t),
        round(ca.blue() + (cb.blue() - ca.blue()) * t),
        round(ca.alpha() + (cb.alpha() - ca.alpha()) * t),
    )


def fade_in(widget, ms: int = BASE, start: float = 0.0, end: float = 1.0) -> QPropertyAnimation | None:
    """把控件从不透明度的 start 淡到 end，结束后摘掉 effect。

    注意：Qt 的 QGraphicsOpacityEffect 会让子控件离屏渲染，只适合小面积、短时间使用。
    """
    previous = getattr(widget, "_fade_anim", None)
    if previous is not None:
        previous.stop()
        previous.deleteLater()
        widget._fade_anim = None
    if not enabled():
        widget.setGraphicsEffect(None)
        return None
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(start)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(ms)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setEasingCurve(QEasingCurve.OutCubic)

    def _done():
        try:
            if widget._fade_anim is anim:
                widget._fade_anim = None
                widget.setGraphicsEffect(None)
                anim.deleteLater()
        except RuntimeError:
            pass

    anim.finished.connect(_done)
    widget._fade_anim = anim  # 持有引用，避免被回收
    anim.start()
    return anim


class SmoothWheel(QObject):
    """缓动普通滚轮，保留触控板、滚动条和键盘的原生控制。"""

    def __init__(self, area, *, wheel_scale=1.0, wheel_duration=FAST):
        super().__init__(area)
        self.wheel_scale = wheel_scale
        self.wheel_duration = wheel_duration
        self.viewport = area.viewport()
        self.bar = area.verticalScrollBar()
        self.animation = QVariantAnimation(self)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.animation.valueChanged.connect(self._apply)
        self.animation.finished.connect(self._stop)
        self.bar.sliderPressed.connect(self._stop)
        self.bar.rangeChanged.connect(self._stop)
        self.bar.valueChanged.connect(self._external_change)
        self.target = None
        self._applying = False
        self.viewport.installEventFilter(self)
        area.installEventFilter(self)

    def _apply(self, value):
        self._applying = True
        try:
            self.bar.setValue(int(value))
        finally:
            self._applying = False

    def _stop(self, *_):
        self.target = None
        self.animation.stop()

    def _external_change(self, _):
        if not self._applying:
            self._stop()

    def scroll_to(self, value, ms=FAST):
        self._stop()
        value = max(self.bar.minimum(), min(self.bar.maximum(), int(value)))
        current = self.bar.value()
        if not enabled() or abs(value - current) < 2:
            self.bar.setValue(value)
            return
        self.target = value
        self.animation.setDuration(ms)
        self.animation.setStartValue(current)
        self.animation.setEndValue(value)
        self.animation.start()

    def eventFilter(self, obj, event):  # noqa: N802
        kind = event.type()
        if kind in (QEvent.Hide, QEvent.KeyPress, QEvent.MouseButtonPress, QEvent.ScrollPrepare):
            self._stop()
        if obj is not self.viewport or kind != QEvent.Wheel:
            return False
        angle = event.angleDelta()
        lines = QApplication.wheelScrollLines()
        if ((self.wheel_scale == 1 and not enabled()) or not lines or not event.pixelDelta().isNull() or
                event.phase() != Qt.NoScrollPhase or event.modifiers() != Qt.NoModifier or
                not angle.y() or abs(angle.x()) >= abs(angle.y())):
            self._stop()
            return False
        current = self.bar.value()
        step = max(1, round(abs(angle.y()) * max(1, self.bar.singleStep()) * lines * self.wheel_scale / 120))
        if angle.y() > 0:
            step = -step
        ahead = (self.target if self.target is not None else current) - current
        base = self.target if step * ahead > 0 else current
        limit = max(self.viewport.height(), abs(step))
        value = max(current - limit, min(current + limit, base + step))
        value = max(self.bar.minimum(), min(self.bar.maximum(), value))
        if value == current:
            self._stop()
            return False
        self.scroll_to(value, self.wheel_duration)
        event.accept()
        return True


class HoverTracker(QObject):
    """给列表提供带过渡的悬停进度：0 = 没指到，1 = 完全指到。

    列表只有一个鼠标位置，所以只追踪「当前这一行」；换行时旧行直接回到 0，
    新行从 0 渐入——比每行各自维护一个进度更快，也更稳定。
    """

    def __init__(self, view, duration_ms: int = FAST):
        super().__init__(view)
        self.view = view
        self.row = -1
        self.progress = 0.0
        self._anim = QVariantAnimation(self)
        self.duration_ms = duration_ms
        self._anim.setDuration(duration(duration_ms))
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._apply)
        view.viewport().setMouseTracking(True)
        view.viewport().installEventFilter(self)
        view.verticalScrollBar().valueChanged.connect(self._refresh_at_cursor)
        view.destroyed.connect(self._on_destroyed)

    # ---- 外部读取 ----
    def progress_for(self, row: int) -> float:
        if row != self.row:
            return 0.0
        return self.progress

    # ---- 动画 ----
    def _apply(self, value):
        self.progress = float(value)
        self.view.viewport().update()

    def _to(self, target: float, start: float | None = None):
        target = float(target)
        if not enabled():
            self._anim.stop()
            self.progress = target
            self.view.viewport().update()
            return
        self._anim.stop()
        self._anim.setDuration(self.duration_ms)
        self._anim.setStartValue(self.progress if start is None else start)
        self._anim.setEndValue(target)
        self._anim.start()

    def _set_row(self, row: int):
        if row == self.row:
            return
        self.row = row
        if row < 0:
            self._to(0.0)
        else:
            self._to(1.0, start=0.0)

    def _on_destroyed(self, *_args):
        self.view = None

    def _refresh_at_cursor(self, *_):
        if self.view is not None and self.view.viewport().underMouse():
            viewport = self.view.viewport()
            index = self.view.indexAt(viewport.mapFromGlobal(QCursor.pos()))
            self._set_row(index.row() if index.isValid() else -1)

    # ---- 事件 ----
    def eventFilter(self, obj, event):  # noqa: N802
        if self.view is None or obj is not self.view.viewport():
            return False
        kind = event.type()
        if kind == QEvent.MouseMove:
            index = self.view.indexAt(event.pos())
            self._set_row(index.row() if index.isValid() else -1)
        elif kind in (QEvent.Leave, QEvent.Hide):
            self._set_row(-1)
        elif kind == QEvent.MouseButtonPress:
            # 按下时立刻消隐，避免点击瞬间还有一层高亮
            self._set_row(-1)
        return False


def attach_hover(view) -> HoverTracker:
    """给视图装上 hover 追踪，并返回它（重复调用返回已有的）。"""
    tracker = getattr(view, "hover", None)
    if tracker is None:
        tracker = HoverTracker(view)
        view.hover = tracker
    return tracker
