"""自绘标题栏与无边框窗口辅助。

Windows 原生标题栏由本模块替代：左侧品牌，右侧最小化 / 最大化 / 关闭，
中间区域可拖动窗口、双击最大化。窗口本身用 FramelessWindowHint，
边缘缩放由 MainWindow.nativeEvent 处理 WM_NCHITTEST。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt5.QtCore import QPoint, QRectF, QSize, Qt
from PyQt5.QtGui import (
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QRegion,
)
from PyQt5.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from .theme import C

# Win32 命中测试常量
WM_NCHITTEST = 0x0084
HTCLIENT = 1
HTLEFT, HTRIGHT, HTTOP = 10, 11, 12
HTTOPLEFT, HTTOPRIGHT = 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17

RESIZE_BORDER = 7  # 边缘可缩放宽度（像素）

WINDOW_RADIUS = 11  # 窗口圆角


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


def app_icon() -> QIcon:
    """源码、便携程序和自解压安装器共用一份多尺寸图标。"""
    root = _base_dir()
    bundled = Path(getattr(sys, "_MEIPASS", root))
    for directory in (root, bundled):
        path = directory / "assets" / "hanlin-notes.ico"
        if path.is_file():
            return QIcon(str(path))
    return QIcon()


def rounded_mask(width: int, height: int, radius: int = WINDOW_RADIUS) -> QRegion:
    path = QPainterPath()
    path.addRoundedRect(0, 0, width, height, radius, radius)
    return QRegion(path.toFillPolygon().toPolygon())


class FrameEdge(QWidget):
    """覆盖在最上层的一条圆角描边，让无边框窗口有清晰的轮廓。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.NoFocus)

    def paintEvent(self, ev):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor(C["line"]))
        pen.setWidth(1)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(
            QRectF(0.5, 0.5, self.width() - 1, self.height() - 1),
            WINDOW_RADIUS,
            WINDOW_RADIUS,
        )


