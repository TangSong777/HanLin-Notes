"""工作台的线条图标与页面切换。"""
from __future__ import annotations

from functools import lru_cache

from PyQt5.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, QTimer, QVariantAnimation
from PyQt5.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PyQt5.QtWidgets import QGraphicsOpacityEffect, QLabel, QStackedWidget, QWidget

from .motion import FAST, enabled as motion_enabled
from .theme import C


# 图形绘制在统一的 16 × 16 网格上。
_STROKES = {
    "all": (((6, 6), (11, 6)), ((6, 9), (11, 9)), ((6, 12), (9, 12))),
    "archived": (((2, 6), (14, 6)), ((6, 9), (10, 9))),
    "trash": (((2.5, 4.5), (13.5, 4.5)), ((6, 4.5), (6, 2.5), (10, 2.5), (10, 4.5)),
              ((4, 5), (5, 13.5), (11, 13.5), (12, 5)), ((7, 7), (7.2, 11)), ((9.3, 7), (9.1, 11))),
    "folder": (((2, 12.5), (2, 4), (6.5, 4), (8, 6), (14, 6), (14, 12.5), (2, 12.5)),),
    "back": (((8, 3.5), (3.5, 8), (8, 12.5)), ((4, 8), (13.5, 8))),
    "add": (((8, 3), (8, 13)), ((3, 8), (13, 8))),
    "chevron": (((4.5, 6.5), (8, 10), (11.5, 6.5)),),
    "sort": (((3, 4), (13, 4)), ((3, 8), (10, 8)), ((3, 12), (7, 12))),
    "select": (((5, 8), (7, 10), (11, 6)),),
    "search": (((10.5, 10.5), (14, 14)),),
    "lock": (((5, 8), (5, 6), (6, 4), (10, 4), (11, 6), (11, 8)),),
    "article": (((4, 2), (10, 2), (13, 5), (13, 14), (4, 14), (4, 2)),
                ((10, 2), (10, 5), (13, 5)), ((6, 8), (11, 8)), ((6, 11), (11, 11))),
    "note": (((5, 6), (11, 6)), ((5, 9), (10, 9))),
    "gallery": (((3, 11), (6.5, 7.5), (9, 10), (11, 8), (13, 10)),),
    "account": (((8.5, 7.5), (13.5, 7.5), (13.5, 10.5)), ((11, 8), (11, 10))),
    "link": (((6, 5), (8, 3), (11.5, 3), (13, 4.5), (13, 7), (10.5, 9.5)),
             ((5.5, 6.5), (3, 9), (3, 11.5), (4.5, 13), (8, 13), (10, 11)), ((6, 10), (10, 6))),
    "date": (((5.5, 2), (5.5, 5)), ((10.5, 2), (10.5, 5)), ((3, 7), (13, 7))),
    "pin": (((6, 2), (10, 2), (9.5, 6), (12, 9), (4, 9), (6.5, 6), (6, 2)),
            ((8, 9), (8, 14))),
    "clock": (((8, 4.5), (8, 8), (10.5, 9.2)),),
    "attention": (((8, 4.5), (8, 8.5)), ((8, 11.5), (8, 11.6))),
    "settings": (((3, 4), (13, 4)), ((3, 8), (13, 8)), ((3, 12), (13, 12)),
                 ((6, 2.5), (6, 5.5)), ((10, 6.5), (10, 9.5)), ((7.5, 10.5), (7.5, 13.5))),
    "eye": (((2, 8), (5, 5), (8, 4), (11, 5), (14, 8), (11, 11), (8, 12), (5, 11), (2, 8)),),
    "eye-off": (((2, 8), (5, 5), (8, 4), (11, 5), (14, 8), (11, 11), (8, 12), (5, 11), (2, 8)),
                ((3, 3), (13, 13))),
    "exit": (((7, 3), (3, 3), (3, 13), (7, 13)), ((6, 8), (14, 8)), ((11, 5), (14, 8), (11, 11))),
}
_BOXES = {
    "all": (3.5, 2, 10, 12),
    "archived": (2.5, 3, 11, 10),
    "select": (2.5, 2.5, 11, 11),
    "lock": (4, 8, 8, 5.5),
    "note": (2.5, 2.5, 11, 11),
    "gallery": (2.5, 3, 11, 10),
    "date": (2.5, 3.5, 11, 10),
}
_ELLIPSES = {
    "eye": (6.5, 6.5, 3, 3),
    "search": (2.5, 2.5, 9, 9),
    "account": (2.5, 4.5, 6, 6),
    "gallery": (9.5, 5.3, 0.8, 0.8),
    "clock": (2.3, 2.3, 11.4, 11.4),
    "attention": (2, 2, 12, 12),
}


