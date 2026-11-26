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
    "card_border": "#e2dfdd",
    "card_border_strong": "#dcccc2",
    "header": "#fae8e6",
    "ink": "#3c3c3c",
    "font_text": "Microsoft YaHei",
    "ink_soft": "#514541",
    "muted": "#736460",
    "faint": "#8f7c76",
    "on_ink": "#fffcf9",
    "line": "#f1efec",
    "line_soft": "#f1e8e1",
    "input_bg": "#f4efea",
    "hover_bg": "#f9e0de",
    "press_bg": "#f5d1cf",
    "checked_bg": "#fceeeb",
    "sel_bg": "#f3ded8",
    "hl_bg": "#f4d9d4",
    "accent": "#e48887",
    "accent_text": "#93444d",
    "accent_hover": "#d58180",
    "accent_pressed": "#cc7d7c",
    "on_accent": "#000000",
    "on_accent_hover": "#000000",
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
    "header": "#422e2a",
    "ink": "#f2eae4",
    "font_text": "Microsoft YaHei",
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
# 每项是 (显示名, 候选字体族)。Qt 里的名字和 Windows 字体名不一定一致，
# 例如「宋体」在 QFontDatabase 里就叫「宋体」而不是 SimSun。
TEXT_FONT_CHOICES = [
    ("微软雅黑", ("微软雅黑", "Microsoft YaHei", "Microsoft YaHei UI")),
    ("宋体", ("宋体", "SimSun")),
    ("悦黑", ("造字工房悦黑（非商用）常规体", "造字工房悦黑体验版细体", "MF YueHei (Noncommercial)")),
]
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
QMainWindow, #Root, #Workbench, #LibraryPage, #DocumentPage {{ background: {C['canvas']}; }}

/* 顶栏 */
#TitleBar {{
    background: {C['card']}; border-bottom: 1px solid {C['card_border']};
}}
QLabel#TitleBrand, QLabel#DialogTitle, QLabel#CollectionTitle, QLineEdit#NoteTitle, QLabel#GeneratedTitle,
QLabel#WelcomeTitle, QLabel#SetupTitle, QLabel#AboutTitle, QLabel#DateBig, QLabel#BrandPanelName {{
    font-family: {heading_font}; font-weight: {heading_weight};
}}
QLabel#TitleBrand {{ color: {C['ink']}; font-size: {int(16 * s)}px; }}
#DialogTitleBar {{
    background: {C['card_soft']}; border-bottom: 1px solid {C['line']};
    border-top-left-radius: 12px; border-top-right-radius: 12px;
}}
QLabel#DialogTitle {{ color: {C['ink']}; font-size: {int(15 * s)}px; }}
#SeriesSettingsTitleBar {{
    background: qlineargradient(x1: 0, y1:0, x2:0.18, y2:1,
        stop:0 {C['header']}, stop:0.33 {C['card']}, stop:1 {C['card']});
    border-bottom: 1px solid {C['line']};
    border-top-left-radius: 10px; border-top-right-radius: 10px;
}}
#SeriesSettingsTitleBar QLabel#DialogTitle {{ font-size: {int(18 * s)}px; }}
QLabel#WelcomeTitle {{ font-size: {int(30 * s)}px; }}
QLabel#SetupTitle {{ font-size: {int(26 * s)}px; }}
QLabel#AboutTitle {{ font-size: {int(19 * s)}px; }}
QFrame#BrandPanel {{ background: {C['card_soft']}; border-right: 1px solid {C['line']}; }}
QLabel#BrandPanelName {{ font-size: {int(18 * s)}px; }}
QLabel#DialogSymbol {{ background: {C['checked_bg']}; border-radius: 9px; }}
QLabel#WelcomeSub, QLabel#SetupHint {{ color: {C['muted']}; }}
QLabel#WelcomeDesc {{ color: {C['ink_soft']}; }}
QToolButton#WinBtn, QToolButton#CloseBtn, QToolButton#DialogClose {{ font-size: {int(12 * s)}px; }}
QToolButton#CloseBtn:hover, QToolButton#DialogClose:hover {{
    background: {C['danger_soft']}; color: {C['danger']};
}}