class TitleBar(QWidget):
    HEIGHT = 58

    def __init__(self, window, commands: QWidget):
        super().__init__(window)
        self.win = window
        self._press_global: QPoint | None = None
        self._press_window: QPoint | None = None
        self._dragging = False

        self.setObjectName("TitleBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(self.HEIGHT)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(12)
        brand = QWidget()
        brand.setFixedWidth(213)
        identity = QHBoxLayout(brand)
        identity.setContentsMargins(24, 0, 10, 0)
        identity.setSpacing(9)
        icon = QLabel(objectName="TitleIcon")
        icon.setPixmap(app_icon().pixmap(QSize(28, 28)))
        icon.setFixedSize(28, 28)
        identity.addWidget(icon)
        identity.addWidget(QLabel("寒淋笔记", objectName="TitleBrand"))
        identity.addStretch(1)
        lay.addWidget(brand)
        lay.addSpacing(12)
        lay.addWidget(commands, 1)
        lay.addSpacing(4)

        self.btn_min = self._win_button("─", "最小化", self.win.showMinimized)
        self.btn_max = self._win_button("□", "最大化", self.win.toggle_maximized)
        self.btn_close = self._win_button("✕", "关闭", self.win.close, "CloseBtn")
        window_buttons = QHBoxLayout()
        window_buttons.setSpacing(2)
        for button in (self.btn_min, self.btn_max, self.btn_close):
            window_buttons.addWidget(button)
        lay.addLayout(window_buttons)

        self.sync_max_button()


    def _win_button(self, text, tip, slot, name="WinBtn") -> QToolButton:
        b = QToolButton(objectName=name)
        b.setText(text)
        b.setToolTip(tip)
        b.setFixedSize(32, 32)
        b.setCursor(Qt.ArrowCursor)
        b.clicked.connect(slot)
        b.installEventFilter(self)
        return b

    def eventFilter(self, obj, ev):
        # 按钮上的双击不要触发窗口最大化
        if ev.type() == ev.MouseButtonDblClick and obj in (
                self.btn_min, self.btn_max, self.btn_close):
            return True
        return super().eventFilter(obj, ev)

    def sync_max_button(self):
        self.btn_max.setText("❐" if self.win.isMaximized() else "□")
        self.btn_max.setToolTip("向下还原" if self.win.isMaximized() else "最大化")

    # ---------- 拖动 ----------


    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._press_global = ev.globalPos()
            self._press_window = self.win.frameGeometry().topLeft()
            self._dragging = False

    def mouseMoveEvent(self, ev):
        if self._press_global is None or not (ev.buttons() & Qt.LeftButton):
            return
        delta = ev.globalPos() - self._press_global
        if not self._dragging:
            if delta.manhattanLength() < 4:
                return
            self._dragging = True
            if self.win.isMaximized():
                # 从最大化拖出：按鼠标相对宽度还原，并让指针落在标题栏上
                ratio = ev.globalPos().x() / max(1, self.win.width())
                self.win.showNormal()
                self.win.move(int(ev.globalPos().x() - self.win.width() * ratio),
                              int(ev.globalPos().y() - self.HEIGHT // 2))
                self._press_global = ev.globalPos()
                self._press_window = self.win.frameGeometry().topLeft()
                return
        self.win.move(self._press_window + delta)

    def mouseReleaseEvent(self, ev):
        self._press_global = None
        self._press_window = None
        self._dragging = False

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.win.toggle_maximized()


class DialogTitleBar(QWidget):
    """无边框对话框的顶部条：标题 + 关闭，可拖动。"""

    HEIGHT = 44

    def __init__(self, dialog, text: str):
        super().__init__(dialog)
        self.dlg = dialog
        self._press: QPoint | None = None
        self.setObjectName("DialogTitleBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(self.HEIGHT)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 0, 10, 0)
        lay.setSpacing(6)
        lay.addWidget(QLabel(text, objectName="DialogTitle"))
        lay.addStretch(1)
        self.btn_close = QToolButton(objectName="DialogClose")
        self.btn_close.setText("✕")
        self.btn_close.setFixedSize(30, 24)
        self.btn_close.setCursor(Qt.ArrowCursor)
        self.btn_close.clicked.connect(self.dlg.reject)
        lay.addWidget(self.btn_close)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._press = ev.globalPos() - self.dlg.frameGeometry().topLeft()

    def mouseMoveEvent(self, ev):
        if self._press is not None and ev.buttons() & Qt.LeftButton:
            self.dlg.move(ev.globalPos() - self._press)

    def mouseReleaseEvent(self, ev):
        self._press = None


class FramelessDialog(QDialog):
    """无边框对话框：自绘标题条 + 圆角 + 描边，内容放进 body。"""

    RADIUS = 12

    def __init__(self, parent=None, title: str = ""):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self._bar = DialogTitleBar(self, title)
        self.body = QWidget(objectName="DialogBody")
        self.body.setAttribute(Qt.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._bar)
        outer.addWidget(self.body, 1)

    def add_edge(self):
        self._edge = FrameEdge(self)
        self._edge.setGeometry(self.rect())
        self._edge.raise_()

    def resizeEvent(self, ev):  # noqa: N802
        super().resizeEvent(ev)
        self.setMask(rounded_mask(self.width(), self.height(), self.RADIUS))
        edge = getattr(self, "_edge", None)
        if edge is not None:
            edge.setGeometry(self.rect())
            edge.raise_()

    def showEvent(self, ev):  # noqa: N802
        super().showEvent(ev)
        self.setMask(rounded_mask(self.width(), self.height(), self.RADIUS))


class BrandPanel(QFrame):
    """首次打开与便携安装共享的品牌栏。"""

    def __init__(self):
        super().__init__(objectName="BrandPanel")
        self.setFixedWidth(184)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 16, 14, 16)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignCenter)
        icon = QLabel()
        icon.setPixmap(app_icon().pixmap(QSize(74, 74)))
        layout.addWidget(icon, alignment=Qt.AlignCenter)
        title = QLabel("寒淋笔记", objectName="BrandPanelName")
        layout.addWidget(title, alignment=Qt.AlignCenter)
