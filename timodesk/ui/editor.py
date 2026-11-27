"""笔记编辑器：便签式正文 + 类型专属视图 + Markdown 预览。

不同笔记类型本来就有各自的呈现方式，这里按类型分开：
* note / article —— Markdown 正文 + 预览
* gallery       —— 图片墙（点击看大图）+ 说明
* account       —— 账号卡片（账号 / 密码 / 备注，可复制）
* link          —— 链接卡片 + 说明
* date          —— 日子卡片（大号日期）+ 说明
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from PyQt5.QtCore import (
    QSize,
    Qt,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt5.QtGui import (
    QColor,
    QDesktopServices,
    QImage,
    QKeySequence,
    QPixmap,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QShortcut,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..render import TASK_RE, render
from ..store import date_countdown, TYPE_LABELS, Library, Note
from .dialogs import error as dlg_error
from .theme import C, NOTE_COLORS, READING_SIZE, color_icon, reading_font
from .motion import FAST, SmoothWheel, fade_in
from .visuals import glyph
from .widgets import AutoFadeScrollBar, human_time


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")


class ImageWall(QListWidget):
    """图片墙：可以拖入图片文件、Ctrl+V 粘贴、Delete 删除选中项。"""

    filesDropped = pyqtSignal(list)
    pasteRequested = pyqtSignal()
    deleteRequested = pyqtSignal()
    orderChanged = pyqtSignal()

    def __init__(self, parent=None, **kwargs):
        super().__init__(parent, **kwargs)
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self._drag_row = -1

    def startDrag(self, actions):  # noqa: N802
        self._drag_row = self.currentRow()
        super().startDrag(actions)

    def _is_self_drag(self, e) -> bool:
        return e.source() is self and self._drag_row >= 0

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or self._is_self_drag(e):
            e.acceptProposedAction()
            return
        super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls() or self._is_self_drag(e):
            e.acceptProposedAction()
            return
        super().dragMoveEvent(e)

    def _move_row(self, e) -> None:
        """内部重排：自己 take/insert，避免 QListWidget 序列化时丢掉 UserRole。"""
        src = self._drag_row
        row = self.indexAt(e.pos()).row()
        if row < 0:
            row = self.count()
        if src < 0 or row == src or row == src + 1:
            return
        item = self.takeItem(src)
        if item is None:
            return
        if row > src:
            row -= 1
        self.insertItem(row, item)
        self.setCurrentRow(row)
        self.orderChanged.emit()

    def dropEvent(self, e):
        if self._is_self_drag(e) and not e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._move_row(e)
            return
        if e.mimeData().hasUrls():
            files = []
            for url in e.mimeData().urls():
                p = url.toLocalFile()
                if p and Path(p).suffix.lower() in IMAGE_EXTS:
                    files.append(p)
            if files:
                self.filesDropped.emit(files)
            e.acceptProposedAction()
            return
        super().dropEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.selectedItems():
            self.deleteRequested.emit()
            return
        if e.matches(QKeySequence.Paste):
            self.pasteRequested.emit()
            return
        super().keyPressEvent(e)


# ---------------------------------------------------------------- 基础控件
class MarkdownEdit(QPlainTextEdit):
    """正文编辑框：粘贴/拖入图片会发出 imageDropped。"""

    imageDropped = pyqtSignal(str)

    def __init__(self, placeholder: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Editor")
        self.setPlaceholderText(placeholder)
        self.setTabChangesFocus(True)
        self.setFont(reading_font(self.font(), READING_SIZE))

    def insertFromMimeData(self, source):  # noqa: N802
        if source.hasImage():
            img = source.imageData()
            if isinstance(img, QImage) and not img.isNull():
                import tempfile

                fd, path = tempfile.mkstemp(suffix=".png")
                os.close(fd)
                img.save(path, "PNG")
                self.imageDropped.emit(path)
                return
        super().insertFromMimeData(source)

    def dragEnterEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls():
            urls = [u for u in e.mimeData().urls() if u.isLocalFile()]
            if urls:
                e.acceptProposedAction()
                return
        super().dragEnterEvent(e)

    def dropEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls():
            for u in e.mimeData().urls():
                if u.isLocalFile():
                    self.imageDropped.emit(u.toLocalFile())
            e.acceptProposedAction()
            return
        super().dropEvent(e)


class ImageViewer(QDialog):
    """双击图片后的大图查看。"""

    def __init__(self, paths: list[str], start: int, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.idx = start
        self.setWindowTitle("查看图片")
        self.resize(1000, 720)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._pm = QPixmap()
        self.zoom = 0.0  # 0 = 适应窗口，其它值是相对原始尺寸的倍数
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.viewport().setAutoFillBackground(False)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.scroll.setWidget(self.label)
        lay.addWidget(self.scroll, 1)
        bar = QHBoxLayout()
        bar.setContentsMargins(12, 6, 12, 10)
        prev = QPushButton("‹ 上一张")
        prev.setCursor(Qt.PointingHandCursor)
        prev.clicked.connect(lambda: self._step(-1))
        bar.addWidget(prev)
        bar.addStretch(1)
        self.counter = QLabel(objectName="ViewerCounter")
        bar.addWidget(self.counter)
        bar.addStretch(1)
        nxt = QPushButton("下一张 ›")
        nxt.setCursor(Qt.PointingHandCursor)
        nxt.clicked.connect(lambda: self._step(1))
        bar.addWidget(nxt)
        lay.addLayout(bar)
        self._render()

    def _step(self, d: int):
        if not self.paths:
            return
        self.idx = (self.idx + d) % len(self.paths)
        self._render()

    def _render(self):
        if not self.paths:
            return
        self._pm = QPixmap(self.paths[self.idx])
        if self._pm.isNull():
            self.label.setPixmap(QPixmap())
            self.label.setText("图片无法读取")
        else:
            self.label.setText("")
            self._apply_zoom()
        pct = "" if self.zoom <= 0 else f" · {int(self.zoom * 100)}%"
        self.counter.setText(f"{self.idx + 1} / {len(self.paths)}{pct} · 滚轮缩放")

    def _apply_zoom(self):
        if self._pm.isNull():
            return
        if self.zoom <= 0:
            box = self.scroll.viewport().size()
            box.setWidth(max(80, box.width() - 8))
            box.setHeight(max(80, box.height() - 8))
            if self._pm.width() > box.width() or self._pm.height() > box.height():
                pm = self._pm.scaled(box, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            else:
                pm = self._pm
        else:
            w = max(20, int(self._pm.width() * self.zoom))
            h = max(20, int(self._pm.height() * self.zoom))
            pm = self._pm.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.label.setPixmap(pm)
        self.label.resize(pm.size())

    def wheelEvent(self, e):  # noqa: N802
        if self._pm.isNull():
            return
        cur = self.zoom if self.zoom > 0 else 1.0
        step = 1.25 if e.angleDelta().y() > 0 else 0.8
        self.zoom = max(0.1, min(8.0, cur * step))
        self._apply_zoom()
        pct = "" if self.zoom <= 0 else f" · {int(self.zoom * 100)}%"
        self.counter.setText(f"{self.idx + 1} / {len(self.paths)}{pct} · 滚轮缩放")

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        self.zoom = 0.0 if self.zoom > 0 else 1.0
        self._apply_zoom()
        pct = "" if self.zoom <= 0 else f" · {int(self.zoom * 100)}%"
        self.counter.setText(f"{self.idx + 1} / {len(self.paths)}{pct} · 滚轮缩放")

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        if self.zoom <= 0:
            self._apply_zoom()

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() == Qt.Key_Left:
            self._step(-1)
        elif e.key() == Qt.Key_Right:
            self._step(1)
        elif e.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)


class NoteEditor(QWidget):
    contentChanged = pyqtSignal(str, str, str)  # note_id, content, title
    metaChanged = pyqtSignal()
    requestNewNote = pyqtSignal()
    actionsRequested = pyqtSignal()

    def __init__(self, lib: Library, parent=None):
        super().__init__(parent)
        self.lib = lib
        self.note: Note | None = None
        self._loading = False
        self._dirty = False
        self._mode = "split"
        self.setObjectName("NotePaper")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._build_ui()
        self.top_bar.hide()
        self.status_bar.hide()
        self._sync_editor_icons()
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(280)
        self._preview_timer.timeout.connect(self._refresh_preview)
        # 预览是否落后于正文（不可见时跳过渲染，切回来再补）
        self._preview_stale = True

    # ================= 文档 =================
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        self.top_bar = QWidget(objectName="DocumentHeading")
        heading = QVBoxLayout(self.top_bar)
        heading.setContentsMargins(24, 15, 20, 14)
        heading.setSpacing(6)
        topline = QHBoxLayout()
        topline.setSpacing(4)
        self.type_icon = QLabel(objectName="DocumentTypeIcon")
        self.type_icon.setFixedSize(25, 25)
        self.type_icon.setAlignment(Qt.AlignCenter)
        topline.addWidget(self.type_icon)
        self.badge = QLabel(objectName="DocumentType")
        topline.addWidget(self.badge)
        topline.addStretch(1)

        self.btn_pin = QToolButton(objectName="NoteAction")
        self.btn_pin.setText("置顶")
        self.btn_pin.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_pin.setCheckable(True)
        self.btn_pin.clicked.connect(self._toggle_pin)
        topline.addWidget(self.btn_pin)
        self.btn_color = QToolButton(objectName="NoteAction")
        self.btn_color.setText("标色")
        self.btn_color.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_color.setPopupMode(QToolButton.InstantPopup)
        colors = QMenu(self)
        for name, color in NOTE_COLORS:
            act = colors.addAction(color_icon(color), name)
            act.setData(color)
        colors.triggered.connect(lambda action: self._set_color(action.data()))
        self.btn_color.setMenu(colors)
        topline.addWidget(self.btn_color)
        self.btn_remind = QToolButton(objectName="NoteAction")
        self.btn_remind.setText("提醒")
        self.btn_remind.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_remind.setToolTip("设置提醒")
        self.btn_remind.clicked.connect(self._on_remind)
        topline.addWidget(self.btn_remind)
        self.btn_more = QToolButton(objectName="NoteAction")
        self.btn_more.setText("⋯")
        self.btn_more.setToolTip("笔记操作")
        self.btn_more.setAccessibleName("笔记操作")
        self.btn_more.clicked.connect(self.actionsRequested.emit)
        topline.addWidget(self.btn_more)
        heading.addLayout(topline)

        self.title_wrap = QWidget()
        title_row = QHBoxLayout(self.title_wrap)
        title_row.setContentsMargins(0, 0, 0, 0)
        self.title_edit = QLineEdit(objectName="NoteTitle")
        self.title_edit.setPlaceholderText("输入标题")
        self.title_edit.textChanged.connect(self._on_title_changed)
        title_row.addWidget(self.title_edit)
        heading.addWidget(self.title_wrap)
        self.generated_title = QLabel(objectName="GeneratedTitle")
        self.generated_title.setTextFormat(Qt.PlainText)
        self.generated_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        heading.addWidget(self.generated_title)
        root.addWidget(self.top_bar)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.page_empty = QWidget()
        empty_layout = QVBoxLayout(self.page_empty)
        empty_layout.setAlignment(Qt.AlignCenter)
        hint = QLabel("选择或新建一条笔记", objectName="EmptyHint")
        empty_layout.addWidget(hint, alignment=Qt.AlignCenter)
        create = QPushButton("新建笔记", objectName="Primary")
        create.clicked.connect(self.requestNewNote.emit)
        empty_layout.addWidget(create, alignment=Qt.AlignCenter)
        self.stack.addWidget(self.page_empty)

        self._build_text_page()
        self._build_gallery_page()
        self._build_account_page()
        self._build_link_page()
        self._build_date_page()

        self.status_bar = QFrame(objectName="DocumentFooter")
        status = QHBoxLayout(self.status_bar)
        status.setContentsMargins(24, 0, 20, 0)
        status.setSpacing(12)
        self.status_bar.setFixedHeight(33)
        self.word_label = QLabel(objectName="WordLabel")
        self.save_state = QLabel(objectName="SaveState")
        self.time_label = QLabel(objectName="TimeLabel")
        status.addWidget(self.word_label)
        status.addWidget(self.save_state)
        status.addStretch(1)
        status.addWidget(self.time_label)
        root.addWidget(self.status_bar)

    def _build_text_page(self):
        self.page_text = QWidget()
        layout = QVBoxLayout(self.page_text)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.format_bar = QFrame(objectName="FormatBar")
        toolbar = QHBoxLayout(self.format_bar)
        toolbar.setContentsMargins(16, 6, 15, 6)
        toolbar.setSpacing(5)

        self.format_tools = QWidget()
        tools = QHBoxLayout(self.format_tools)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(2)
        actions = (
            ("B", "粗体 Ctrl+B", lambda: self._wrap("**", "**")),
            ("I", "斜体 Ctrl+I", lambda: self._wrap("*", "*")),
            ("S", "删除线", lambda: self._wrap("~~", "~~")),
            ("H1", "一级标题", lambda: self._prefix("# ")),
            ("H2", "二级标题", lambda: self._prefix("## ")),
            ("•", "列表", lambda: self._prefix("- ")),
            ("select", "待办 Ctrl+Shift+T", lambda: self._prefix("- [ ] ")),
            ("“", "引用", lambda: self._prefix("> ")),
            ("</>", "代码块", lambda: self._wrap("\n```\n", "\n```\n")),
            ("link", "链接 Ctrl+K", self._insert_link),
            ("gallery", "插入图片", self._pick_images),
        )
        self._toolbar_icons = {}
        for i, (label, tooltip, action) in enumerate(actions):
            if i in (3, 5, 8):
                divider = QFrame(objectName="FmtSep")
                divider.setFixedSize(1, 16)
                tools.addWidget(divider)
            button = QToolButton(objectName="FormatControl")
            button.setFixedSize(28 if label != "</>" else 34, 28)
            if label in ("select", "link", "gallery"):
                self._toolbar_icons[label] = button
                button.setIcon(glyph(label))
            else:
                button.setText(label)
            button.setToolTip(tooltip)
            button.setAccessibleName(tooltip)
            button.clicked.connect(action)
            tools.addWidget(button)
        toolbar.addWidget(self.format_tools)
        toolbar.addStretch(1)

        segments = QFrame(objectName="ModeSwitch")
        buttons = QHBoxLayout(segments)
        buttons.setContentsMargins(3, 3, 3, 3)
        buttons.setSpacing(2)
        self.mode_buttons = {}
        for mode, label in (("edit", "编辑"), ("split", "分屏"), ("preview", "阅读")):
            button = QToolButton(objectName="ModeOption")
            button.setText(label)
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.clicked.connect(lambda _=False, m=mode: self.set_mode(m, focus=True))
            self.mode_buttons[mode] = button
            buttons.addWidget(button)
        toolbar.addWidget(segments)
        layout.addWidget(self.format_bar)

        self._build_find_bar()
        layout.addWidget(self.find_bar)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)

        self.edit = MarkdownEdit("开始写点什么……")
        self._source_wheel = SmoothWheel(self.edit, wheel_scale=0.5, wheel_duration=175)
        self.edit.textChanged.connect(self._on_text_changed)
        self.edit.imageDropped.connect(self._insert_image_file)
        self.edit_panel = self._text_panel(self.edit, "Markdown", "SourceSheet")
        self.preview = QTextBrowser(objectName="Preview")
        self.preview.setFont(reading_font(self.preview.font(), READING_SIZE))
        self._preview_wheel = SmoothWheel(self.preview, wheel_scale=0.5, wheel_duration=175)
        self.preview.setOpenExternalLinks(True)
        self.preview.setOpenLinks(False)
        self.preview.anchorClicked.connect(self._on_anchor)
        self.preview.setFrameShape(QFrame.NoFrame)
        self.preview_panel = self._text_panel(self.preview, "预览", "PreviewSheet")
        self.splitter.addWidget(self.edit_panel)
        self.splitter.addWidget(self.preview_panel)
        layout.addWidget(self.splitter, 1)
        self.stack.addWidget(self.page_text)
        self.set_mode("split")
        self._setup_shortcuts()

    @staticmethod
    def _text_panel(widget, label, name):
        panel = QFrame(objectName=name)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        caption = QLabel(label, objectName="SheetCaption")
        layout.addWidget(caption)
        layout.addWidget(widget, 1)
        return panel

    def _build_find_bar(self):
        self.find_bar = QFrame(objectName="FindBar")
        self.find_bar.hide()
        find = QHBoxLayout(self.find_bar)
        find.setContentsMargins(16, 5, 15, 5)
        find.setSpacing(6)
        find.addStretch(1)
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("在笔记中查找…")
        self.find_edit.setFixedWidth(220)
        self.find_edit.textChanged.connect(lambda _t: self._find_step(forward=True, restart=True))
        self.find_edit.returnPressed.connect(lambda: self._find_step(forward=True))
        find.addWidget(self.find_edit)
        self.find_count = QLabel(objectName="FindCount")
        find.addWidget(self.find_count)
        for text, tip, forward in (("↑", "上一个 Shift+Enter", False), ("↓", "下一个 Enter", True)):
            button = QToolButton()
            button.setText(text)
            button.setToolTip(tip)
            button.clicked.connect(lambda _=False, f=forward: self._find_step(forward=f))
            find.addWidget(button)
        close = QToolButton()
        close.setText("✕")
        close.setToolTip("关闭 Esc")
        close.clicked.connect(self.hide_find)
        find.addWidget(close)

    def _setup_shortcuts(self):
        def add(seq, fn, ctx=Qt.WidgetShortcut):
            sc = QShortcut(QKeySequence(seq), self.edit)
            sc.setContext(ctx)
            sc.activated.connect(fn)
            return sc

        self.find_shortcut = add(QKeySequence.Find, self.show_find)
        add("Ctrl+B", lambda: self._wrap("**", "**"))
        add("Ctrl+I", lambda: self._wrap("*", "*"))
        add("Ctrl+K", self._insert_link)
        add("Ctrl+Shift+T", lambda: self._prefix("- [ ] "))
        add("Ctrl+Shift+1", lambda: self._prefix("# "))
        add("Ctrl+Shift+2", lambda: self._prefix("## "))
        esc = QShortcut(QKeySequence(Qt.Key_Escape), self.find_edit)
        esc.setContext(Qt.WidgetShortcut)
        esc.activated.connect(self.hide_find)
        up = QShortcut(QKeySequence("Shift+Return"), self.find_edit)
        up.setContext(Qt.WidgetShortcut)
        up.activated.connect(lambda: self._find_step(forward=False))

    def show_find(self):
        self.find_bar.setVisible(True)
        field = self.preview if self._mode == "preview" else self.edit
        text = field.textCursor().selectedText()
        if text and "\n" not in text:
            self.find_edit.setText(text)
        self.find_edit.setFocus()
        self.find_edit.selectAll()
        self._find_step(forward=True, restart=True)

    def hide_find(self):
        self.find_bar.setVisible(False)
        (self.preview if self._mode == "preview" else self.edit).setFocus()

    def _find_step(self, forward: bool = True, restart: bool = False):
        needle = self.find_edit.text()
        if not needle:
            self.find_count.setText("")
            return
        field = self.preview if self._mode == "preview" else self.edit
        total = field.toPlainText().count(needle)
        self.find_count.setText(f"{total} 处" if total else "无匹配")
        if not total:
            return
        flags = QTextDocument.FindFlags()
        if not forward:
            flags |= QTextDocument.FindBackward
        if restart:
            cur = field.textCursor()
            cur.movePosition(QTextCursor.Start if forward else QTextCursor.End)
            field.setTextCursor(cur)
        if not field.find(needle, flags):
            cur = field.textCursor()
            cur.movePosition(QTextCursor.Start if forward else QTextCursor.End)
            field.setTextCursor(cur)
            field.find(needle, flags)

    @staticmethod
    def _field_card(title: str):
        card = QFrame(objectName="FieldCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(10)
        layout.addWidget(QLabel(title, objectName="FieldHeading"))
        return card, layout

    def _build_gallery_page(self):
        self.page_gallery = QWidget()
        layout = QHBoxLayout(self.page_gallery)
        layout.setContentsMargins(20, 15, 20, 14)
        layout.setSpacing(14)

        gallery = QWidget()
        page = QVBoxLayout(gallery)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(9)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("图片", objectName="FieldHeading"))
        self.gallery_hint = QLabel(objectName="GalleryHint")
        bar.addWidget(self.gallery_hint)
        bar.addStretch(1)
        self.add_images_button = QPushButton("添加图片", objectName="MiniPrimary")
        self.add_images_button.setIcon(glyph("add", "on_accent"))
        self.add_images_button.clicked.connect(self._pick_images)
        bar.addWidget(self.add_images_button)
        page.addLayout(bar)

        self.wall = ImageWall()
        self.wall.filesDropped.connect(self._add_images_from_files)
        self.wall.pasteRequested.connect(self._paste_wall_image)
        self.wall.deleteRequested.connect(self._remove_selected_images)
        self.wall.orderChanged.connect(self._on_wall_reordered)
        self.wall.setViewMode(QListWidget.IconMode)
        self.wall.setIconSize(QSize(155, 155))
        self.wall.setGridSize(QSize(168, 168))
        self.wall.setResizeMode(QListWidget.Adjust)
        self.wall.setMovement(QListWidget.Static)
        self.wall.setWordWrap(True)
        self.wall.setObjectName("Wall")
        self.wall.setVerticalScrollBar(AutoFadeScrollBar(Qt.Vertical, self.wall))
        self.wall.itemDoubleClicked.connect(self._open_viewer)
        self.wall.setContextMenuPolicy(Qt.CustomContextMenu)
        self.wall.customContextMenuRequested.connect(self._wall_menu)
        page.addWidget(self.wall, 1)
        layout.addWidget(gallery, 2)

        card, fields = self._field_card("图片说明")
        self.gallery_desc = MarkdownEdit("给这组图片写点说明……")
        self.gallery_desc.setObjectName("FieldDescription")
        self.gallery_desc.textChanged.connect(self._on_desc_changed)
        fields.addWidget(self.gallery_desc, 1)
        layout.addWidget(card, 1)
        self.stack.addWidget(self.page_gallery)

    def _build_account_page(self):
        self.page_account = QWidget()
        columns = QHBoxLayout(self.page_account)
        columns.setContentsMargins(20, 15, 20, 14)
        columns.setSpacing(14)
        login_card, login = self._field_card("登录凭据")

        for label, attr, secret in (("账号", "account_edit", False), ("密码", "password_edit", True)):
            login.addWidget(QLabel(label, objectName="FieldLabel"))
            row = QHBoxLayout()
            row.setSpacing(5)
            line = QLineEdit()
            line.setPlaceholderText("未填写")
            line.textChanged.connect(self._on_field_changed)
            setattr(self, attr, line)
            row.addWidget(line, 1)
            if secret:
                line.setEchoMode(QLineEdit.Password)
                self.password_toggle = QToolButton()
                self.password_toggle.setText("显示")
                self.password_toggle.setCheckable(True)
                self.password_toggle.setToolTip("显示或隐藏密码")
                self.password_toggle.toggled.connect(self._toggle_password)
                row.addWidget(self.password_toggle)
            copy = QToolButton()
            copy.setText("复制")
            copy.setAccessibleName(f"复制{label}")
            copy.clicked.connect(lambda _=False, field=line: self._copy(field.text()))
            row.addWidget(copy)
            login.addLayout(row)
        self.account_tip = QLabel(
            "密码以明文保存在本机 library.json 中，导出时也会写入 accounts.csv。",
            objectName="AccountTip",
        )
        self.account_tip.setWordWrap(True)
        login.addWidget(self.account_tip)
        columns.addWidget(login_card, 3, Qt.AlignTop)

        remark_card, remark = self._field_card("账号备注")
        self.remark_edit = QLineEdit()
        self.remark_edit.setPlaceholderText("例如账号的用途")
        self.remark_edit.textChanged.connect(self._on_field_changed)
        remark.addWidget(self.remark_edit)
        columns.addWidget(remark_card, 2, Qt.AlignTop)
        self.stack.addWidget(self.page_account)

    def _toggle_password(self, showing: bool):
        self.password_edit.setEchoMode(QLineEdit.Normal if showing else QLineEdit.Password)
        self.password_toggle.setText("隐藏" if showing else "显示")

    def _build_link_page(self):
        self.page_link = QWidget()
        layout = QVBoxLayout(self.page_link)
        layout.setContentsMargins(20, 15, 20, 14)
        layout.setSpacing(14)
        address_card, address = self._field_card("链接地址")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://")
        self.url_edit.textChanged.connect(self._on_field_changed)
        row.addWidget(self.url_edit, 1)
        open_link = QPushButton("打开链接", objectName="MiniPrimary")
        open_link.clicked.connect(self._open_url)
        row.addWidget(open_link)
        address.addLayout(row)
        layout.addWidget(address_card)

        description_card, description = self._field_card("链接说明")
        self.link_desc = MarkdownEdit("写下这条链接的说明……")
        self.link_desc.setObjectName("FieldDescription")
        self.link_desc.textChanged.connect(self._on_desc_changed)
        description.addWidget(self.link_desc, 1)
        layout.addWidget(description_card, 1)
        self.stack.addWidget(self.page_link)

    def _build_date_page(self):
        self.page_date = QWidget()
        columns = QHBoxLayout(self.page_date)
        columns.setContentsMargins(20, 15, 20, 14)
        columns.setSpacing(14)
        display = QFrame(objectName="DateHero")
        summary = QVBoxLayout(display)
        summary.setContentsMargins(22, 22, 22, 24)
        summary.setSpacing(12)
        self.date_icon = QLabel(objectName="DateHeroIcon")
        self.date_icon.setPixmap(glyph("date", "accent_text").pixmap(27))
        summary.addWidget(self.date_icon)
        self.date_big = QLabel(objectName="DateBig")
        self.date_big.setWordWrap(True)
        summary.addWidget(self.date_big)
        self.date_sub = QLabel(objectName="DateSub")
        self.date_sub.setWordWrap(True)
        summary.addWidget(self.date_sub)
        columns.addWidget(display, 2, Qt.AlignTop)

        right = QWidget()
        sections = QVBoxLayout(right)
        sections.setContentsMargins(0, 0, 0, 0)
        sections.setSpacing(12)
        date_card, form = self._field_card("日期设置")
        recurrence = QHBoxLayout()
        self.date_type = QComboBox()
        self.date_type.addItems(["固定日期", "每年重复"])
        self.date_type.currentIndexChanged.connect(self._on_date_changed)
        recurrence.addWidget(self.date_type, 1)
        self.date_lunar = QCheckBox("农历")
        self.date_lunar.toggled.connect(self._on_date_changed)
        recurrence.addWidget(self.date_lunar)
        form.addLayout(recurrence)

        parts = QHBoxLayout()
        parts.setSpacing(7)
        for attr, label, minimum, maximum in (
            ("date_year", "年", 1900, 2200), ("date_month", "月", 1, 12), ("date_day", "日", 1, 31),
        ):
            field = QSpinBox()
            field.setRange(minimum, maximum)
            field.setButtonSymbols(QSpinBox.NoButtons)
            field.setAlignment(Qt.AlignCenter)
            field.setAccessibleName(label)
            field.setMinimumWidth(45)
            field.valueChanged.connect(self._on_date_changed)
            setattr(self, attr, field)
            parts.addWidget(field, 1)
            caption = QLabel(label, objectName="FieldLabel")
            if attr == "date_year":
                self.year_label = caption
            parts.addWidget(caption)
        form.addLayout(parts)
        sections.addWidget(date_card)

        description_card, description = self._field_card("这个日子的说明")
        self.date_desc = MarkdownEdit("写下这个日子对你的意义……")
        self.date_desc.setObjectName("FieldDescription")
        self.date_desc.textChanged.connect(self._on_desc_changed)
        description.addWidget(self.date_desc, 1)
        sections.addWidget(description_card, 1)
        columns.addWidget(right, 3)
        self.stack.addWidget(self.page_date)

    # ================= 数据 =================
    def set_library(self, lib: Library):
        self.lib = lib

    def set_note(self, note: Note | None, focus: bool = True):
        self.note = note
        self._loading = True
        if note is None:
            self.stack.setCurrentWidget(self.page_empty)
            self.top_bar.hide()
            self.status_bar.hide()
            self.word_label.setText("")
            self.time_label.setText("")
            self._loading = False
            return

        self.top_bar.show()
        self.status_bar.show()
        self.password_toggle.setChecked(False)
        self.badge.setText(TYPE_LABELS.get(note.type, "笔记"))
        self.title_edit.blockSignals(True)
        self.title_edit.setText(note.title or "")
        self.title_edit.blockSignals(False)
        titled = note.type in ("article", "account", "link", "date")
        self.title_wrap.setVisible(titled)
        self.generated_title.setVisible(not titled)

        content = self.lib.content(note.id)
        page = {
            "note": self.page_text,
            "article": self.page_text,
            "gallery": self.page_gallery,
            "account": self.page_account,
            "link": self.page_link,
            "date": self.page_date,
        }.get(note.type, self.page_text)
        previous_page = self.stack.currentWidget()
        self.stack.setCurrentWidget(page)
        if previous_page is not page:
            fade_in(page, FAST, 0.55, 1.0)  # 换到另一种笔记类型时轻轻出现一次

        if note.type in ("note", "article"):
            self.edit.blockSignals(True)
            self.edit.setPlainText(content)
            self.edit.blockSignals(False)
            self._refresh_preview()
        elif note.type == "gallery":
            self.gallery_desc.blockSignals(True)
            self.gallery_desc.setPlainText(content)
            self.gallery_desc.blockSignals(False)
            self._reload_wall()
        elif note.type == "link":
            self.url_edit.blockSignals(True)
            self.url_edit.setText(note.url or "")
            self.url_edit.blockSignals(False)
            self.link_desc.blockSignals(True)
            self.link_desc.setPlainText(content)
            self.link_desc.blockSignals(False)
        elif note.type == "date":
            d = note.date or {}
            import datetime as _dt

            yearly = d.get("type") == 1
            year = d.get("year")
            if not year or int(year) < 1900:
                year = _dt.date.today().year
            self.date_type.setCurrentIndex(1 if yearly else 0)
            self.date_year.setValue(int(year))
            self.date_year.setVisible(not yearly)
            self.year_label.setVisible(not yearly)
            self.date_month.setValue(int(d.get("month") or 1))
            self.date_day.setValue(int(d.get("dayOfMonth") or 1))
            self.date_lunar.setChecked(bool(d.get("isLunar")))
            for w in (self.date_type, self.date_year, self.date_month, self.date_day, self.date_lunar):
                w.blockSignals(False)
            self.date_desc.blockSignals(True)
            self.date_desc.setPlainText(content)
            self.date_desc.blockSignals(False)
            self._update_date_card()
        elif note.type == "account":
            for w, v in ((self.account_edit, note.account), (self.password_edit, note.password), (self.remark_edit, note.remark)):
                w.blockSignals(True)
                w.setText(v or "")
                w.blockSignals(False)

        self._apply_readonly(note.state == "trash")
        self._dirty = False
        self.refresh_note_meta()
        self._loading = False
        if focus:
            self.focus_document()

    def focus_document(self):
        if not self.note:
            return
        if self.note.type in ("note", "article") and self._mode == "preview":
            widget = self.preview
        elif self.title_wrap.isVisible() and not self.title_edit.text() and self.note.state != "trash":
            widget = self.title_edit
        else:
            widget = self._content_widget()
        widget.setFocus()

    def _sync_editor_icons(self):
        self.btn_pin.setIcon(glyph("pin"))
        self.add_images_button.setIcon(glyph("add", "on_accent"))
        self.date_icon.setPixmap(glyph("date", "accent_text").pixmap(27))
        for key, button in self._toolbar_icons.items():
            button.setIcon(glyph(key))
        for action in self.btn_color.menu().actions():
            action.setIcon(color_icon(action.data()))

    def refresh_note_meta(self):
        if not self.note:
            return
        self.type_icon.setPixmap(glyph(self.note.type, "accent_text").pixmap(15))
        self.btn_color.setIcon(color_icon(self.note.color))
        self.btn_pin.setChecked(bool(self.note.pinned_at))
        self._sync_remind()
        if self._readonly != (self.note.state == "trash"):
            self._apply_readonly(self.note.state == "trash")
        self._update_meta_labels()

    def _content_widget(self) -> QWidget:
        if not self.note:
            return self.edit
        return {
            "note": self.edit,
            "article": self.edit,
            "gallery": self.gallery_desc,
            "link": self.link_desc,
            "date": self.date_desc,
            "account": self.account_edit,
        }.get(self.note.type, self.edit)

    def _update_meta_labels(self):
        if not self.note:
            return
        n = self.note
        text = self.current_content()
        if n.type in ("note", "gallery"):
            title = n.display_title(text)
            self.generated_title.setText(title)
            self.generated_title.setToolTip(title)
        chars = len(text.strip())
        if n.type == "gallery":
            self.word_label.setText(f"{len(n.images)} 张图片 · 说明 {chars} 字")
        elif n.type == "account":
            self.word_label.setText("账号笔记")
        elif n.type == "date":
            self.word_label.setText(f"说明 {chars} 字" if chars else "")
        else:
            self.word_label.setText(f"{chars} 字")
        self.time_label.setText(f"创建 {human_time(n.created_at)}   更新 {human_time(n.updated_at)}")

    # ================= 编辑事件 =================
    def _emit(self):
        if self._loading or not self.note or self.note.state == "trash":
            return
        self._dirty = True
        self.save_state.setText("编辑中…")
        self.contentChanged.emit(self.note.id, self.current_content(), self.note.title or "")

    def _on_text_changed(self):
        self._emit()
        if self.note and self.note.type == "note":
            self._update_meta_labels()
        if self.preview.isVisible():
            self._preview_timer.start()
        else:
            self._preview_stale = True

    def _on_desc_changed(self):
        self._emit()
        self._update_meta_labels()

    def _on_title_changed(self, text: str):
        if self._loading or not self.note:
            return
        self.note.title = text
        self._emit()

    def _on_field_changed(self):
        if self._loading or not self.note:
            return
        n = self.note
        if n.type == "account":
            n.account = self.account_edit.text()
            n.password = self.password_edit.text()
            n.remark = self.remark_edit.text()
        elif n.type == "link":
            n.url = self.url_edit.text() or None
        self._emit()

    def _on_date_changed(self):
        if self._loading or not self.note:
            return
        yearly = self.date_type.currentIndex() == 1
        self.date_year.setVisible(not yearly)
        if hasattr(self, "year_label"):
            self.year_label.setVisible(not yearly)
        self.note.date = {
            "type": self.date_type.currentIndex(),
            "year": self.date_year.value(),
            "month": self.date_month.value(),
            "dayOfMonth": self.date_day.value(),
            "isLunar": self.date_lunar.isChecked(),
            "dayOfWeek": -1,
        }
        self._update_date_card()
        self._emit()

    def _update_date_card(self):
        y, m, d = self.date_year.value(), self.date_month.value(), self.date_day.value()
        self.date_big.setText(f"{m} 月 {d} 日")
        parts = []
        if self.date_type.currentIndex() == 0:
            parts.append(f"{y} 年")
        else:
            parts.append("每年重复")
        parts.append("农历" if self.date_lunar.isChecked() else "公历")
        delta = date_countdown(
            {
                "type": self.date_type.currentIndex(),
                "year": y,
                "month": m,
                "dayOfMonth": d,
            }
        )
        if delta:
            parts.append(delta)
        self.date_sub.setText(" · ".join(parts))

    # ================= 正文 =================
    def current_content(self) -> str:
        if not self.note:
            return ""
        return {
            "note": self.edit.toPlainText,
            "article": self.edit.toPlainText,
            "gallery": self.gallery_desc.toPlainText,
            "link": self.link_desc.toPlainText,
            "date": self.date_desc.toPlainText,
            "account": lambda: "",
        }.get(self.note.type, self.edit.toPlainText)()

    def flush_note(self) -> Note | None:
        if not self.note:
            return None
        n = self.note
        if self.title_wrap.isVisible():
            n.title = self.title_edit.text().strip() or None
        if n.type == "link":
            n.url = self.url_edit.text() or None
            n.description = self.link_desc.toPlainText().strip() or None
        elif n.type == "gallery":
            n.description = self.gallery_desc.toPlainText().strip() or None
        elif n.type == "date":
            n.description = self.date_desc.toPlainText().strip() or None
        elif n.type == "account":
            n.account = self.account_edit.text()
            n.password = self.password_edit.text()
            n.remark = self.remark_edit.text()
        elif n.type == "date":
            n.date = {
                "type": self.date_type.currentIndex(),
                "year": self.date_year.value(),
                "month": self.date_month.value(),
                "dayOfMonth": self.date_day.value(),
                "isLunar": self.date_lunar.isChecked(),
                "dayOfWeek": -1,
            }
        return n

    def _on_wall_reordered(self, *args):
        """拖动图片后把新顺序写回笔记（只在图库类型下有效）。"""
        if not self.note or self.note.type != "gallery":
            return
        order = []
        for i in range(self.wall.count()):
            item = self.wall.item(i)
            rel = self._rel_of(item) if item else None
            if rel:
                order.append(rel)
        if len(order) == len(self.note.images) and order != self.note.images:
            self.note.images = order
            self.lib.save()
            self.metaChanged.emit()

    # ================= 图片墙 =================
    def _reload_wall(self):
        self.wall.clear()
        if not self.note:
            return
        for rel in self.note.images:
            p = self.lib.attachment_abspath(rel)
            item = QListWidgetItem()
            if p and p.exists():
                pm = QPixmap(str(p))
                if not pm.isNull():
                    item.setIcon(_thumb(pm, 200))
            else:
                item.setText("图片缺失")
            item.setData(Qt.UserRole, str(p))
            item.setToolTip(Path(rel).name)
            self.wall.addItem(item)
        n = len(self.note.images)
        self.gallery_hint.setText(f"{n} 张" if n else "可拖入或粘贴")
        self.wall.setAccessibleName("图片列表，双击查看大图")

    def _open_viewer(self, item: QListWidgetItem):
        paths = [self.wall.item(i).data(Qt.UserRole) for i in range(self.wall.count())]
        paths = [p for p in paths if p and Path(p).exists()]
        if item.data(Qt.UserRole) not in paths:
            return
        idx = max(0, paths.index(item.data(Qt.UserRole)))
        ImageViewer(paths, idx, self).exec_()

    def _wall_menu(self, pos):
        item = self.wall.itemAt(pos)
        if not item or not self.note:
            return
        menu = QMenu(self)
        a_view = menu.addAction("查看大图")
        a_save = menu.addAction("图片另存为…")
        a_copy = menu.addAction("复制图片")
        menu.addSeparator()
        a_remove = menu.addAction("移除这张图片")
        act = menu.exec_(self.wall.mapToGlobal(pos))
        if act is a_view:
            self._open_viewer(item)
        elif act is a_save:
            self._save_image(item)
        elif act is a_copy:
            self._copy_image(item)
        elif act is a_remove:
            rel = self._rel_of(item)
            if rel and rel in self.note.images:
                self.note.images.remove(rel)
                self.lib.save()
            self._reload_wall()
            self.metaChanged.emit()

    def _save_image(self, item: QListWidgetItem):
        src = item.data(Qt.UserRole)
        if not src:
            return
        src = Path(src)
        out, _ = QFileDialog.getSaveFileName(self, "图片另存为", src.name, "图片 (*.png *.jpg *.jpeg *.webp *.gif *.bmp)")
        if not out:
            return
        try:
            shutil.copy2(src, out)
        except OSError as e:
            dlg_error(self, "保存失败", f"写入图片时出错：\n{e}")

    def _copy_image(self, item: QListWidgetItem):
        src = item.data(Qt.UserRole)
        if not src:
            return
        img = QImage(src)
        if img.isNull():
            return
        QApplication.clipboard().setImage(img)

    def _rel_of(self, item: QListWidgetItem) -> str | None:
        p = item.data(Qt.UserRole)
        if not p:
            return None
        for rel in self.note.images if self.note else []:
            if str(self.lib.attachment_abspath(rel)) == p:
                return rel
        return None

    def _pick_images(self):
        if not self.note:
            return
        files, _ = QFileDialog.getOpenFileNames(self, "选择图片", "", "图片 (*.png *.jpg *.jpeg *.gif *.webp *.bmp)")
        if not files:
            return
        for f in files:
            self._add_image(f, reload=False)
        self._reload_wall()
        self.metaChanged.emit()

    def _add_images_from_files(self, files: list):
        """拖进图片墙的文件。"""
        if not self.note or self.note.type != "gallery" or not files:
            return
        for f in files:
            self._add_image(f, reload=False)
        self._reload_wall()
        self.metaChanged.emit()

    def _paste_wall_image(self):
        """Ctrl+V：把剪贴板里的图片存进图库。"""
        if not self.note or self.note.type != "gallery":
            return
        img = QApplication.clipboard().image()
        if img.isNull():
            self._flash("剪贴板里没有图片")
            return
        tmp = self.lib.root / "attachments" / f"_paste_{os.getpid()}.png"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        if not img.save(str(tmp), "PNG"):
            self._flash("图片保存失败")
            return
        try:
            self._add_image(str(tmp), reload=False)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        self._reload_wall()
        self.metaChanged.emit()
        self._flash("已粘贴图片")

    def _remove_selected_images(self):
        """Delete：从图库里删掉选中的图片（文件留在附件目录）。"""
        if not self.note or self.note.type != "gallery":
            return
        rels = [self._rel_of(it) for it in self.wall.selectedItems()]
        rels = [r for r in rels if r]
        if not rels:
            return
        for r in rels:
            if r in self.note.images:
                self.note.images.remove(r)
        self.lib.save()
        self._reload_wall()
        self.metaChanged.emit()

    def _flash(self, text: str, ms: int = 1200):
        self.save_state.setText(text)
        QTimer.singleShot(ms, lambda: self.save_state.setText(""))

    def _insert_image_file(self, path: str):
        if not self.note:
            return
        self._add_image(path)

    def _add_image(self, path: str, reload: bool = True):
        rel = self.lib.import_attachment(path)
        if self.note.type == "gallery":
            self.note.images.append(rel)
            self.lib.save()
            if reload:
                self._reload_wall()
                self.metaChanged.emit()
        else:
            cursor = self.edit.textCursor()
            cursor.insertText(f"\n![图片]({rel})\n")
            self.edit.setFocus()

    # ================= 编辑器操作 =================
    def _wrap(self, left: str, right: str):
        e = self.edit
        c = e.textCursor()
        if c.hasSelection():
            c.insertText(f"{left}{c.selectedText()}{right}")
        else:
            c.insertText(f"{left}{right}")
            c.movePosition(c.Left)
            e.setTextCursor(c)
        e.setFocus()

    def _prefix(self, prefix: str):
        e = self.edit
        c = e.textCursor()
        c.beginEditBlock()
        line_start = e.toPlainText().rfind("\n", 0, c.selectionStart()) + 1
        c.setPosition(line_start)
        c.insertText(prefix)
        c.endEditBlock()
        e.setFocus()

    def _insert_link(self):
        from PyQt5.QtWidgets import QInputDialog

        url, ok = QInputDialog.getText(self, "插入链接", "链接地址：")
        if ok and url.strip():
            c = self.edit.textCursor()
            c.insertText(f"[{c.selectedText() or url.strip()}]({url.strip()})")
            self.edit.setFocus()

    def _open_url(self):

        u = self.url_edit.text().strip()
        if not u:
            return
        if not u.lower().startswith(("http://", "https://")):
            u = "https://" + u
        QDesktopServices.openUrl(QUrl(u))

    def _copy(self, text: str):
        from PyQt5.QtWidgets import QApplication

        QApplication.clipboard().setText(text or "")
        self.save_state.setText("已复制")
        QTimer.singleShot(1200, lambda: self.save_state.setText(""))

    # ================= 笔记操作 =================
    def _toggle_pin(self):
        if not self.note:
            return
        self.lib.toggle_pin(self.note.id)
        self.note.pinned_at = self.lib.notes[self.note.id].pinned_at
        self.metaChanged.emit()

    def _set_color(self, color):
        if not self.note:
            return
        self.note.color = color
        self.lib.toggle_color(self.note.id, color)
        self.metaChanged.emit()

    def _sync_remind(self):
        n = self.note
        if n and n.remind_at:
            self.btn_remind.setText("⏰")
            self.btn_remind.setIcon(glyph("clock", "accent_text"))
            self.btn_remind.setToolTip("提醒：" + human_time(n.remind_at) + "（点击修改）")
        else:
            self.btn_remind.setText("提醒")
            self.btn_remind.setIcon(glyph("clock"))
            self.btn_remind.setToolTip("设置提醒")

    def _on_remind(self):
        if not self.note:
            return
        from .dialogs import ReminderDialog
        from ..store import from_iso, to_iso

        cur = from_iso(self.note.remind_at)
        d = ReminderDialog(self, cur, has_reminder=bool(self.note.remind_at))
        if not d.exec_():
            return
        if d.cleared:
            self.lib.set_reminder(self.note.id, None)
        elif d.result_when:
            self.lib.set_reminder(self.note.id, to_iso(d.result_when))
        self._sync_remind()
        self.metaChanged.emit()

    # ================= 预览 =================
    def _on_anchor(self, url: QUrl):
        """预览里的链接：待办方框就地勾选，其余链接交给系统浏览器。"""
        if url.scheme() == "todo":
            try:
                self._toggle_todo(int(url.path()))
            except (TypeError, ValueError):
                pass
            return
        if url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)

    def _toggle_todo(self, lineno: int):
        """把正文第 lineno 行的 [ ] / [x] 翻过来（保留撤销栈）。"""
        if self.note and self.note.state == "trash":
            return
        text = self.edit.toPlainText()
        lines = text.split("\n")
        if not (0 <= lineno < len(lines)):
            return
        m = TASK_RE.match(lines[lineno])
        if not m:
            return
        checked = m.group(2).lower() == "x"
        offset = sum(len(x) + 1 for x in lines[:lineno]) + m.start(2)
        cur = QTextCursor(self.edit.document())
        cur.setPosition(offset)
        cur.setPosition(offset + 1, QTextCursor.KeepAnchor)
        cur.insertText(" " if checked else "x")
        self._emit()
        self._refresh_preview()

    def _refresh_preview(self):
        if not self.note:
            return
        self.preview.setHtml(render(self.edit.toPlainText(), self.lib.root, "开始写点什么……"))
        self._preview_stale = False
        self._paint_highlight()
        self._update_meta_labels()

    def _paint_highlight(self):
        """用当前主题的底色标出预览里的搜索命中。"""
        term = getattr(self, "_highlight", "")
        if not term:
            self.preview.setExtraSelections([])
            return
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(C["hl_bg"]))
        fmt.setForeground(QColor(C["accent_text"]))
        doc = self.preview.document()
        sels = []
        for word in [w for w in term.split() if w]:
            cur = QTextCursor(doc)
            while True:
                cur = doc.find(word, cur)
                if cur.isNull():
                    break
                sel = QTextEdit.ExtraSelection()
                sel.cursor = cur
                sel.format = fmt
                sels.append(sel)
        self.preview.setExtraSelections(sels)

    def set_highlight(self, term: str):
        """搜索时把命中词在预览里标出来。"""
        term = (term or "").strip()
        if term == getattr(self, "_highlight", ""):
            return
        self._highlight = term
        if self.note and self.note.type in ("note", "article") and self._preview_stale:
            # 预览已落后（正文还没来得及重渲染），顺手补一次，避免高亮落在旧文本上
            self._refresh_preview()
        else:
            self._paint_highlight()

    def set_mode(self, mode: str, *, focus: bool = False):
        previous = self._mode
        self.mode_buttons[mode].setChecked(True)
        self._mode = mode
        self.edit_panel.setVisible(mode != "preview")
        self.preview_panel.setVisible(mode != "edit")
        self.format_tools.setVisible(mode != "preview")
        if mode == "preview":
            self.find_bar.hide()
            self._refresh_preview()
        elif mode == "split" and previous != mode:
            self.splitter.setSizes([1, 1])
            if self._preview_stale:
                self._refresh_preview()
        if focus:
            if previous == "edit" and mode != "edit":
                fade_in(self.preview_panel, FAST, 0.72)
            elif previous == "preview" and mode != "preview":
                fade_in(self.edit_panel, FAST, 0.72)
            self.focus_document()

    def refresh_theme(self):
        """切换浅色/深色后刷新依赖配色表 C 的部件与预览。"""
        self._sync_editor_icons()
        self.refresh_note_meta()
        try:
            self._refresh_preview()
        except Exception:
            pass
        if hasattr(self, "wall"):
            self.wall.viewport().update()
        self.update()

    def _apply_readonly(self, ro: bool):
        """回收站里的笔记只读——想改先恢复出来。"""
        self._readonly = ro
        for w in self.findChildren(QWidget):
            if isinstance(w, (QLineEdit, QPlainTextEdit)) and w is not self.find_edit:
                w.setReadOnly(ro)
        self.format_tools.setEnabled(not ro)
        self.add_images_button.setEnabled(not ro)
        self.wall.setAcceptDrops(not ro)
        self.save_state.setText("回收站 · 只读" if ro else "")

    def mark_saved(self):
        self._dirty = False
        self.save_state.setText("已保存")


def _thumb(pm: QPixmap, size: int):
    from PyQt5.QtGui import QIcon, QPainter, QPainterPath

    scaled = pm.scaled(size - 2, size - 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(1, 1, size - 2, size - 2, 9, 9)
    p.setClipPath(path)
    p.drawPixmap((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    p.end()
    return QIcon(out)