QLineEdit {{
    background: {C['card']}; border: 1px solid {C['card_border']}; border-radius: 5px;
    padding: 8px 11px; color: {C['ink']};
    selection-background-color: {C['sel_bg']}; selection-color: {C['accent_text']};
}}
QLineEdit:focus {{ border-color: {C['accent_text']}; }}
QLineEdit#Search {{
    background: {C['paper_alt']}; border: 1px solid {C['line']}; border-radius: 8px;
    padding: 5px 10px; font-size: {int(13 * s)}px;
}}
QLineEdit#Search:focus {{ background: {C['card']}; border-color: {C['accent_text']}; }}
QPushButton#NewNoteBtn {{
    border-top-left-radius: 8px; border-bottom-left-radius: 8px;
    border-top-right-radius: 0; border-bottom-right-radius: 0;
    padding: 6px 13px; font-weight: 600;
}}
QToolButton#NewTypeBtn {{
    border-top-right-radius: 8px; border-bottom-right-radius: 8px;
    border-top-left-radius: 0; border-bottom-left-radius: 0;
}}
QToolButton#NewTypeBtn::menu-indicator {{ image: none; width: 0; }}

/* 资料库 */
#NavigationRail {{ background: {C['paper_alt']}; border-right: 1px solid {C['line']}; }}
#RailFooter {{ border-top: 1px solid {C['line']}; }}
#RailLabel {{ color: {C['muted']}; font-size: {int(11 * s)}px; font-weight: 600; padding-left: 9px; }}
QToolButton#RailIcon {{ padding: 4px; }}
QToolButton#DataButton {{ padding: 8px 9px; text-align: left; }}
#SideSection {{ color: {C['muted']}; font-size: {int(11 * s)}px; font-weight: 600; padding: 2px 6px; }}
QListWidget#TopicList, QListWidget#NavList {{ background: transparent; border: none; }}
QListWidget#NavList::item {{
    border: 1px solid transparent; border-radius: 8px; padding: 4px 7px;
    margin: 2px 0; color: {C['ink_soft']};
}}
QListWidget#NavList::item:selected {{
    background: {C['checked_bg']}; color: {C['accent_text']}; border-left: 2px solid {C['accent']};
}}
QListWidget#NavList::item:hover:!selected {{ background: {C['card_hover']}; }}
#CollectionHeader {{ border-bottom: 1px solid {C['card_border_strong']}; }}
QLabel#CollectionTitle {{ font-size: {int(31 * s)}px; }}
QLabel#CollectionCount {{ color: {C['muted']}; font-size: {int(12 * s)}px; }}
QToolButton#CollectionControl {{
    background: {C['card']}; border: 1px solid {C['card_border']};
    border-radius: 7px; padding: 7px 10px;
}}
QToolButton#CollectionControl:hover, QToolButton#CollectionControl:focus {{ border-color: {C['accent_text']}; }}
QToolButton#CollectionControl:checked {{ background: {C['checked_bg']}; color: {C['accent_text']}; }}
QToolButton#CollectionControl::menu-indicator, QToolButton#DataButton::menu-indicator,
QToolButton#NoteAction::menu-indicator {{ image: none; width: 0; }}
QListWidget#NoteGrid {{ background: {C['canvas']}; border: none; }}
QListWidget#NoteGrid::item {{ background: transparent; border: none; }}
QLabel#LibraryEmptyIcon {{
    background: {C['checked_bg']}; border: 1px solid {C['card_border']}; border-radius: 12px;
}}
QLabel#ListEmpty {{ color: {C['muted']}; font-size: {int(16 * s)}px; }}

