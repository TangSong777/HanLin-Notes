"""Application tooltips with HanLin's shared tooltip appearance."""
from __future__ import annotations

from math import ceil

from PyQt5.QtCore import QEvent, QObject, QPoint, QRect, QRectF, Qt, QTimer
from PyQt5.QtGui import QAbstractTextDocumentLayout, QColor, QPainter, QPalette, QPen, QTextDocument, QTextOption
from PyQt5.QtWidgets import QApplication, QAbstractItemView, QWidget

from .theme import C


class _TipBubble(QWidget):
    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.document = QTextDocument(self)
        self.document.setDocumentMargin(0)
        font = QApplication.font()
        font.setPixelSize(12)
        self.document.setDefaultFont(font)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.document.setDefaultTextOption(option)

    def show_tip(self, text, point):
        screen = QApplication.screenAt(point) or QApplication.primaryScreen()
        work = screen.availableGeometry().adjusted(4, 4, -4, -4)
        self.document.setPlainText(text)
        self.document.setTextWidth(-1)
        self.document.setTextWidth(max(1, min(280, work.width() - 20, ceil(self.document.idealWidth()))))
        size = self.document.size()
        self.resize(ceil(size.width()) + 20, min(work.height(), ceil(size.height()) + 14))
        x = max(work.left(), min(point.x() + 12, work.right() + 1 - self.width()))
        y = point.y() + 20
        if y + self.height() > work.bottom() + 1:
            y = point.y() - self.height() - 8
        self.move(x, max(work.top(), min(y, work.bottom() + 1 - self.height())))
        self.show()
        self.raise_()

    def paintEvent(self, _event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(C["card_border"]), 1))
        painter.setBrush(QColor(C["card"]))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 7, 7)
        painter.translate(10, 7)
        painter.setClipRect(0, 0, self.width() - 20, self.height() - 14)
        context = QAbstractTextDocumentLayout.PaintContext()
        context.palette.setColor(QPalette.Text, QColor(C["ink"]))
        self.document.documentLayout().draw(painter, context)


class HoverTips(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.bubble = None
        self.owner = None
        self.anchor = QRect()
        self.expire = QTimer(self)
        self.expire.setSingleShot(True)
        self.expire.timeout.connect(self.hide)
        app.aboutToQuit.connect(self.hide)
        app.applicationStateChanged.connect(self.hide)

    def hide(self, *_):
        self.expire.stop()
        self.owner = None
        if self.bubble is not None:
            self.bubble.hide()

    def eventFilter(self, watched, event):  # noqa: N802
        kind = event.type()
        if kind == QEvent.ToolTip and isinstance(watched, QWidget):
            return self._show(watched, event)
        if self.owner is not None:
            if kind in (QEvent.MouseButtonPress, QEvent.Wheel, QEvent.KeyPress):
                self.hide()
            elif kind == QEvent.MouseMove and not self.anchor.contains(event.globalPos()):
                self.hide()
            elif watched is self.owner and kind in (QEvent.Leave, QEvent.Hide, QEvent.ToolTipChange):
                self.hide()
        return False

    def _show(self, widget, event):
        position = event.globalPos()
        owner = widget
        parent = widget.parentWidget()
        view = widget if isinstance(widget, QAbstractItemView) else parent
        text = ""
        rect = QRect(widget.mapToGlobal(QPoint()), widget.size())
        if isinstance(view, QAbstractItemView) and widget in (view.viewport(), view):
            viewport = view.viewport()
            index = view.indexAt(viewport.mapFromGlobal(position))
            text = index.data(Qt.ToolTipRole) if index.isValid() else ""
            if text:
                owner = viewport
                item = view.visualRect(index).intersected(viewport.rect())
                rect = QRect(viewport.mapToGlobal(item.topLeft()), item.size())
        if not text:
            text = widget.toolTip()
        if not text:
            return False
        if self.bubble is None:
            self.bubble = _TipBubble()
        self.owner = owner
        self.anchor = rect
        self.bubble.show_tip(str(text), position)
        self.expire.start(max(4000, min(12000, len(str(text)) * 65)))
        event.accept()
        return True
