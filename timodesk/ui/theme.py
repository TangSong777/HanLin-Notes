"""HanLin-Notes 的浅色、深色配色与控件样式。"""
from __future__ import annotations

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import QProxyStyle, QStyle

LIGHT = {
    "canvas": "#f4efea",
    "sidebar": "#fbf6f1",
    "paper_alt": "#fbf6f1",
    "card": "#fffcf9",
    "card_soft": "#fbf6f1",
    "card_hover": "#fdf5f2",
    "card_border": "#eadfd7",
    "card_border_strong": "#dcccc2",
    "ink": "#292424",
    "ink_soft": "#514541",
    "muted": "#736460",
    "faint": "#8f7c76",
    "on_ink": "#fffcf9",
    "line": "#eadfd7",
    "line_soft": "#f1e8e1",
    "input_bg": "#f4efea",
    "hover_bg": "#f9e0de",
    "press_bg": "#f5d1cf",
    "checked_bg": "#fceeeb",
    "sel_bg": "#f3ded8",
    "hl_bg": "#f4d9d4",
    "accent": "#e48887",
    "accent_text": "#93444d",
    "accent_hover": "#93444d",
    "accent_pressed": "#773440",
    "on_accent": "#292424",
    "on_accent_hover": "#fffcf9",
    "scroll": "#dcccc2",
    "scroll_hover": "#ba9f94",
    "danger": "#b4302a",
    "danger_soft": "#f9e6e1",
}

DARK = {
    "canvas": "#1a1614",
    "sidebar": "#221d1a",
    "paper_alt": "#28211e",
    "card": "#221d1a",
    "card_soft": "#28211e",
    "card_hover": "#2d2521",
    "card_border": "#332b27",
    "card_border_strong": "#4a3c36",
    "ink": "#f2eae4",
    "ink_soft": "#d5c6be",
    "muted": "#b5a49c",
    "faint": "#a08e84",
    "on_ink": "#1a1614",
    "line": "#332b27",
    "line_soft": "#2d2521",
    "input_bg": "#2c2420",
    "hover_bg": "#342923",
    "press_bg": "#42322c",
    "checked_bg": "#392b26",
    "sel_bg": "#4c3531",
    "hl_bg": "#533735",
    "accent": "#f09a99",
    "accent_text": "#f09a99",
    "accent_hover": "#93444d",
    "accent_pressed": "#773440",
    "on_accent": "#292424",
    "on_accent_hover": "#fffcf9",
    "scroll": "#594841",
    "scroll_hover": "#7c645b",
    "danger": "#ef8b82",
    "danger_soft": "#3b2421",
}

C = dict(LIGHT)
_mode = "light"


def set_mode(mode: str) -> str:
    """原地更新 C，让自绘控件读取到生效的配色。"""
    global _mode
    _mode = "dark" if str(mode).lower() in ("dark", "深色", "夜间") else "light"
    C.clear()
    C.update(DARK if _mode == "dark" else LIGHT)
    return _mode


def current_mode() -> str:
    return _mode


def is_dark() -> bool:
    return _mode == "dark"