/* 文档 */
#NotePaper {{ background: {C['card']}; border: 1px solid {C['card_border']}; border-radius: 12px; }}
#DocumentHeading {{ border-bottom: 1px solid {C['line']}; }}
QToolButton#BackToLibrary {{ color: {C['accent_text']}; font-weight: 600; padding: 6px 7px; }}
QLabel#DocumentTopic {{
    color: {C['muted']}; border-left: 1px solid {C['card_border_strong']}; padding-left: 12px;
}}
QLabel#DocumentTypeIcon {{ background: {C['checked_bg']}; border-radius: 7px; }}
QLabel#DocumentType {{ color: {C['accent_text']}; font-size: {int(12 * s)}px; padding-left: 3px; }}
QToolButton#NoteAction {{ padding: 5px 7px; }}
QLabel#GeneratedTitle, QLineEdit#NoteTitle {{
    font-size: {int(30 * s)}px; padding: 3px 0; color: {C['ink']};
}}
QLineEdit#NoteTitle {{ background: transparent; border: none; border-bottom: 1px solid transparent; }}
QLineEdit#NoteTitle:focus {{ border-bottom-color: {C['accent_text']}; }}
QFrame#DocumentFooter {{
    background: {C['card_soft']}; border-top: 1px solid {C['line']};
    border-bottom-left-radius: 11px; border-bottom-right-radius: 11px;
}}
QLabel#SaveState {{ color: {C['muted']}; font-size: {int(11 * s)}px; }}

QFrame#FormatBar {{ background: {C['card']}; border-bottom: 1px solid {C['line']}; }}
QToolButton#FormatControl {{ padding: 2px; }}
QFrame#ModeSwitch {{ background: {C['paper_alt']}; border: 1px solid {C['line']}; border-radius: 8px; }}
QToolButton#ModeOption {{ padding: 4px 12px; color: {C['muted']}; border-radius: 5px; border: 1px solid transparent; }}
QToolButton#ModeOption:checked {{ background: {C['card']}; color: {C['accent_text']}; border-color: {C['card_border']}; }}
QToolButton#ModeOption:focus {{ border-color: {C['accent_text']}; }}
QFrame#SourceSheet {{ background: {C['paper_alt']}; }}
QFrame#PreviewSheet {{ background: {C['card']}; }}
QLabel#SheetCaption {{ color: {C['muted']}; font-size: {int(11 * s)}px; padding: 10px 24px 3px; }}
QPlainTextEdit#Editor, QTextBrowser#Preview {{
    border: none; padding: 8px 24px 15px;
    font-family: {C['font_text']}; font-size: {int(15 * s)}px; color: {C['ink']};
    selection-background-color: {C['sel_bg']}; selection-color: {C['accent_text']};
}}
QPlainTextEdit#Editor {{ background: {C['paper_alt']}; }}
QTextBrowser#Preview {{ background: {C['card']}; }}
QFrame#FindBar {{ background: {C['paper_alt']}; border-bottom: 1px solid {C['line']}; }}
QFrame#FindBar QLineEdit {{ padding: 4px 9px; }}
QFrame#FindBar QToolButton {{ padding: 3px 6px; }}
#FindCount {{ color: {C['muted']}; font-size: {int(11 * s)}px; }}
QLabel#FieldLabel {{ color: {C['muted']}; font-size: {int(12 * s)}px; }}

QFrame#FieldCard, QFrame#DateHero, QFrame#SettingsGroup {{
    background: {C['card_soft']}; border: 1px solid {C['card_border']}; border-radius: 10px;
}}
QLabel#FieldHeading {{ font-size: {int(14 * s)}px; font-weight: 600; }}
QLabel#DateBig {{ font-size: {int(33 * s)}px; color: {C['ink']}; }}
QLabel#DateSub {{ color: {C['muted']}; font-size: {int(13 * s)}px; }}
QPlainTextEdit#FieldDescription {{
    background: {C['card']}; border: 1px solid {C['line']}; border-radius: 6px;
    padding: 9px 10px; font-family: {C['font_text']}; font-size: {int(14 * s)}px;
}}
QPlainTextEdit#FieldDescription:focus {{ border-color: {C['accent_text']}; }}
QListWidget#Wall {{ background: {C['card_soft']}; border: 1px solid {C['line']}; border-radius: 8px; }}
QListWidget#Wall::item {{ border: 1px solid transparent; border-radius: 8px; margin: 2px; }}
QListWidget#Wall::item:hover {{ background: {C['card_hover']}; }}
QListWidget#Wall::item:selected {{ background: {C['checked_bg']}; border-color: {C['accent']}; }}

