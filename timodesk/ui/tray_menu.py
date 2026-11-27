"""Notification-area menu following the HanLin-ToDo tray appearance."""
from __future__ import annotations

import ctypes
import sys

from PyQt5.QtCore import QByteArray, QEasingCurve, QLineF, QPropertyAnimation, QRectF, Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import QMenu

from .motion import enabled as motion_enabled
from .theme import C, is_dark

_OUTER_RADIUS = 8
_ITEM_RADIUS = 5
_BORDER_WIDTH = 1

# Coordinates and strokes match Ui.GlyphGeometry in HanLin-ToDo.
_PATHS = {
    "plus": "M 2,7 H 12 M 7,2 V 12",
    "settings": "M 1,3.5 H 4 M 7,3.5 H 13 M 4,2 H 7 V 5 H 4 Z M 1,10.5 H 7 M 10,10.5 H 13 M 7,9 H 10 V 12 H 7 Z",
    "eye": "M 1.3,7 C 4.2,2.2 9.8,2.2 12.7,7 C 9.8,11.8 4.2,11.8 1.3,7 Z M 8.5,7 A 1.5,1.5 0 0 1 5.5,7 A 1.5,1.5 0 0 1 8.5,7 Z",
    "eye-off": "M 1.3,7 C 2,5.8 3,4.8 4.1,4.3 M 6.2,3.5 C 8.8,3.2 11.1,4.5 12.7,7 C 12,8.2 11,9.2 9.9,9.7 M 7.8,10.5 C 5.2,10.8 2.9,9.5 1.3,7 M 2,2 L 12,12",
    "file": "M 3,1.4 H 8.2 L 11,4.2 V 12.6 H 3 Z M 8.2,1.4 V 4.2 H 11 M 5.2,7 H 8.8 M 5.2,9.5 H 8.8",
    "exit": "M 6,2 H 2.5 V 12 H 6 M 5,7 H 12 M 9.5,4.5 L 12,7 L 9.5,9.5",
}


def _system_setting(code, value, size=0):
    if sys.platform != "win32":
        return False
    return bool(ctypes.windll.user32.SystemParametersInfoW(code, size, ctypes.byref(value), 0))


def _high_contrast():
    class HighContrast(ctypes.Structure):
        _fields_ = [("size", ctypes.c_uint), ("flags", ctypes.c_uint), ("scheme", ctypes.c_void_p)]

    state = HighContrast(ctypes.sizeof(HighContrast))
    return _system_setting(0x0042, state, state.size) and bool(state.flags & 1)


def _system_color(index):
    bgr = ctypes.windll.user32.GetSysColor(index)
    return QColor(bgr & 255, bgr >> 8 & 255, bgr >> 16 & 255)


class TrayMenu(QMenu):
    def __init__(self):
        super().__init__()
        self.setObjectName("HanLinTrayMenu")
        self.setFont(QFont("Microsoft YaHei UI", 9))
        self.setCursor(Qt.ArrowCursor)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setToolTipsVisible(False)
        self.setStyleSheet("""
            QMenu#HanLinTrayMenu { background: transparent; border: 0; padding: 4px 3px;
                                  font: 9pt 'Microsoft YaHei UI'; }
            QMenu#HanLinTrayMenu::item { padding: 2px 9px 3px 27px; min-height: 16px; }
            QMenu#HanLinTrayMenu::separator { height: 7px; margin: 0; }
        """)
        self.fade = QPropertyAnimation(self, b"windowOpacity", self)
        self.fade.setDuration(110)
        self.fade.setStartValue(0.85)
        self.fade.setEndValue(1.0)
        self.fade.setEasingCurve(QEasingCurve.OutCubic)
        self.pressed = None
        self.high_contrast = False
        self.icons = {}

    def showEvent(self, event):  # noqa: N802
        self.high_contrast = _high_contrast()
        animate = ctypes.c_int()
        if (not self.high_contrast and motion_enabled() and
                _system_setting(0x1002, animate) and animate.value):  # SPI_GETMENUANIMATION
            self.setWindowOpacity(0.85)
            self.fade.start()
        super().showEvent(event)

    def hideEvent(self, event):  # noqa: N802
        self.fade.stop()
        self.setWindowOpacity(1.0)
        self.pressed = None
        super().hideEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        self.pressed = self.actionAt(event.pos())
        self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        self.pressed = None
        self.update()
        super().mouseReleaseEvent(event)

    def _icon(self, name, color):
        key = name, color.name()
        if key not in self.icons:
            data = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 14 14">'
                    f'<path d="{_PATHS[name]}" stroke="{key[1]}" stroke-width="1.25" '
                    'stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>')
            if len(self.icons) >= 32:
                self.icons.clear()
            self.icons[key] = QSvgRenderer(QByteArray(data.encode("ascii")))
        return self.icons[key]

    def paintEvent(self, event):  # noqa: N802
        dark = is_dark()
        surface, edge, ink, muted, alert, hover, press, line = map(QColor, (
            (C["card"], C["card_border"], C["ink"], C["muted"], C["accent_text"],
             C["hover_bg"], C["press_bg"], C["line"]) if dark else
            ("#FFFCF9", "#E2DFDD", "#3C3C3C", "#736460", "#93444D", "#F9E0DE", "#F5D1CF", "#F1EFEC")
        ))
        if self.high_contrast:
            surface = _system_color(4)  # COLOR_MENU
            edge = _system_color(6)     # COLOR_WINDOWFRAME
            ink = alert = _system_color(7)
            muted = _system_color(17)
            hover = press = _system_color(13)
            line = muted
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(self.font())
        painter.setPen(QPen(edge, _BORDER_WIDTH))
        painter.setBrush(surface)
        inset = _BORDER_WIDTH / 2
        outline_radius = _OUTER_RADIUS - inset
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(inset, inset, -inset, -inset),
                                outline_radius, outline_radius)
        for action in self.actions():
            if not action.isVisible():
                continue
            rect = QRectF(self.actionGeometry(action))
            if action.isSeparator():
                painter.setPen(line)
                painter.drawLine(QLineF(rect.left() + 7, rect.center().y(),
                                        rect.right() - 7, rect.center().y()))
                continue
            selected = action.isEnabled() and action == self.activeAction()
            if selected:
                painter.setPen(Qt.NoPen)
                painter.setBrush(press if action == self.pressed else hover)
                painter.drawRoundedRect(rect.adjusted(2, 1, -2, -1), _ITEM_RADIUS, _ITEM_RADIUS)
            name = action.data()
            text_color = muted if not action.isEnabled() else alert if name == "exit" else ink
            icon_color = alert if name == "exit" else muted
            if selected and self.high_contrast:
                text_color = icon_color = _system_color(14)
            if name in _PATHS:
                self._icon(name, icon_color).render(painter, QRectF(rect.left() + 7, rect.center().y() - 7, 14, 14))
            painter.setPen(text_color)
            painter.drawText(rect.adjusted(27, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft, action.text())