@lru_cache(maxsize=96)
def _glyph_image(name: str, color: str) -> QPixmap:
    image = QPixmap(32, 32)
    image.setDevicePixelRatio(2)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor(color), 1.22, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    if name in _BOXES:
        painter.drawRoundedRect(QRectF(*_BOXES[name]), 1.4, 1.4)
    if name in _ELLIPSES:
        painter.drawEllipse(QRectF(*_ELLIPSES[name]))
    for path in _STROKES.get(name, ()):
        painter.drawPolyline(QPolygonF([QPointF(*point) for point in path]))
    painter.end()
    return image


def glyph(name: str, role: str = "muted") -> QIcon:
    icon = QIcon(_glyph_image(name, C[role]))
    icon.addPixmap(_glyph_image(name, C["accent_text"]), QIcon.Selected)
    return icon


class _PageSnapshot(QWidget):
    def __init__(self, parent, pixmap: QPixmap, direction: int):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.pixmap = pixmap
        self.direction = direction
        self.progress = 0.0

    def set_progress(self, fraction):
        self.progress = float(fraction)
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setOpacity(1.0 - self.progress)
        dpr = self.pixmap.devicePixelRatio()
        offset = round(-8 * self.direction * self.progress * dpr) / dpr
        painter.drawPixmap(QPointF(offset, 0), self.pixmap)


class WorkspaceStack(QStackedWidget):
    """给离开的页面做短暂过渡，目标页面从切换时起就可以操作。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._snapshot = None
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(160)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.valueChanged.connect(self._advance)
        self._animation.finished.connect(self.clear_transition)

    def open_page(self, page: QWidget, direction: int, *, animate: bool = True) -> None:
        previous = self.currentWidget()
        if previous is page:
            return
        self.clear_transition()
        snapshot = previous.grab() if previous and self.isVisible() and animate and motion_enabled() else QPixmap()
        self.setCurrentWidget(page)
        if snapshot.isNull():
            return
        self._snapshot = _PageSnapshot(self, snapshot, direction)
        self._snapshot.setGeometry(self.rect())
        self._snapshot.show()
        self._snapshot.raise_()
        self._animation.start()

    def _advance(self, fraction):
        if self._snapshot is not None:
            self._snapshot.set_progress(fraction)

    def clear_transition(self) -> None:
        self._animation.stop()
        if self._snapshot is not None:
            self._snapshot.hide()
            self._snapshot.deleteLater()
            self._snapshot = None

    def resizeEvent(self, event):  # noqa: N802
        self.clear_transition()
        super().resizeEvent(event)

    def hideEvent(self, event):  # noqa: N802
        self.clear_transition()
        super().hideEvent(event)


class Notice(QLabel):
    """右下角的短时反馈，无需移动鼠标或切换焦点。"""

    def __init__(self, parent):
        super().__init__(parent, objectName="Notice")
        self.setTextFormat(Qt.PlainText)
        self.setWordWrap(True)
        self.setMaximumWidth(360)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._dismiss)
        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._motion = QPropertyAnimation(self._opacity, b"opacity", self)
        self._motion.setDuration(FAST)
        self._motion.setEasingCurve(QEasingCurve.OutCubic)
        self._motion.finished.connect(self._hide_if_faded)
        self.hide()

    def present(self, text: str, ms: int):
        self._motion.stop()
        self.setText(text)
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self._opacity.setOpacity(1)
        if motion_enabled():
            self._fade(0.25, 1.0)
        self._timer.start(ms)

    def reposition(self):
        self.move(self.parentWidget().width() - self.width() - 32,
                  self.parentWidget().height() - self.height() - 27)

    def _hide_if_faded(self):
        if self._opacity.opacity() <= 0:
            self.hide()

    def _fade(self, start: float, end: float):
        self._motion.stop()
        self._motion.setStartValue(start)
        self._motion.setEndValue(end)
        self._motion.start()

    def _dismiss(self):
        if motion_enabled():
            self._fade(self._opacity.opacity(), 0.0)
        else:
            self.hide()