QDateEdit, QTimeEdit {{
    background: {C['card']}; border: 1px solid {C['line']};
    border-radius: 5px; padding: 6px 8px; min-height: 18px;
    selection-background-color: {C['sel_bg']}; selection-color: {C['accent_text']};
}}
QDateEdit:focus, QTimeEdit:focus {{ border-color: {C['accent_text']}; }}
QLabel#Notice {{
    background: {C['ink']}; color: {C['on_ink']}; border-left: 3px solid {C['accent']};
    border-radius: 8px; padding: 10px 14px;
}}

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
QPushButton:default, QPushButton#Primary, QPushButton#WelcomePrimary, QPushButton#MiniPrimary,
QPushButton#NewNoteBtn, QToolButton#NewTypeBtn {{
    background: {C['accent']}; color: {C['on_accent']}; border: none;
}}
QPushButton:default:hover, QPushButton#Primary:hover, QPushButton#WelcomePrimary:hover, QPushButton#MiniPrimary:hover,
QPushButton#NewNoteBtn:hover, QToolButton#NewTypeBtn:hover {{
    background: {C['accent_hover']}; color: {C['on_accent_hover']};
}}
QPushButton:default:pressed, QPushButton#Primary:pressed, QPushButton#WelcomePrimary:pressed, QPushButton#MiniPrimary:pressed,
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
QPushButton:disabled, QPushButton#Primary:disabled, QPushButton#Danger:disabled, QPushButton#MiniPrimary:disabled,
QToolButton:disabled {{
    color: {C['faint']}; border-color: {C['line']}; background: {C['paper_alt']};
}}
QPushButton:focus, QPushButton#Plain:focus, QPushButton#Primary:focus, QToolButton:focus {{
    border: 1px solid {C['accent_text']};
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
QTabWidget#SeriesSettingsTabs::pane {{ border: none; border-top: 1px solid {C['line']}; }}
QTabWidget#SeriesSettingsTabs QTabBar::tab {{
    background: transparent; border: 1px solid transparent; border-radius: 7px;
    min-height: 30px; min-width: 48px; margin: 0 4px 5px 0; padding: 0 12px;
    color: {C['ink']};
}}
QTabWidget#SeriesSettingsTabs QTabBar::tab:hover {{ background: {C['hover_bg']}; }}
QTabWidget#SeriesSettingsTabs QTabBar::tab:selected {{
    background: {C['checked_bg']}; color: {C['accent_text']};
}}
QTabWidget#SeriesSettingsTabs QTabBar::tab:selected:focus {{ border-color: {C['accent_text']}; }}
#SeriesSettingsScroll, #SeriesSettingsPage {{ background: {C['card']}; }}
QLabel#SeriesSettingsError {{ color: {C['accent_text']}; }}
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
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; width: 26px; border: none; }}
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


def available_text_fonts() -> list[tuple[str, str]]:
    """系统里实际装了的正文字体，返回 (显示名, Qt 字体族名)。"""
    from PyQt5.QtGui import QFontDatabase

    installed = set(QFontDatabase().families())
    out = []
    for label, candidates in TEXT_FONT_CHOICES:
        for family in candidates:
            if family in installed:
                out.append((label, family))
                break
    return out or [("微软雅黑", "微软雅黑")]


def set_text_font(family: str) -> str:
    """切换正文/预览字体，返回实际生效的字体族名。"""
    valid = {f for _, f in available_text_fonts()}
    if family not in valid:
        family = next(iter(valid), "微软雅黑")
    C["font_text"] = family
    return family