class _HanLinStyle(QProxyStyle):
    """为 Qt 自带的箭头和复选框绘制两套主题的状态。"""

    def drawPrimitive(self, element, option, painter, widget=None):
        if element in (QStyle.PE_IndicatorCheckBox, QStyle.PE_IndicatorItemViewItemCheck):
            self._draw_checkbox(option, painter)
        elif element in (QStyle.PE_IndicatorArrowDown, QStyle.PE_IndicatorSpinDown, QStyle.PE_IndicatorSpinUp):
            self._draw_arrow(option, painter, upward=element == QStyle.PE_IndicatorSpinUp)
        else:
            super().drawPrimitive(element, option, painter, widget)

    @staticmethod
    def _draw_arrow(option, painter, upward):
        center = QRectF(option.rect).center()
        dx = min(3.2, option.rect.width() / 2 - 0.5)
        dy = -1.6 if upward else 1.6
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if not option.state & QStyle.State_Enabled:
            painter.setOpacity(0.45)
        painter.setPen(QPen(QColor(C["muted"]), 1.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawPolyline(QPolygonF([
            center + QPointF(-dx, -dy), center + QPointF(0, dy), center + QPointF(dx, -dy),
        ]))
        painter.restore()

    @staticmethod
    def _draw_checkbox(option, painter):
        checked = option.state & (QStyle.State_On | QStyle.State_NoChange)
        rect = QRectF(option.rect).adjusted(0.8, 0.8, -0.8, -0.8)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if not option.state & QStyle.State_Enabled:
            painter.setOpacity(0.45)
        border = "accent" if checked or option.state & QStyle.State_MouseOver else "faint"
        painter.setPen(QPen(QColor(C[border]), 1))
        painter.setBrush(QColor(C["accent" if checked else "card"]))
        painter.drawRoundedRect(rect, 3, 3)
        if checked:
            center = rect.center()
            painter.setPen(QPen(QColor(C["on_accent"]), 1.35, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            if option.state & QStyle.State_NoChange:
                painter.drawLine(center + QPointF(-3, 0), center + QPointF(3, 0))
            else:
                painter.drawPolyline(QPolygonF([
                    center + QPointF(-3, 0), center + QPointF(-1, 2.2), center + QPointF(3.5, -2.6),
                ]))
        painter.restore()


def apply_theme(app, mode: str) -> None:
    """同时更新样式表和 Qt 自带控件使用的调色板。"""
    from PyQt5.QtGui import QPalette

    set_mode(mode)
    if not app.property("hanlinStyleInstalled"):
        app.setStyle(_HanLinStyle("Fusion"))
        app.setProperty("hanlinStyleInstalled", True)
    palette = app.style().standardPalette()
    for role, key in (
        (QPalette.Window, "canvas"),
        (QPalette.WindowText, "ink"),
        (QPalette.Base, "card"),
        (QPalette.AlternateBase, "card_soft"),
        (QPalette.Text, "ink"),
        (QPalette.Button, "card_soft"),
        (QPalette.ButtonText, "ink"),
        (QPalette.ToolTipBase, "ink"),
        (QPalette.ToolTipText, "on_ink"),
        (QPalette.PlaceholderText, "muted"),
        (QPalette.Highlight, "sel_bg"),
        (QPalette.HighlightedText, "accent_text"),
        (QPalette.Link, "accent_text"),
        (QPalette.LinkVisited, "accent_text"),
        (QPalette.Light, "card_hover"),
        (QPalette.Mid, "card_border_strong"),
        (QPalette.Dark, "scroll_hover"),
    ):
        palette.setColor(role, QColor(C[key]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(C["faint"]))

    app.setPalette(palette)
    app.setStyleSheet(app_qss())


# 笔记颜色标记沿用 Timo 的 color 字段，切换主题时保持原色。
NOTE_COLORS = [
    ("无", None),
    ("浅灰", "#e8e8e8"),
    ("灰", "#d4d4d4"),
    ("中灰", "#bfbfbf"),
    ("深灰", "#a3a3a3"),
    ("石墨", "#7a7a7a"),
    ("炭灰", "#565656"),
    ("墨黑", "#262626"),
]

FONT_UI = '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "PingFang SC", sans-serif'
FONT_TEXT = '"Microsoft YaHei", "Segoe UI", "PingFang SC", sans-serif'
FONT_MONO = '"Cascadia Code", "Consolas", "JetBrains Mono", monospace'


def _heading_font() -> tuple[str, int]:
    from PyQt5.QtGui import QFont, QFontInfo
    from PyQt5.QtWidgets import QApplication

    if QApplication.instance():
        for preferred in ("MF YueHei (Noncommercial)", "造字工房悦黑（非商用）"):
            if QFontInfo(QFont(preferred)).family() == preferred:
                return f'"{preferred}"', 400
    return FONT_UI, 600


def color_icon(col, size: int = 14):
    """画一个小圆角色块，给「颜色标记」菜单用。col 为 None 时画空心圈（表示无）。"""
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    if col:
        p.setBrush(QColor(col))
        p.setPen(Qt.NoPen)
    else:
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(C["card_border_strong"]), 1.2))
    p.drawRoundedRect(1, 1, size - 2, size - 2, 4, 4)
    p.end()
    return QIcon(pm)


def add_shadow(widget, blur: int = 26, y: int = 6, alpha: int = 24):
    """给控件挂一层柔和投影。只用在数量少、不随滚动重绘的控件上。"""
    from PyQt5.QtGui import QColor
    from PyQt5.QtWidgets import QGraphicsDropShadowEffect

    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setOffset(0, y)
    eff.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(eff)
    return eff


def app_qss(scale: float = 1.0) -> str:
    s = scale
    heading_font, heading_weight = _heading_font()
    return f"""
* {{ outline: none; }}
QWidget {{
    font-family: {FONT_UI};
    font-size: {int(13 * s)}px;
    color: {C['ink']};
}}
QMainWindow, #Root, #Body {{ background: {C['canvas']}; }}

/* ---------- 窗口标题 ---------- */
#TitleBar, QLabel#TitleBrand {{ background: transparent; border: none; }}
QLabel#TitleBrand, QLabel#DialogTitle, #ListHeader, QLineEdit#NoteTitle,
QLabel#WelcomeTitle, QLabel#SetupTitle, QLabel#AboutTitle {{
    font-family: {heading_font}; font-weight: {heading_weight};
}}
QLabel#TitleBrand {{ color: {C['ink']}; font-size: {int(14 * s)}px; }}
#DialogTitleBar {{
    background: {C['card']}; border-bottom: 1px solid {C['line_soft']};
    border-top-left-radius: 12px; border-top-right-radius: 12px;
}}
QLabel#DialogTitle {{ color: {C['ink']}; font-size: {int(13 * s)}px; }}
QLabel#WelcomeTitle {{ font-size: {int(30 * s)}px; }}
QLabel#SetupTitle {{ font-size: {int(26 * s)}px; }}
QLabel#AboutTitle {{ font-size: {int(19 * s)}px; }}
QLabel#WelcomeSub, QLabel#SetupHint {{ color: {C['muted']}; }}
QLabel#WelcomeDesc {{ color: {C['ink_soft']}; }}
QToolButton#WinBtn, QToolButton#CloseBtn, QToolButton#DialogClose {{ font-size: {int(12 * s)}px; }}
QToolButton#CloseBtn:hover, QToolButton#DialogClose:hover {{
    background: {C['danger_soft']}; color: {C['danger']};
}}

/* ---------- 卡片容器 ---------- */
#Sidebar {{
    background: {C['sidebar']};
}}
#ListPane, #EditorPane {{
    background: {C['card']};
}}
#Sidebar, #ListPane, #EditorPane {{
    border: 1px solid {C['card_border']};
    border-radius: 12px;
}}

/* ---------- 侧栏 ---------- */
#Brand {{
    font-family: {FONT_TEXT};
    font-size: {int(23 * s)}px;
    font-weight: 600;
    color: {C['ink']};
    letter-spacing: 0.4px;
}}
#BrandSub {{ color: {C['muted']}; font-size: {int(11 * s)}px; }}
#SideSection {{ color: {C['faint']}; font-size: {int(11 * s)}px; font-weight: 600; padding: 2px 6px; }}
#NewTopicBtn {{
    background: {C['card']}; border: 1px solid {C['card_border']};
    border-radius: 7px; color: {C['muted']}; padding: 8px 10px;
}}
#NewTopicBtn:hover {{ border-color: {C['card_border_strong']}; color: {C['ink']}; background: {C['hover_bg']}; }}

QListWidget#TopicList, QListWidget#NavList {{
    background: transparent; border: none;
}}
QListWidget#NavList::item {{
    border: 1px solid transparent;
    border-radius: 10px; padding: 10px 12px; margin: 2px 4px;
    color: {C['ink_soft']};
}}
QListWidget#NavList::item:selected {{
    background: {C['checked_bg']}; color: {C['accent_text']};
    border: 1px solid {C['card_border_strong']};
    border-left: 2px solid {C['accent']};
}}
QListWidget#NavList::item:hover:!selected {{
    background: {C['card_hover']};
}}

QListWidget#Wall {{ background: transparent; border: none; }}
QListWidget#Wall::item {{ border: 1px solid transparent; border-radius: 10px; margin: 2px; }}
QListWidget#Wall::item:hover {{ background: {C['card_hover']}; }}
QListWidget#Wall::item:selected {{ background: {C['checked_bg']}; border-color: {C['accent']}; }}

/* ---------- 中栏 ---------- */
#ListHeader {{ font-size: {int(18 * s)}px; color: {C['ink']}; }}
#CountLabel {{ color: {C['faint']}; font-size: {int(11 * s)}px; }}

QLineEdit {{
    background: {C['card']}; border: 1px solid {C['card_border']}; border-radius: 5px;
    padding: 8px 11px; color: {C['ink']};
    selection-background-color: {C['sel_bg']}; selection-color: {C['accent_text']};
}}
QLineEdit:focus {{ border-color: {C['accent_text']}; }}

QLineEdit#Search {{
    background: {C['input_bg']}; border: 1px solid transparent; border-radius: 5px;
    padding: 9px 14px;
}}
QLineEdit#Search:focus {{ background: {C['card']}; border-color: {C['accent_text']}; }}

QFrame#FindBar {{ background: {C['paper_alt']}; border: none; border-top: 1px solid {C['line']}; border-bottom: 1px solid {C['line']}; }}
QFrame#FindBar QLineEdit {{
    background: {C['card']}; border: 1px solid {C['card_border']}; border-radius: 5px;
    padding: 4px 9px;
}}
QFrame#FindBar QLineEdit:focus {{ border-color: {C['accent_text']}; }}
QFrame#FindBar QToolButton {{ border: none; border-radius: 6px; padding: 3px 6px; color: {C['muted']}; }}
QFrame#FindBar QToolButton:hover {{ background: {C['hover_bg']}; color: {C['ink']}; }}
#FindCount {{ color: {C['muted']}; font-size: {int(11 * s)}px; }}
QLabel#FieldLabel {{ color: {C['muted']}; font-size: {int(12 * s)}px; }}

#ListEmpty {{ color: {C['faint']}; font-size: {int(12 * s)}px; }}
QListWidget#NoteList {{ background: {C['card']}; border: none; padding: 4px 2px; }}
QListWidget#NoteList::item {{ border: none; margin: 0px; }}

QPushButton#NewNoteBtn {{
    border-top-left-radius: 7px; border-bottom-left-radius: 7px;
    border-top-right-radius: 0; border-bottom-right-radius: 0;
    padding: 9px 14px; font-weight: 600;
}}
QToolButton#SortBtn {{
    border: none; background: transparent; border-radius: 7px;
    color: {C['muted']}; font-size: {int(13 * s)}px;
}}
QToolButton#SortBtn:hover {{ background: {C['paper_alt']}; color: {C['ink']}; }}
QToolButton#SortBtn:pressed {{ background: {C['line']}; }}
QToolButton#SortBtn::menu-indicator {{ image: none; width: 0px; }}

QToolButton#NewTypeBtn {{
    border-top-right-radius: 7px; border-bottom-right-radius: 7px;
    border-top-left-radius: 0; border-bottom-left-radius: 0;
    font-size: {int(11 * s)}px;
}}
QToolButton#NewTypeBtn::menu-indicator {{ image: none; width: 0px; }}

/* ---------- 编辑区 ---------- */
#EditorTopBar {{ border-bottom: 1px solid {C['line_soft']}; }}
#TypeBadge {{
    color: {C['ink_soft']}; background: {C['hover_bg']};
    border-radius: 7px; padding: 3px 9px; font-size: {int(11 * s)}px; font-weight: 600;
}}
#SaveState {{ color: {C['faint']}; font-size: {int(11 * s)}px; }}

QLineEdit#NoteTitle {{
    background: transparent; border: none; border-bottom: 1px solid transparent; padding: 2px 0;
    font-size: {int(24 * s)}px; color: {C['ink']};
}}
QLineEdit#NoteTitle::placeholder {{ color: {C['faint']}; }}
QLineEdit#NoteTitle:focus {{ border-bottom-color: {C['accent_text']}; }}

QPlainTextEdit#Editor {{
    background: {C['card']}; border: none; padding: 18px 26px;
    font-family: {FONT_TEXT}; font-size: {int(15 * s)}px; color: {C['ink']};
    selection-background-color: {C['sel_bg']}; selection-color: {C['accent_text']};
}}
QTextBrowser#Preview {{
    background: {C['paper_alt']}; border: none; border-left: 1px solid {C['line']};
    padding: 18px 26px;
    font-family: {FONT_TEXT}; font-size: {int(15 * s)}px; color: {C['ink']};
    selection-background-color: {C['sel_bg']}; selection-color: {C['accent_text']};
}}

/* 格式栏：一条浅浅的卡片 */
#FormatBar {{
    background: {C['card']}; border: 1px solid {C['line_soft']};
    border-radius: 12px; margin: 6px 16px 2px 16px;
}}
QDateEdit, QTimeEdit {{
    background: {C['card']}; border: 1px solid {C['line']};
    border-radius: 5px; padding: 6px 8px; min-height: 18px;
    selection-background-color: {C['sel_bg']}; selection-color: {C['accent_text']};
}}
QDateEdit:focus, QTimeEdit:focus {{ border-color: {C['accent_text']}; }}

/* 类型页内部的小卡片（账号 / 日子 / 链接） */
#Card {{
    background: {C['card_soft']}; border: 1px solid {C['card_border']};
    border-radius: 12px;
}}
#Card QLabel {{ color: {C['muted']}; font-size: {int(12 * s)}px; background: transparent; border: none; }}
#Card QLineEdit {{
    background: transparent; border: none; border-radius: 5px; padding: 6px 4px;
    font-size: {int(15 * s)}px;
}}
#Card QLineEdit:focus {{ background: {C['card']}; }}

QToolButton {{
    background: transparent; border: none; border-radius: 7px; padding: 5px 7px; color: {C['ink_soft']};
}}
QToolButton:hover {{ background: {C['hover_bg']}; color: {C['ink']}; }}
QToolButton:checked {{ background: {C['checked_bg']}; color: {C['accent_text']}; }}
QToolButton:pressed {{ background: {C['press_bg']}; }}

/* ---------- 通用按钮 ---------- */
QPushButton {{
    background: {C['card']}; border: 1px solid {C['card_border_strong']}; border-radius: 7px;
    padding: 7px 14px; color: {C['ink']};
}}
QPushButton:hover {{ border-color: {C['accent_text']}; background: {C['hover_bg']}; }}
QPushButton:pressed {{ background: {C['press_bg']}; }}
QPushButton:default, QPushButton#Primary, QPushButton#WelcomePrimary,
QPushButton#NewNoteBtn, QToolButton#NewTypeBtn {{
    background: {C['accent']}; color: {C['on_accent']}; border: none;
}}
QPushButton:default:hover, QPushButton#Primary:hover, QPushButton#WelcomePrimary:hover,
QPushButton#NewNoteBtn:hover, QToolButton#NewTypeBtn:hover {{
    background: {C['accent_hover']}; color: {C['on_accent_hover']};
}}
QPushButton:default:pressed, QPushButton#Primary:pressed, QPushButton#WelcomePrimary:pressed,
QPushButton#NewNoteBtn:pressed, QToolButton#NewTypeBtn:pressed {{
    background: {C['accent_pressed']}; color: {C['on_accent_hover']};
}}
QToolButton#NewTypeBtn {{ border-left: 1px solid {C['accent_hover']}; }}
QPushButton#Plain, QPushButton#Primary, QPushButton#Danger {{ padding: 9px 22px; }}
QPushButton#Plain, QPushButton#WelcomePlain {{
    background: {C['card']}; border: 1px solid {C['card_border_strong']}; color: {C['ink']};
}}
QPushButton#Plain:hover, QPushButton#WelcomePlain:hover {{
    border-color: {C['accent_text']}; background: {C['hover_bg']};
}}
QPushButton#Plain:pressed, QPushButton#WelcomePlain:pressed {{ background: {C['press_bg']}; }}
QPushButton#WelcomePrimary, QPushButton#WelcomePlain {{ padding: 12px 18px; }}
QPushButton#WelcomePrimary {{ font-size: {int(14 * s)}px; font-weight: 600; }}
QPushButton#Link {{
    background: transparent; border: none; color: {C['accent_text']}; font-size: {int(12 * s)}px;
}}
QPushButton#Link:hover {{ background: {C['hover_bg']}; }}
QPushButton#Danger {{
    background: {C['danger_soft']}; color: {C['danger']}; border: 1px solid {C['danger']};
}}
QPushButton#Danger:hover {{ background: {C['card']}; }}
QPushButton#Danger:pressed {{ background: {C['press_bg']}; }}
QPushButton:disabled, QPushButton#Primary:disabled, QPushButton#Danger:disabled, QToolButton:disabled {{
    color: {C['faint']}; border-color: {C['line']}; background: {C['paper_alt']};
}}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 5px 2px; }}
QScrollBar::handle:vertical {{ background: {C['scroll']}; border-radius: 3px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {C['scroll_hover']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 5px; }}
QScrollBar::handle:horizontal {{ background: {C['scroll']}; border-radius: 3px; min-width: 24px; }}
QScrollBar::handle:horizontal:hover {{ background: {C['scroll_hover']}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

/* ---------- 菜单 ---------- */
QMenu {{
    background: {C['card']}; border: 1px solid {C['card_border']}; border-radius: 10px; padding: 6px;
}}
QMenu::item {{ padding: 7px 28px 7px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {C['checked_bg']}; color: {C['accent_text']}; }}
QMenu::item:disabled {{ color: {C['faint']}; }}
QMenu::separator {{ height: 1px; background: {C['line']}; margin: 5px 8px; }}

QToolTip {{
    background: {C['ink']}; color: {C['on_ink']}; border: none; padding: 6px 9px; border-radius: 7px;
}}

/* ---------- 对话框 ---------- */
QDialog {{ background: {C['canvas']}; }}
#DialogBody {{ background: {C['card']}; }}
QLineEdit#PathBox {{ padding: 9px 11px; }}
QLabel#ImportSummary {{ font-size: {int(15 * s)}px; font-weight: 600; }}
QLabel#LibraryStats {{
    background: {C['paper_alt']}; border: 1px solid {C['card_border']};
    border-radius: 8px; padding: 11px 14px;
}}
QLabel#KeyCap {{
    background: {C['input_bg']}; border-radius: 5px; padding: 3px 8px;
    font-family: "Consolas", "Microsoft YaHei UI"; font-size: {int(12 * s)}px;
}}
QListWidget#BackupList, QListWidget#TrashTopicList, QTreeWidget#DupeTree {{
    background: {C['card']}; border: 1px solid {C['card_border']};
    border-radius: 8px; padding: 4px;
}}
QListWidget#BackupList::item, QListWidget#TrashTopicList::item {{ padding: 7px 8px; border-radius: 6px; }}
QTreeWidget#DupeTree::item {{ height: 26px; }}
QListWidget#BackupList::item:selected, QListWidget#TrashTopicList::item:selected, QTreeWidget#DupeTree::item:selected {{
    background: {C['checked_bg']}; color: {C['accent_text']};
}}
QTreeWidget#DupeTree QHeaderView::section {{
    background: {C['card_soft']}; border: none; color: {C['muted']}; padding: 5px 6px; font-size: {int(11 * s)}px;
}}
QProgressBar {{ background: {C['line_soft']}; border: none; border-radius: 3px; }}
QProgressBar::chunk {{ background: {C['accent']}; border-radius: 3px; }}
QComboBox {{
    background: {C['card']}; border: 1px solid {C['card_border_strong']}; border-radius: 5px; padding: 6px 10px;
}}
QComboBox:focus, QSpinBox:focus {{ border-color: {C['accent_text']}; }}
QComboBox QAbstractItemView {{
    background: {C['card']}; border: 1px solid {C['card_border']}; selection-background-color: {C['checked_bg']};
    selection-color: {C['accent_text']}; outline: none;
}}
QSpinBox {{
    background: {C['card']}; border: 1px solid {C['card_border_strong']}; border-radius: 5px; padding: 5px 8px;
}}
QCheckBox {{ spacing: 8px; }}
QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: {C['card_soft']}; }}
QCalendarWidget QAbstractItemView {{
    background: {C['card']}; selection-background-color: {C['accent']}; selection-color: {C['on_accent']};
}}
QLabel#ViewerCounter {{ color: {C['muted']}; }}
QSplitter::handle {{ background: {C['line_soft']}; }}
QSplitter::handle:horizontal {{ width: 1px; }}

#TotalLabel {{ color: {C['faint']}; font-size: 11px; }}
#EmptyIcon {{ font-size: 54px; color: {C['line']}; }}
#EmptyHint {{ color: {C['faint']}; font-size: 13px; }}
#WordLabel, #TimeLabel {{ color: {C['muted']}; font-size: 11px; }}
#FmtSep {{ background: {C['line']}; border: none; }}
#GalleryHint, #AccountTip {{ color: {C['muted']}; font-size: 11px; }}
#NoteDesc {{
    background: {C['card']}; border: 1px solid {C['card_border_strong']}; border-radius: 12px; padding: 12px 16px;
}}
QLabel#DateBig {{ color: {C['ink']}; font-size: 40px; font-weight: 500; background: transparent; border: none; }}
QLabel#DateSub {{ color: {C['muted']}; font-size: 13px; background: transparent; border: none; }}
"""


def system_mode() -> str:
    """读 Windows「应用模式」：浅色 → light，深色 → dark。"""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as k:
            light, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
        return "light" if light else "dark"
    except Exception:
        return "light"


def resolve(mode: str) -> str:
    """light / dark / system → 实际生效的 light|dark。"""
    if mode == "system":
        return system_mode()
    return "dark" if mode == "dark" else "light"
