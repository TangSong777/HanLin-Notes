"""主窗口，组织笔记库、文档页和全局操作。"""
from __future__ import annotations

from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

from PyQt5.QtCore import (
    QEvent,
    QPoint,
    QSize,
    Qt,
    QTimer,
)
from PyQt5.QtGui import QCursor, QKeySequence
from PyQt5.QtWidgets import (
    QPlainTextEdit,
    QTextBrowser,
    QActionGroup,
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPushButton,
    QShortcut,
    QSizePolicy,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import re
import shutil

from ..exporter import export_zip, note_markdown
from ..settings import Settings
from ..importer import import_export
from ..store import NOTE_TYPES, TYPE_LABELS, Library, Note
from ..undo import Command, UndoStack
from .dialogs import DuplicatesDialog, ProgressDialog, confirm, error, info
from .editor import NoteEditor
from .theme import NOTE_COLORS, apply_theme, color_icon, resolve as resolve_theme
from .title_bar import (
    FrameEdge,
    HTBOTTOM,
    HTBOTTOMLEFT,
    HTBOTTOMRIGHT,
    HTLEFT,
    HTRIGHT,
    HTTOP,
    HTTOPLEFT,
    HTTOPRIGHT,
    RESIZE_BORDER,
    WM_NCHITTEST,
    TitleBar,
    rounded_mask,
)
from .widgets import AutoFadeScrollBar, NoteCardDelegate, NoteList, TopicDelegate, TopicList
from .visuals import Notice, WorkspaceStack, glyph


class ImportCanceled(Exception):
    """用户在导入进度条上点了取消。"""


class MainWindow(QMainWindow):
    def __init__(self, lib: Library, parent=None):
        super().__init__(parent)
        self.lib = lib
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.view = ("all", None)  # (kind, topic_id)
        self._pending_save = None
        self._search_buf = ""
        self.setWindowTitle("寒淋笔记")
        self.resize(1280, 830)
        self.setMinimumSize(980, 620)
        self.tray = None
        self.setAcceptDrops(True)
        self.settings = Settings(self.lib.root)
        self.sort_mode = self.settings.get("sort_mode", "updated")
        self.undo_stack = UndoStack(self.lib)
        self._undo_guard = False
        self._quitting = False
        self._tray_hinted = False
        self._build_ui()
        self._sync_sort_menu()
        self._restore_geometry()
        self._build_shortcuts()
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(700)
        self._save_timer.timeout.connect(self._flush_save)
        self._remind_timer = QTimer(self)
        self._remind_timer.setInterval(30_000)
        self._remind_timer.timeout.connect(self._check_reminders)
        self._remind_timer.start()
        QTimer.singleShot(1500, self._check_reminders)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(220)
        self._search_timer.timeout.connect(self._apply_search)
        self.refresh_topics()
        self._restore_view()

    # ---------------- 工作台 ----------------
    def _build_ui(self):
        central = QWidget(objectName="Root")
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)
        self.title_bar = TitleBar(self, self._build_commands())
        outer.addWidget(self.title_bar)

        workbench = QWidget(objectName="Workbench")
        workbench.setAttribute(Qt.WA_StyledBackground, True)
        layout = QHBoxLayout(workbench)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_navigation())
        self.workspace = WorkspaceStack()
        self.library_page = self._build_library_page()
        self.document_page = self._build_document_page()
        self.workspace.addWidget(self.library_page)
        self.workspace.addWidget(self.document_page)
        layout.addWidget(self.workspace, 1)
        outer.addWidget(workbench, 1)

        self.statusBar().hide()
        self._edge = FrameEdge(central)
        self._edge.setGeometry(central.rect())
        self._edge.raise_()
        self.notice = Notice(central)
        self._sync_chrome_icons()

    def _build_commands(self):
        commands = QWidget(objectName="Commands")
        row = QHBoxLayout(commands)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.search = QLineEdit(objectName="Search")
        self.search.setAccessibleName("全库搜索")
        self.search.setPlaceholderText("搜索笔记    Ctrl+F")
        self.search.setFixedHeight(36)
        self.search.setMinimumWidth(240)
        self.search.setMaximumWidth(488)
        self._search_action = self.search.addAction(glyph("search"), QLineEdit.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_search_changed)
        self.search.returnPressed.connect(self._open_search_result)
        esc_search = QShortcut(QKeySequence("Escape"), self.search)
        esc_search.setContext(Qt.WidgetShortcut)
        esc_search.activated.connect(self._reset_search)
        row.addWidget(self.search, 1)
        row.addStretch(1)

        self.btn_new_note = QPushButton("新建笔记", objectName="NewNoteBtn")
        self.btn_new_note.setFixedHeight(36)
        self.btn_new_note.setToolTip("新建便签（Ctrl+N）")
        self.btn_new_note.clicked.connect(lambda: self.new_note("note"))
        row.addWidget(self.btn_new_note)
        self.btn_new_type = QToolButton(objectName="NewTypeBtn")
        self.btn_new_type.setFixedSize(32, 36)
        self.btn_new_type.setToolTip("选择笔记类型")
        self.btn_new_type.setAccessibleName("选择笔记类型")
        self.btn_new_type.setPopupMode(QToolButton.InstantPopup)
        self.type_menu = QMenu(self)
        for kind in NOTE_TYPES:
            action = self.type_menu.addAction(TYPE_LABELS[kind], lambda _=False, k=kind: self.new_note(k))
            action.setData(kind)
        self.btn_new_type.setMenu(self.type_menu)
        row.addWidget(self.btn_new_type)
        return commands

    def _build_navigation(self):
        rail = QFrame(objectName="NavigationRail")
        rail.setFixedWidth(214)
        rail.setAttribute(Qt.WA_StyledBackground, True)
        column = QVBoxLayout(rail)
        column.setContentsMargins(14, 24, 14, 14)
        column.setSpacing(8)
        label = QLabel("资料库", objectName="RailLabel")
        column.addWidget(label)
        self.nav = QListWidget(objectName="NavList")
        self.nav.setFrameShape(QListWidget.NoFrame)
        self.nav.setFixedHeight(114)
        self.nav.setIconSize(QSize(16, 16))
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for kind, caption in (("all", "全部笔记"), ("archived", "归档"), ("trash", "回收站")):
            item = QListWidgetItem(caption)
            item.setData(Qt.UserRole, kind)
            item.setSizeHint(QSize(0, 38))
            self.nav.addItem(item)
        self.nav.currentRowChanged.connect(self._on_nav_changed)
        self.nav.itemClicked.connect(lambda _: self._show_library())
        column.addWidget(self.nav)
        column.addSpacing(17)

        topics_header = QHBoxLayout()
        topics_header.setSpacing(2)
        topics_header.addWidget(QLabel("主题", objectName="RailLabel"))
        topics_header.addStretch()
        self.btn_topic_sort = QToolButton(objectName="RailIcon")
        self.btn_topic_sort.setToolTip("主题排序")
        self.btn_topic_sort.setAccessibleName("主题排序")
        self.btn_topic_sort.clicked.connect(lambda: self._show_topic_sort(self.btn_topic_sort))
        topics_header.addWidget(self.btn_topic_sort)
        self.btn_add_topic = QToolButton(objectName="RailIcon")
        self.btn_add_topic.setToolTip("新建主题（Ctrl+Shift+N）")
        self.btn_add_topic.setAccessibleName("新建主题")
        self.btn_add_topic.clicked.connect(self.new_topic)
        topics_header.addWidget(self.btn_add_topic)
        column.addLayout(topics_header)

        self.topic_list = TopicList(objectName="TopicList")
        self.topic_list.setVerticalScrollBar(AutoFadeScrollBar(Qt.Vertical, self.topic_list))
        self.topic_list.setItemDelegate(TopicDelegate(self.lib, self))
        self.topic_list.setFrameShape(QListWidget.NoFrame)
        self.topic_list.setDefaultDropAction(Qt.MoveAction)
        self.topic_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.topic_list.customContextMenuRequested.connect(self._topic_menu)
        self.topic_list.currentRowChanged.connect(self._on_topic_changed)
        self.topic_list.itemClicked.connect(lambda _: self._show_library())
        self.topic_list.notesDropped.connect(self._on_notes_dropped)
        self.topic_list.topicsReordered.connect(self._on_topics_reordered)
        column.addWidget(self.topic_list, 1)

        footer = QFrame(objectName="RailFooter")
        foot = QVBoxLayout(footer)
        foot.setContentsMargins(0, 8, 0, 0)
        foot.setSpacing(3)
        self.btn_data = QToolButton(objectName="DataButton")
        self.btn_data.setText("数据与设置")
        self.btn_data.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_data.setPopupMode(QToolButton.InstantPopup)
        self.btn_data.setMenu(self._build_data_menu())
        foot.addWidget(self.btn_data)
        self.total_label = QLabel(objectName="TotalLabel")
        foot.addWidget(self.total_label)
        column.addWidget(footer)
        return rail

    def _build_data_menu(self):
        menu = QMenu(self)
        self._undo_menu = menu
        self.act_undo = menu.addAction("撤销\tCtrl+Z")
        self.act_undo.triggered.connect(self.undo)
        self.act_redo = menu.addAction("重做\tCtrl+Shift+Z")
        self.act_redo.triggered.connect(self.redo)
        menu.aboutToShow.connect(self._sync_undo_actions)
        menu.addSeparator()
        incoming = menu.addMenu("导入")
        incoming.addAction("Timo 导出包（ZIP）…", self.import_zip)
        incoming.addAction("Timo 导出文件夹…", self.import_folder)
        outgoing = menu.addMenu("导出")
        outgoing.addAction("Timo ZIP…", self.export_zip)
        outgoing.addAction("Markdown 文件夹…", self.export_md_tree)
        menu.addAction("打开数据文件夹", self.open_data_dir)
        menu.addAction("备份与恢复…", self.backup_dialog)
        menu.addSeparator()
        maintenance = menu.addMenu("整理数据")
        maintenance.addAction("已删除的主题…", self.trash_topics_dialog)
        maintenance.addAction("清空回收站…", self.empty_trash)
        maintenance.addAction("清理未使用的图片…", self.clean_attachments)
        maintenance.addAction("检查数据完整性…", self.audit_data)
        maintenance.addAction("查找重复笔记…", self.dedupe_dialog)
        appearance = menu.addMenu("外观")
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        current = self.settings.get("theme_mode", "light")
        for key, caption in (("light", "浅色"), ("dark", "深色"), ("system", "跟随系统")):
            action = appearance.addAction(caption)
            action.setCheckable(True)
            action.setChecked(current == key)
            action.setData(key)
            action.triggered.connect(lambda _=False, k=key: self.set_theme(k))
            self._theme_group.addAction(action)
        menu.addAction("设置…", self.open_settings)
        menu.addSeparator()
        menu.addAction("关于寒淋笔记", lambda: self.open_settings(3))
        return menu

    def _sync_undo_actions(self):
        """菜单弹出前更新「撤销 / 重做」的文案与可用状态。"""
        label = self.undo_stack.undo_label()
        self.act_undo.setText(f"撤销 {label}\tCtrl+Z" if label else "撤销\tCtrl+Z")
        self.act_undo.setEnabled(bool(label))
        rlabel = self.undo_stack.redo_label()
        self.act_redo.setText(f"重做 {rlabel}\tCtrl+Shift+Z" if rlabel else "重做\tCtrl+Shift+Z")
        self.act_redo.setEnabled(bool(rlabel))

    def _build_library_page(self):
        page = QWidget(objectName="LibraryPage")
        page.setAttribute(Qt.WA_StyledBackground, True)
        panel = QVBoxLayout(page)
        panel.setContentsMargins(30, 31, 25, 14)
        panel.setSpacing(16)
        header = QFrame(objectName="CollectionHeader")
        bar = QHBoxLayout(header)
        bar.setContentsMargins(5, 0, 4, 16)
        bar.setSpacing(8)
        title = QWidget()
        title_area = QVBoxLayout(title)
        title_area.setContentsMargins(0, 0, 0, 0)
        title_area.setSpacing(3)
        self.header_label = QLabel("全部笔记", objectName="CollectionTitle")
        self.header_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.header_label.setTextFormat(Qt.PlainText)
        title_area.addWidget(self.header_label)
        self.count_label = QLabel(objectName="CollectionCount")
        title_area.addWidget(self.count_label)
        bar.addWidget(title, 1)

        self.batch_button = QToolButton(objectName="CollectionControl")
        self.batch_button.setToolTip("批量移动、归档、置顶或删除")
        self.batch_button.clicked.connect(self._show_batch_actions)
        self.batch_button.hide()
        bar.addWidget(self.batch_button, alignment=Qt.AlignBottom)
        self.selection_button = QToolButton(objectName="CollectionControl")
        self.selection_button.setText("选择")
        self.selection_button.setCheckable(True)
        self.selection_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.selection_button.setToolTip("多选笔记后批量整理，也可用 Ctrl 或 Shift 点选")
        self.selection_button.toggled.connect(self._toggle_selection_mode)
        bar.addWidget(self.selection_button, alignment=Qt.AlignBottom)

        self.btn_sort = QToolButton(objectName="CollectionControl")
        self.btn_sort.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_sort.setPopupMode(QToolButton.InstantPopup)
        self.sort_menu = QMenu(self)
        for key, caption in (("updated", "最近更新"), ("created", "最近创建"), ("title", "标题")):
            action = self.sort_menu.addAction(caption)
            action.setCheckable(True)
            action.setData(key)
            action.triggered.connect(lambda _=False, k=key: self.set_sort(k))
        self.btn_sort.setMenu(self.sort_menu)
        bar.addWidget(self.btn_sort, alignment=Qt.AlignBottom)
        panel.addWidget(header)

        self.note_list = NoteList(objectName="NoteGrid")
        self.note_list.setVerticalScrollBar(AutoFadeScrollBar(Qt.Vertical, self.note_list))
        self.note_list.setItemDelegate(NoteCardDelegate(self.lib, self.note_list))
        self.note_list.setFrameShape(QListWidget.NoFrame)
        self.note_list.setMouseTracking(True)
        self.note_list.setViewMode(QListView.IconMode)
        self.note_list.setMovement(QListView.Static)
        self.note_list.setResizeMode(QListView.Adjust)
        self.note_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.note_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.note_list.setUniformItemSizes(True)
        self.note_list.setLayoutMode(QListView.Batched)
        self.note_list.setBatchSize(80)
        self.note_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.note_list.setDragEnabled(True)
        self.note_list.setDragDropMode(QListWidget.DragOnly)
        self.note_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.note_list.customContextMenuRequested.connect(self._note_menu)
        self.note_list.currentRowChanged.connect(self._on_note_changed)
        self.note_list.itemSelectionChanged.connect(self._sync_batch_actions)
        self.note_list.openRequested.connect(self._open_grid_note)
        self.note_list.deleteRequested.connect(self._on_notes_delete_key)
        panel.addWidget(self.note_list, 1)

        self.library_empty = QWidget()
        empty_layout = QVBoxLayout(self.library_empty)
        empty_layout.setAlignment(Qt.AlignCenter)
        empty_layout.setSpacing(14)
        self.empty_icon = QLabel(objectName="LibraryEmptyIcon")
        self.empty_icon.setFixedSize(58, 58)
        self.empty_icon.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(self.empty_icon, alignment=Qt.AlignCenter)
        self.list_empty = QLabel(objectName="ListEmpty")
        self.list_empty.setAlignment(Qt.AlignCenter)
        self.list_empty.setWordWrap(True)
        self.list_empty.setTextFormat(Qt.PlainText)
        empty_layout.addWidget(self.list_empty)
        self.empty_create = QPushButton("新建笔记", objectName="Primary")
        self.empty_create.clicked.connect(lambda: self.new_note())
        empty_layout.addWidget(self.empty_create, alignment=Qt.AlignCenter)
        self.library_empty.hide()
        panel.addWidget(self.library_empty, 1)
        return page

    def _build_document_page(self):
        page = QWidget(objectName="DocumentPage")
        page.setAttribute(Qt.WA_StyledBackground, True)
        column = QVBoxLayout(page)
        column.setContentsMargins(30, 15, 29, 18)
        column.setSpacing(11)
        crumbs = QHBoxLayout()
        crumbs.setContentsMargins(0, 0, 0, 0)
        crumbs.setSpacing(12)
        self.back_button = QToolButton(objectName="BackToLibrary")
        self.back_button.setText("笔记库")
        self.back_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.back_button.setToolTip("返回笔记库（Alt+←）")
        self.back_button.clicked.connect(lambda: self._show_library(focus=True))
        crumbs.addWidget(self.back_button)
        self.document_topic = QLabel(objectName="DocumentTopic")
        self.document_topic.setTextFormat(Qt.PlainText)
        self.document_topic.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        crumbs.addWidget(self.document_topic, 1)
        column.addLayout(crumbs)

        self.editor = NoteEditor(self.lib)
        self.editor.find_shortcut.setEnabled(False)
        self.editor.contentChanged.connect(self._on_content_changed)
        self.editor.metaChanged.connect(self._on_meta_changed)
        self.editor.requestNewNote.connect(self.new_note)
        self.editor.actionsRequested.connect(self._show_document_actions)
        column.addWidget(self.editor, 1)
        return page

    def _sync_chrome_icons(self):
        self._search_action.setIcon(glyph("search"))
        self.btn_new_note.setIcon(glyph("add", "on_accent"))
        self.btn_new_type.setIcon(glyph("chevron", "on_accent"))
        self.btn_add_topic.setIcon(glyph("add"))
        self.btn_topic_sort.setIcon(glyph("sort"))
        self.btn_sort.setIcon(glyph("sort"))
        self.selection_button.setIcon(glyph("select"))
        self.back_button.setIcon(glyph("back"))
        self.btn_data.setIcon(glyph("settings"))
        for action in self.type_menu.actions():
            action.setIcon(glyph(action.data()))
        for row in range(self.nav.count()):
            item = self.nav.item(row)
            item.setIcon(glyph(item.data(Qt.UserRole)))
        self._sync_empty_icon()

    def _sync_empty_icon(self):
        icon = "search" if self._search_buf else self.view[0]
        self.empty_icon.setPixmap(glyph(icon if icon != "topic" else "folder", "accent_text").pixmap(26))

    def _open_grid_note(self, item):
        note = self.lib.notes.get(item.data(Qt.UserRole)) if item is not None else None
        if note is None:
            return
        self.save_now()
        if self.editor.note is not note:
            self.editor.set_note(note, focus=False)
        self._open_document()

    def _open_document(self, *, animate=True, focus=True):
        note = self.editor.note
        if note is None:
            return
        self._sync_document_topic(note)
        self.workspace.open_page(self.document_page, 1, animate=animate)
        if focus:
            self.editor.focus_document()

    def _sync_document_topic(self, note):
        topic = self.lib.topics.get(note.topic_id)
        self.document_topic.setText("回收站" if note.state == "trash" else topic.name if topic else "")

    def _show_library(self, *, focus=False):
        if self.workspace.currentWidget() is self.document_page:
            self.save_now()
            self.workspace.open_page(self.library_page, -1)
        if focus:
            self.note_list.setFocus()

    def _open_search_result(self):
        if not self.search.text().strip():
            return
        if self._search_timer.isActive():
            self._search_timer.stop()
            self._apply_search()
        self._open_grid_note(self.note_list.currentItem())

    def _reset_search(self):
        self._clear_search(focus=True)
        self._apply_search()

    def _toggle_selection_mode(self, selecting):
        self.note_list.open_on_click = not selecting
        self.note_list.setSelectionMode(QListWidget.MultiSelection if selecting else QListWidget.ExtendedSelection)
        self.selection_button.setText("完成选择" if selecting else "选择")
        self.note_list.clearSelection()
        self._sync_batch_actions()
        self.note_list.setFocus()

    def _sync_batch_actions(self):
        count = len(self.note_list.selectedItems())
        self.batch_button.setText(f"{count} 条已选")
        self.batch_button.setVisible(count > 1 or (self.selection_button.isChecked() and count > 0))

    def _show_batch_actions(self):
        pos = self.batch_button.mapToGlobal(QPoint(0, self.batch_button.height()))
        self._bulk_menu_at(pos, self.note_list.selectedItems())

    def _show_document_actions(self):
        if self.editor.note is None:
            return
        button = self.editor.btn_more
        self._show_note_actions(self.editor.note, button.mapToGlobal(QPoint(0, button.height())))

    def _build_shortcuts(self):
        for seq, fn in [
            ("Ctrl+N", self.new_note),
            ("Ctrl+Shift+N", self.new_topic),
            ("Ctrl+F", self._find_in_context),
            ("Ctrl+Shift+F", lambda: self.search.setFocus()),
            ("Ctrl+S", self.save_now),
            ("Ctrl+E", self._toggle_preview),
            ("Ctrl+Shift+E", self._show_edit_mode),
            ("Ctrl+,", self.open_settings),
            ("Alt+Left", lambda: self._show_library(focus=True)),
        ]:
            QShortcut(QKeySequence(seq), self, fn)
        for seq in ("Ctrl+Z",):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(self.undo)
        for seq in ("Ctrl+Shift+Z", "Ctrl+Y"):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(self.redo)

    # ---------------- 撤销 / 重做 ----------------
    @contextmanager
    def undoable(self, label: str, *, note_ids=(), topic_ids=(), state_keys=()):
        """把一次写操作包成可撤销的。

        yield 出来的是「本次新建出来的对象」容器（`.notes` / `.topics` 两个集合），
        操作里新建的笔记或主题塞进去，撤销时才知道该把它们也删掉。
        `_undo_guard` 打开时整段跳过记录。
        """
        if self._undo_guard:
            yield SimpleNamespace(notes=set(), topics=set())
            return
        ids_n = set(note_ids)
        ids_t = set(topic_ids)
        before = self.lib.snapshot(ids_n, ids_t)
        before_state = self._state_of(state_keys)
        scope = SimpleNamespace(notes=set(), topics=set())
        try:
            yield scope
        finally:
            ids_n |= scope.notes
            ids_t |= scope.topics
            after = self.lib.snapshot(ids_n, ids_t)
            self.undo_stack.push(
                Command(
                    label=label,
                    before=before,
                    after=after,
                    note_ids=ids_n,
                    topic_ids=ids_t,
                    before_state=before_state,
                    after_state=self._state_of(state_keys),
                    on_change=self._after_undo_change,
                )
            )

    def _state_of(self, keys) -> dict:
        out = {}
        for key in keys:
            if key == "sort_mode":
                out[key] = self.sort_mode
        return out

    def _after_undo_change(self, state: dict):
        """数据回滚之后让界面跟上：重新排列表、必要时切回列表页。"""
        self._pending_save = None
        if "sort_mode" in state and state["sort_mode"] != self.sort_mode:
            self._apply_sort(state["sort_mode"])
        self.refresh_topics()
        current = self.editor.note
        if current is not None and current.id not in self.lib.notes:
            self.editor.set_note(None)
            self._show_library()
        self._reload_notes(select_id=current.id if current else None, animate=False)

    def undo(self):
        label = self.undo_stack.undo()
        if label:
            self._toast(f"已撤销：{label}")
        else:
            self._toast("没有可以撤销的操作")
        return label

    def redo(self):
        label = self.undo_stack.redo()
        if label:
            self._toast(f"已重做：{label}")
        else:
            self._toast("没有可以重做的操作")
        return label

    # ---------------- 视图记忆 ----------------
    def _restore_view(self):
        """打开时回到上次的视图和笔记；记录失效就退回「全部笔记」。"""
        raw = self.settings.get("last_view") or {}
        kind = raw.get("kind") if isinstance(raw, dict) else None
        topic_id = raw.get("topic") if isinstance(raw, dict) else None
        last_note = self.settings.get("last_note") or None

        topic = self.lib.topics.get(topic_id) if isinstance(topic_id, str) else None
        if kind == "topic" and topic and topic.state != "trash":
            self.view = ("topic", topic_id)
            row = -1
            self.refresh_topics()
        else:
            kind = kind if kind in ("all", "archived", "trash") else "all"
            self.view = (kind, None)
            row = {"all": 0, "archived": 1, "trash": 2}[kind]
        self.nav.blockSignals(True)
        self.nav.setCurrentRow(row)
        self.nav.blockSignals(False)
        self._reload_notes(select_id=last_note)
        if self.settings.get("last_document") and self.editor.note is not None:
            self._open_document(animate=False, focus=False)

    def _save_view(self):
        kind, topic_id = self.view
        try:
            self.settings.set("last_view", {"kind": kind, "topic": topic_id or ""})
            note = getattr(self.editor, "note", None)
            self.settings.set("last_note", note.id if note else "")
            self.settings.set("last_document", self.workspace.currentWidget() is self.document_page)
        except Exception:
            pass

    # ---------------- 侧栏 ----------------
    def refresh_topics(self):
        current = self.view[1] if self.view[0] == "topic" else None
        self.topic_list.blockSignals(True)
        self.topic_list.clear()
        topics = [t for t in self.lib.topics.values() if t.state != "trash"]
        topics.sort(key=lambda t: (t.order, t.created_at))
        active_counts = self.lib.note_counts("active")
        archived_counts = self.lib.note_counts("archived")
        counts = {t.id: (archived_counts if t.state == "archived" else active_counts).get(t.id, 0) for t in topics}
        dlg = self.topic_list.itemDelegate()
        if dlg is not None:
            dlg.counts = counts
        for t in topics:
            it = QListWidgetItem(t.name)
            it.setData(Qt.UserRole, t)
            it.setToolTip(f"{t.name} · {counts.get(t.id, 0)} 条")
            self.topic_list.addItem(it)
        if current:
            for i in range(self.topic_list.count()):
                if getattr(self.topic_list.item(i).data(Qt.UserRole), "id", None) == current:
                    self.topic_list.setCurrentRow(i)
                    break
        self.topic_list.blockSignals(False)
        active = sum(1 for n in self.lib.notes.values() if n.state == "active")
        total = len(self.lib.notes)
        self.total_label.setText(f"{active} 条使用中 · 共 {total} 条")

    def select_nav(self, kind: str):
        row = {"all": 0, "archived": 1, "trash": 2}.get(kind, 0)
        if self.nav.currentRow() == row:
            self._on_nav_changed(row)
        else:
            self.nav.setCurrentRow(row)

    def _clear_search(self, focus: bool = False):
        """清掉搜索词（切换视图或按 Esc），免得列表停在上一轮的搜索结果里。"""
        self._search_buf = ""
        self._search_timer.stop()
        self.editor.set_highlight("")
        self.search.clear()
        if focus:
            self.note_list.setFocus()

    def _on_nav_changed(self, row: int):
        kind = {0: "all", 1: "archived", 2: "trash"}.get(row, "all")
        self._clear_search()
        self.topic_list.blockSignals(True)
        self.topic_list.clearSelection()
        self.topic_list.setCurrentRow(-1)
        self.topic_list.blockSignals(False)
        self.view = (kind, None)
        self._show_library()
        self._reload_notes(animate=True)

    def _on_topic_changed(self, row: int):
        if row < 0:
            return
        it = self.topic_list.item(row)
        if not it:
            return
        topic = it.data(Qt.UserRole)
        self._clear_search()
        self.nav.blockSignals(True)
        self.nav.setCurrentRow(-1)
        self.nav.blockSignals(False)
        self.view = ("topic", topic.id)
        self._show_library()
        self._reload_notes(animate=True)

    def _topic_menu(self, pos):
        it = self.topic_list.itemAt(pos)
        if not it:
            self._topic_sort_menu(pos)
            return
        topic = it.data(Qt.UserRole)
        menu = QMenu(self)
        a_new = menu.addAction("在此主题新建笔记")
        menu.addSeparator()
        a_rename = menu.addAction("重命名主题…")
        a_lock = menu.addAction("解除锁定" if topic.locked else "锁定主题")
        a_delete = menu.addAction("删除主题")
        act = menu.exec_(self.topic_list.mapToGlobal(pos))
        if act is a_new:
            self.new_note(topic_id=topic.id)
        elif act is a_lock:
            self.toggle_topic_lock(topic)
        elif act is a_rename:
            if topic.locked:
                info(self, "主题已锁定", f"「{topic.name}」已锁定，先解除锁定再改名。")
                return
            from .dialogs import InputDialog

            dlg = InputDialog(self, "重命名主题", "主题名称", text=topic.name)
            name = dlg.value if dlg.exec_() else None
            if name:
                if self._topic_name_taken(name, exclude_id=topic.id):
                    info(self, "已有同名主题", f"已经有一个叫「{name}」的主题了，换个名字吧。")
                    return
                with self.undoable(f"重命名主题「{topic.name}」", topic_ids=[topic.id]):
                    self.lib.rename_topic(topic.id, name)
                    self.lib.save()
                self.refresh_topics()
                if self.view == ("topic", topic.id):
                    self._reload_notes()
        elif act is a_delete:
            if topic.locked:
                info(self, "主题已锁定", f"「{topic.name}」已锁定，先解除锁定再删除。")
                return
            cnt = (self.lib.note_counts("archived") if topic.state == "archived" else self.lib.note_counts()).get(topic.id, 0)
            if cnt:
                if not confirm(
                    self,
                    "删除主题",
                    f"「{topic.name}」里还有 {cnt} 条笔记，删除主题会把它们一起移到回收站。继续？",
                    danger=True,
                ):
                    return
            children = [n.id for n in self.lib.notes.values() if n.topic_id == topic.id]
            with self.undoable(f"删除主题「{topic.name}」", note_ids=children, topic_ids=[topic.id]):
                self.lib.delete_topic(topic.id)
                self.lib.save()
            self.refresh_topics()
            self.select_nav("all")

    def _topic_sort_menu(self, pos):
        self._topic_sort_at(self.topic_list.mapToGlobal(pos))

    def _show_topic_sort(self, button):
        self._topic_sort_at(button.mapToGlobal(QPoint(0, button.height())))

    def _topic_sort_at(self, pos):
        menu = QMenu(self)
        a_name = menu.addAction("主题按名称排序")
        a_count = menu.addAction("主题按笔记数排序")
        a_time = menu.addAction("主题按最近更新排序")
        act = menu.exec_(pos)
        if act is a_name:
            self.sort_topics("name")
        elif act is a_count:
            self.sort_topics("count")
        elif act is a_time:
            self.sort_topics("updated")

    def sort_topics(self, by: str):
        """把主题列表重新排一遍（只影响非回收站主题，结果写回 order）。"""
        topics = [t for t in self.lib.topics.values() if t.state != "trash"]
        if len(topics) < 2:
            return
        from functools import cmp_to_key

        from ..store import compare_zh

        if by == "name":
            # 中文主题名按拼音排，不然「阿/白/陈」会变成码位乱序
            topics.sort(
                key=cmp_to_key(
                    lambda a, b: compare_zh(a.name, b.name)
                    or ((a.created_at > b.created_at) - (a.created_at < b.created_at))
                )
            )
            label = "名称"
        elif by == "count":
            counts = self.lib.note_counts()
            arch = self.lib.note_counts("archived")
            n_of = lambda t: (arch if t.state == "archived" else counts).get(t.id, 0)  # noqa: E731
            topics.sort(key=cmp_to_key(lambda a, b: (n_of(b) - n_of(a)) or compare_zh(a.name, b.name)))
            label = "笔记数"
        else:
            topics.sort(key=lambda t: (t.updated_at or t.created_at or ""), reverse=True)
            label = "最近更新"
        with self.undoable(f"主题按{label}排序", topic_ids=[t.id for t in topics]):
            self.lib.reorder_topics([t.id for t in topics])
            self.lib.save()
        self.refresh_topics()
        self._toast(f"主题已按{label}排序")

    def _topic_name_taken(self, name: str, exclude_id: str | None = None) -> bool:
        """是否已有同名主题（回收站里的不算，重命名自己也不算）。"""
        key = name.strip()
        return any(
            t.name.strip() == key and t.state != "trash" and t.id != exclude_id
            for t in self.lib.topics.values()
        )

    def new_topic(self):
        from .dialogs import InputDialog

        dlg = InputDialog(self, "新建主题", "主题名称", placeholder="比如：日记、工作、读书")
        name = dlg.value if dlg.exec_() else None
        if not name:
            return
        if self._topic_name_taken(name):
            info(self, "已有同名主题", f"已经有一个叫「{name}」的主题了，换个名字吧。")
            return
        with self.undoable("新建主题") as scope:
            t = self.lib.add_topic(name)
            scope.topics.add(t.id)
            self.lib.save()
        self.refresh_topics()
        for i in range(self.topic_list.count()):
            if self.topic_list.item(i).data(Qt.UserRole).id == t.id:
                self.topic_list.setCurrentRow(i)
                break
    # ---------------- 列表 ----------------
    def _current_notes(self) -> list[Note]:
        kind, tid = self.view
        order = getattr(self, "sort_mode", "updated")
        if kind == "all":
            return self.lib.sorted_notes(state="active", order=order)
        if kind == "archived":
            return self.lib.sorted_notes(state="archived", order=order)
        if kind == "trash":
            return self.lib.sorted_notes(state="trash", order=order)
        t = self.lib.topics.get(tid)
        state = "archived" if (t and t.state == "archived") else "active"
        return self.lib.topic_notes(tid, state=state, order=order)

    def _reload_notes(self, select_id: str | None = None, animate: bool = False):
        kind, tid = self.view
        title = "全部笔记"
        if kind == "archived":
            title = "归档"
        elif kind == "trash":
            title = "回收站"
        elif kind == "topic":
            t = self.lib.topics.get(tid)
            title = t.name if t else "主题"
        self.header_label.setText(title)

        delegate = self.note_list.itemDelegate()
        if delegate is not None:
            delegate.show_topic = kind in ("all", "archived") or bool(self._search_buf)
            delegate.term = self._search_buf or ""

        notes = self._current_notes()
        if self._search_buf:
            # 搜索和手机版一样是「全库搜索」：当前在回收站里才只搜回收站
            if kind == "trash":
                pool = notes
            else:
                pool = self.lib.sorted_notes(state="active") + self.lib.sorted_notes(state="archived")
            notes = self.lib.search(self._search_buf, pool=pool)
            # 搜索结果也跟着当前的排序方式走，不然和列表其它时候不一致
            if self.sort_mode != "updated":
                self.lib.sort_notes(notes, self.sort_mode)
            self.header_label.setText("搜索结果")
        self.header_label.setToolTip(title if not self._search_buf else self._search_buf)

        self.note_list.blockSignals(True)
        self.note_list.setUpdatesEnabled(False)
        try:
            self.note_list.clear()
            for n in notes:
                it = QListWidgetItem(n.display_title(self.lib.content(n.id)))
                it.setData(Qt.UserRole, n.id)
                self.note_list.addItem(it)
        finally:
            self.note_list.setUpdatesEnabled(True)
            self.note_list.blockSignals(False)
        count = f"{len(notes)} 条笔记"
        if self._search_buf:
            query = " ".join(self._search_buf.split())
            detail = f"「{query[:36]}{'…' if len(query) > 36 else ''}」"
            count = f"{count}匹配  ·  {detail}"
        self.count_label.setText(count)
        self.count_label.setTextFormat(Qt.PlainText)
        self.library_empty.setVisible(not notes)
        self.note_list.setVisible(bool(notes))
        self.selection_button.setEnabled(bool(notes))
        if notes:
            self.note_list.update_grid()
        else:
            if self._search_buf:
                self.list_empty.setText("没有找到匹配的笔记")
            elif kind == "trash":
                self.list_empty.setText("回收站是空的")
            elif kind == "archived":
                self.list_empty.setText("还没有归档的笔记")
            else:
                self.list_empty.setText("这里还没有笔记")
            self.empty_create.setVisible(kind not in ("trash", "archived") and not self._search_buf)
            self._sync_empty_icon()

        # 保留当前选中
        target = select_id or (self.editor.note.id if self.editor.note else None)
        found = -1
        if target:
            for i in range(self.note_list.count()):
                if self.note_list.item(i).data(Qt.UserRole) == target:
                    found = i
                    break
        if found >= 0:
            self.note_list.blockSignals(True)
            self.note_list.setCurrentRow(found)
            self.note_list.blockSignals(False)
            if select_id:
                self.note_list.scrollToItem(self.note_list.item(found))
            note = self.lib.notes.get(target)
            if self.editor.note is not note:
                self.editor.set_note(note, focus=False)
            else:
                self.editor.refresh_note_meta()
            self._sync_document_topic(note)
        else:
            self.note_list.blockSignals(True)
            self.note_list.setCurrentRow(-1)
            self.note_list.blockSignals(False)
            self.note_list.scrollToTop()
            self._show_library()
            self.editor.set_note(None, focus=False)

        if animate and notes:
            self.note_list.reveal_cards()
        self._sync_batch_actions()

    def _check_reminders(self):
        """把到点的提醒弹出来（每条只提醒一次）。"""
        due = self.lib.due_reminders()
        if not due:
            return
        names = "、".join(n.display_title(self.lib.content(n.id)) for n in due[:3])
        if len(due) > 3:
            names += " 等"
        text = f"{len(due)} 条笔记到时间了：{names}"
        if self.tray is not None:
            self.tray.showMessage("寒淋笔记 提醒", text, QSystemTrayIcon.Information, 8000)
        if self.isVisible():
            self._toast(text, 6000)
            QApplication.alert(self, 0)  # 任务栏图标闪烁，直到用户回到窗口
            self._reload_notes()
        else:
            self._reload_notes(select_id=self.editor.note.id if self.editor.note else None)

    def _on_notes_delete_key(self, note_ids):
        """列表上按 Delete：移到回收站（回收站里的笔记不在这里处理）。"""
        ids = [
            i
            for i in note_ids
            if i in self.lib.notes and self.lib.notes[i].state != "trash"
        ]
        if not ids:
            self._toast("回收站里的笔记请用右键彻底删除")
            return
        from .dialogs import confirm

        if not confirm(self, "移到回收站", f"把 {len(ids)} 条笔记移到回收站？", danger=True):
            return
        self._flush_save()
        with self.undoable(f"移到回收站 {len(ids)} 条", note_ids=ids):
            for nid in ids:
                self.lib.delete_note(nid)
            self.lib.save()
        self.refresh_topics()
        self._reload_notes(animate=True)
        self._toast(f"已移到回收站（{len(ids)} 条）")

    def _on_topics_reordered(self):
        """主题拖动排序后，按界面顺序写回 order 并保存。"""
        ids = []
        for i in range(self.topic_list.count()):
            it = self.topic_list.item(i)
            topic = it.data(Qt.UserRole) if it else None
            tid = getattr(topic, "id", None)
            if tid:
                ids.append(tid)
        if len(ids) < 2:
            return
        with self.undoable("调整主题顺序", topic_ids=ids):
            self.lib.reorder_topics(ids)

    def _move_targets(self):
        """右键「移动到主题」的可选目标：正常主题 + 归档主题（回收站里的不给选）。"""
        ts = [t for t in self.lib.topics.values() if t.state != "trash"]
        ts.sort(key=lambda t: (t.order, t.created_at))
        return ts

    def _move_label(self, t) -> str:
        return t.name + ("（已归档）" if t.state == "archived" else "")

    def _on_notes_dropped(self, note_ids, topic_id: str):
        """把笔记（可多选）拖到左侧主题上：批量移动到该主题。"""
        ids = [i for i in note_ids if i in self.lib.notes]
        if not ids:
            return
        self._flush_save()
        with self.undoable(f"移动 {len(ids)} 条笔记", note_ids=ids):
            moved = self.lib.move_notes(ids, topic_id)
        if not moved:
            return
        self.refresh_topics()
        self._reload_notes(animate=True)
        if len(ids) > 1:
            self._toast(f"已移动 {len(ids)} 条笔记")

    def _on_note_changed(self, row: int):
        if row < 0:
            return
        it = self.note_list.item(row)
        if not it:
            return
        self._flush_save()
        nid = it.data(Qt.UserRole)
        note = self.lib.notes.get(nid)
        self.editor.set_note(note, focus=False)

    def _note_menu(self, pos):
        items = self.note_list.selectedItems()
        if len(items) > 1:
            self._bulk_menu(pos, items)
            return
        it = self.note_list.itemAt(pos)
        if not it:
            return
        nid = it.data(Qt.UserRole)
        note = self.lib.notes.get(nid)
        if not note:
            return
        self._show_note_actions(note, self.note_list.viewport().mapToGlobal(pos))

    def _show_note_actions(self, note, pos):
        self.save_now()
        nid = note.id
        menu = QMenu(self)
        on_grid = self.workspace.currentWidget() is self.library_page
        a_open = menu.addAction("打开笔记") if on_grid else None
        if on_grid:
            menu.addSeparator()
        a_pin = menu.addAction("取消置顶" if note.pinned_at else "置顶")
        color_menu = menu.addMenu("颜色标记")
        for name, col in NOTE_COLORS:
            act = color_menu.addAction(color_icon(col), name)
            act.setData(col)
            if col == note.color:
                act.setCheckable(True)
                act.setChecked(True)
        menu.addSeparator()
        a_arch = menu.addAction("取消归档" if note.state == "archived" else "归档")
        move_menu = menu.addMenu("移动到主题")
        for t in self._move_targets():
            if t.id != note.topic_id:
                act = move_menu.addAction(self._move_label(t))
                act.setData(t.id)
        a_dup = menu.addAction("复制笔记")
        a_copy_md = menu.addAction("复制为 Markdown")
        a_export_md = menu.addAction("导出为 Markdown…")
        a_export_pdf = menu.addAction("导出为 PDF…")
        menu.addSeparator()
        a_del = a_restore = a_purge = None
        if note.state == "trash":
            a_restore = menu.addAction("从回收站恢复")
            a_purge = menu.addAction("彻底删除")
        else:
            a_del = menu.addAction("删除到回收站")
        act = menu.exec_(pos)
        if act is None:
            return
        if act is a_open:
            for row in range(self.note_list.count()):
                item = self.note_list.item(row)
                if item.data(Qt.UserRole) == nid:
                    self.note_list.setCurrentRow(row)
                    self._open_grid_note(item)
                    break
            return
        if act is a_copy_md:
            QApplication.clipboard().setText(note_markdown(note, self.lib.content(nid)))
            self._toast("已复制 Markdown")
            return
        if act is a_export_md:
            self._export_markdown(note)
            return
        if act is a_export_pdf:
            self._export_pdf(note)
            return
        if act is a_purge:
            if not confirm(self, "彻底删除", "这条笔记会被永久删除，无法恢复。继续？", danger=True):
                return
            with self.undoable("彻底删除 1 条笔记", note_ids=[nid]):
                self.lib.delete_note(nid, hard=True)
            self.refresh_topics()
            self._reload_notes(animate=True)
            self._toast("已永久删除")
            return

        # 剩下都是写操作：统一包进撤销栈
        if act is a_pin:
            label = "取消置顶" if note.pinned_at else "置顶"
        elif act is a_arch:
            label = "取消归档" if note.state == "archived" else "归档"
        elif act is a_del:
            label = "删除到回收站"
        elif act is a_restore:
            label = "从回收站恢复"
        elif act is a_dup:
            label = "复制笔记"
        elif act is not None and act.parent() is color_menu:
            label = "颜色标记"
        elif act is not None and act.parent() is move_menu:
            label = "移动笔记"
        else:
            return
        with self.undoable(label, note_ids=[nid]) as scope:
            if act is a_pin:
                self.lib.toggle_pin(nid)
            elif act is a_arch:
                self.lib.archive_note(nid, note.state != "archived")
            elif act is a_dup:
                copy = self.lib.add_note(note.topic_id, note.type, self.lib.content(nid), note.title)
                copy.url, copy.account, copy.password, copy.remark = note.url, note.account, note.password, note.remark
                copy.date, copy.images, copy.description = note.date, list(note.images), note.description
                scope.notes.add(copy.id)
            elif act is a_del:
                self.lib.delete_note(nid)
            elif act is a_restore:
                self.lib.restore_note(nid)
            elif act.parent() is color_menu:
                self.lib.toggle_color(nid, act.data())
            elif act.parent() is move_menu:
                self.lib.move_note(nid, act.data())
            self.lib.save()
        self.refresh_topics()
        self._reload_notes(animate=act in (a_del, a_arch, a_restore, a_dup))

    def _bulk_menu(self, pos, items):
        """多选时的批量操作菜单。"""
        self._bulk_menu_at(self.note_list.viewport().mapToGlobal(pos), items)

    def _bulk_menu_at(self, pos, items):
        ids = [it.data(Qt.UserRole) for it in items if it.data(Qt.UserRole) in self.lib.notes]
        n = len(ids)
        if not n:
            return
        menu = QMenu(self)
        if self.view[0] == "trash":
            a_restore = menu.addAction(f"恢复 {n} 条")
            a_purge = menu.addAction(f"彻底删除 {n} 条")
            a_pin = a_arch = None
        else:
            a_purge = None
            pinned = all(self.lib.notes[i].pinned_at for i in ids)
            a_pin = menu.addAction(f"取消置顶 {n} 条" if pinned else f"置顶 {n} 条")
            arch = self.view[0] == "archived"
            a_arch = menu.addAction(f"取消归档 {n} 条" if arch else f"归档 {n} 条")
            a_restore = None
        move_menu = menu.addMenu(f"移动到主题（{n} 条）")
        for t in self._move_targets():
            act = move_menu.addAction(self._move_label(t))
            act.setData(t.id)
        menu.addSeparator()
        a_del = menu.addAction(f"删除 {n} 条")
        act = menu.exec_(pos)
        if act is None:
            return
        if act is a_pin:
            self._bulk_apply(ids, "unpin" if self.lib.notes[ids[0]].pinned_at else "pin")
        elif act is a_arch:
            self._bulk_apply(ids, "unarchive" if self.view[0] == "archived" else "archive")
        elif act is a_restore:
            self._bulk_apply(ids, "restore")
        elif act is a_purge:
            if confirm(self, "彻底删除", f"{n} 条笔记会被永久删除，无法恢复。继续？", danger=True):
                self._bulk_apply(ids, "purge")
        elif act is a_del:
            self._bulk_apply(ids, "delete")
        elif act.parent() is move_menu:
            self._bulk_apply(ids, "move", act.data())

    def _bulk_apply(self, ids, kind: str, payload=None):
        """对一批笔记执行同一操作。kind: pin/unpin/archive/unarchive/restore/delete/purge/move"""
        self._flush_save()
        labels = {
            "pin": "置顶",
            "unpin": "取消置顶",
            "archive": "归档",
            "unarchive": "取消归档",
            "restore": "从回收站恢复",
            "delete": "移到回收站",
            "purge": "彻底删除",
            "move": "移动到主题",
        }
        with self.undoable(f"{labels.get(kind, '操作')} {len(ids)} 条笔记", note_ids=ids):
            if kind == "pin":
                self.lib.pin_notes(ids, True)
            elif kind == "unpin":
                self.lib.pin_notes(ids, False)
            elif kind == "archive":
                self.lib.archive_notes(ids, True)
            elif kind == "unarchive":
                self.lib.archive_notes(ids, False)
            elif kind == "restore":
                for nid in ids:
                    self.lib.restore_note(nid)
            elif kind == "delete":
                self.lib.delete_notes(ids)
            elif kind == "purge":
                self.lib.delete_notes(ids, hard=True)
            elif kind == "move" and payload:
                self.lib.move_notes(ids, payload)
            self.lib.save()
        self.refresh_topics()
        self._reload_notes(animate=True)

    # ---------------- 编辑保存 ----------------
    def new_note(self, note_type: str = "note", *, topic_id: str | None = None):
        self.save_now()
        kind, tid = self.view
        topic_id = topic_id or (tid if kind == "topic" else None)
        if not topic_id:
            topics = self.lib.sorted_topics()
            if not topics:
                self.new_topic()
                topics = self.lib.sorted_topics()
                if not topics:
                    return
            # 全部/归档视图里新建：优先落到上次用过的主题，别每次都塞给第一个
            last = self.settings.get("last_topic_id", "")
            t = self.lib.topics.get(last)
            if not t or t.state == "trash":
                t = topics[0]
            topic_id = t.id
        self._clear_search()
        with self.undoable(f"新建{TYPE_LABELS.get(note_type, '笔记')}") as scope:
            note = self.lib.add_note(topic_id, note_type, "", None)
            scope.notes.add(note.id)
            self.lib.save()
        self.settings.set("last_topic_id", topic_id)
        self.refresh_topics()
        if self.view != ("topic", topic_id):
            self.view = ("topic", topic_id)
            for i in range(self.topic_list.count()):
                if self.topic_list.item(i).data(Qt.UserRole).id == topic_id:
                    self.topic_list.blockSignals(True)
                    self.topic_list.setCurrentRow(i)
                    self.topic_list.blockSignals(False)
                    break
        self._reload_notes(select_id=note.id, animate=True)
        self._open_document()

    def _on_content_changed(self, note_id: str, content: str, title: str):
        self._pending_save = (note_id, content, title)
        self._save_timer.start()

    def _on_meta_changed(self):
        self.refresh_topics()
        self._reload_notes()

    def _flush_save(self):
        if not self._pending_save:
            return
        note_id, content, title = self._pending_save
        self._pending_save = None
        note = self.lib.notes.get(note_id)
        if not note:
            return
        self.lib.set_content(note_id, content)
        if self.editor.note and self.editor.note.id == note_id:
            self.editor.flush_note()
        if note.type == "article" and title is not None:
            note.title = title.strip() or None
        self.lib.touch(note_id)
        try:
            self.lib.save()
        except OSError as exc:  # 磁盘满、目录只读、文件被杀软占用
            self._save_failed(exc)
            return
        self._save_error_shown = False
        self.editor.mark_saved()
        self._refresh_item(note_id)

    def _save_failed(self, exc: OSError) -> None:
        """写盘失败时不要吞掉：把状态标成未保存，并提示一次原因。"""
        self.editor.save_state.setText("未保存")
        if getattr(self, "_save_error_shown", False):
            return
        self._save_error_shown = True
        self._toast("保存失败：磁盘可能已满，或数据目录没有写入权限", 6000)

    def save_now(self):
        self._save_timer.stop()
        self._flush_save()

    def _refresh_item(self, note_id: str):
        for i in range(self.note_list.count()):
            item = self.note_list.item(i)
            if item.data(Qt.UserRole) == note_id:
                note = self.lib.notes.get(note_id)
                if note:
                    item.setText(note.display_title(self.lib.content(note_id)))
                self.note_list.update(self.note_list.indexFromItem(item))
                break

    def _toggle_preview(self):
        if not self.editor.note:
            return
        m = getattr(self.editor, "_mode", "split")
        self.editor.set_mode("preview" if m != "preview" else "split")
        self._open_document()

    def _show_edit_mode(self):
        if self.editor.note:
            self.editor.set_mode("edit")
            self._open_document()

    def _find_in_context(self):
        editor = self.editor
        if self.workspace.currentWidget() is self.document_page and any(
            widget.hasFocus() for widget in (editor.edit, editor.preview, editor.find_edit)
        ):
            editor.show_find()
        else:
            self.search.setFocus()

    def _on_search_changed(self, text: str):
        term = text.strip()
        if term == self._search_buf:
            return
        self._search_buf = term
        self._show_library()
        self._search_timer.start()

    def _apply_search(self):
        self._search_timer.stop()
        term = self.search.text().strip()
        self._search_buf = term
        self.editor.set_highlight(term if term else "")
        self._reload_notes(animate=True)
        # 搜索后预选第一条。回车直接打开并显示正文中的匹配。
        if term and self.note_list.count():
            self.note_list.setCurrentRow(0)

    # ---------------- 数据操作 ----------------
    def import_zip(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入 Timo 导出包", "", "Timo 导出 (*.zip);;所有文件 (*)")
        if not path:
            return
        self._do_import(path)

    def import_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择导出文件夹（含 manifest.json）")
        if not path:
            return
        self._do_import(path)

    # ---------------- 拖拽导入 ----------------
    TEXT_EXTS = (".md", ".markdown", ".txt")

    @staticmethod
    def _droppable(path: str) -> bool:
        if not path:
            return False
        low = path.lower()
        return low.endswith(".zip") or low.endswith(MainWindow.TEXT_EXTS) or Path(path).is_dir()

    def _has_droppable(self, e) -> bool:
        md = e.mimeData()
        if md is None or not md.hasUrls():
            return False
        return any(self._droppable(u.toLocalFile()) for u in md.urls())

    def dragEnterEvent(self, e):
        if self._has_droppable(e):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if self._has_droppable(e):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        md = e.mimeData()
        if md is not None and md.hasUrls():
            for u in md.urls():
                p = u.toLocalFile()
                if self._droppable(p):
                    e.acceptProposedAction()
                    if p.lower().endswith(MainWindow.TEXT_EXTS):
                        self._import_text_file(p)
                    else:
                        self._do_import(p)
                    return
        e.ignore()

    def _import_text_file(self, path: str):
        """把 .md / .txt 拖进来 → 在当前主题新建一条笔记。"""
        try:
            raw = Path(path).read_bytes()
        except OSError as exc:
            self._toast(f"读不了这个文件：{exc}")
            return
        text = None
        for enc in ("utf-8-sig", "utf-8", "gbk"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            self._toast("这个文件的编码认不出来")
            return
        stem = Path(path).stem.strip() or "新笔记"
        body = text.replace("\r\n", "\n").strip()
        title = ""
        lines = body.split("\n")
        if lines and lines[0].startswith("# "):
            title = lines[0][2:].strip()
            body = "\n".join(lines[1:]).strip()
        topic_id = self.view[1] if self.view[0] == "topic" and self.view[1] else None
        if not topic_id:
            topics = self.lib.sorted_topics()
            topic_id = topics[0].id if topics else self.lib.add_topic("我的笔记").id
        if title:
            note = self.lib.add_note(topic_id, "article", content=body, title=title)
        else:
            note = self.lib.add_note(topic_id, "note", content=body, title=stem)
        self.lib.save()
        self.refresh_topics()
        self._reload_notes(select_id=note.id, animate=True)
        self._open_document()
        self._toast(f"已导入「{note.display_title(self.lib.content(note.id))}」")

    def _do_import(self, path: str):
        self.save_now()
        mode = "replace"
        if self.lib.notes:
            from .dialogs import ImportModeDialog

            pick = ImportModeDialog(self, len(self.lib.notes))
            if not pick.exec_() or not pick.mode:
                return
            mode = pick.mode
        dlg = ProgressDialog(self, "导入 Timo 数据", "正在导入…")
        dlg.show()
        QApplication.processEvents()

        def progress(done: int, total: int):
            dlg.set_progress(done, total)
            QApplication.processEvents()
            if dlg.was_canceled():
                raise ImportCanceled()

        backup = None
        try:
            backup = self._backup_current() if mode == "replace" else None
            report = import_export(path, self.lib, progress=progress, mode=mode)
            self.lib.save()
        except ImportCanceled:
            dlg.close()
            restored = self._restore_from_backup(backup) if backup else False
            if restored:
                info(self, "已取消", "导入已取消，数据已还原成导入前的样子。")
            else:
                info(self, "已取消", "导入已取消。")
            return
        except Exception as e:  # noqa: BLE001
            dlg.close()
            error(self, "导入失败", f"{e}")
            return
        dlg.close()
        self.refresh_topics()
        self.select_nav("all")
        msg = report.summary().split("\n")[0] + "。"
        if report.skipped:
            msg += f"\n跳过已存在的笔记 {report.skipped} 条。"
        if report.missing_attachments:
            msg += f"\n有 {len(report.missing_attachments)} 个附件缺失。"
        if report.warnings:
            msg += "\n" + "\n".join(report.warnings[:5])
        if backup:
            msg += f"\n原数据已备份到：{backup}"
        info(self, "导入完成", msg)

    def _restore_from_backup(self, bdir: Path | None) -> bool:
        """导入被取消时把库还原回导入前。"""
        if not bdir or not bdir.exists():
            return False
        import shutil

        try:
            meta = bdir / "library.json"
            if meta.exists():
                shutil.copy2(meta, self.lib.meta_path)
            for name, real in (("notes", self.lib.notes_dir), ("attachments", self.lib.attachments_dir)):
                src = bdir / name
                if not src.exists():
                    continue
                if real.exists():
                    shutil.rmtree(real)
                shutil.copytree(src, real)
            self.lib.load()
            self.editor.set_note(None)
            self.refresh_topics()
            self._reload_notes()
            return True
        except Exception:
            return False

    def _backup_current(self) -> Path | None:
        if not self.lib.notes:
            return None
        import time

        bdir = self.lib.root / "backup" / time.strftime("%Y%m%d-%H%M%S")
        bdir.mkdir(parents=True, exist_ok=True)
        import shutil

        if self.lib.meta_path.exists():
            shutil.copy2(self.lib.meta_path, bdir / "library.json")
        if self.lib.notes_dir.exists():
            shutil.copytree(self.lib.notes_dir, bdir / "notes")
        if self.lib.attachments_dir.exists():
            shutil.copytree(self.lib.attachments_dir, bdir / "attachments")
        return bdir

    def empty_trash(self):
        """回收站里彻底删掉，和手机版一样有二次确认。"""
        notes = self.lib.sorted_notes(state="trash")
        if not notes:
            info(self, "清空回收站", "回收站是空的。")
            return
        if not confirm(
            self, "清空回收站", f"回收站里的 {len(notes)} 条笔记会被永久删除，无法恢复。继续？", danger=True
        ):
            return
        with self.undoable(f"清空回收站（{len(notes)} 条）", note_ids=[x.id for x in notes]):
            n = self.lib.empty_trash()
        self.refresh_topics()
        self._reload_notes(animate=True)
        self._toast(f"已永久删除 {n} 条笔记")

    def toggle_topic_lock(self, topic):
        """锁定后主题不能改名、不能删除，避免手滑。"""
        topic.locked = not topic.locked
        self.lib.save()
        self.refresh_topics()
        self._toast("已锁定主题" if topic.locked else "已解除锁定")

    def open_settings(self, section_index=0):
        from .dialogs import SettingsDialog

        previous_font = self.settings.get("font_family", "宋体")
        dlg = SettingsDialog(
            self,
            self.settings,
            self.settings.get("theme_mode", "light"),
            getattr(self, "sort_mode", "updated"),
            section_index,
        )
        if dlg.exec_() != QDialog.Accepted:
            return
        if dlg.theme_changed:
            self.set_theme(self.settings.get("theme_mode", "light"))
        want_sort = dlg.cmb_sort.currentData()
        if want_sort != getattr(self, "sort_mode", "updated"):
            self.set_sort(want_sort)
        want_font = dlg.cmb_font.currentData()
        if want_font != previous_font:
            self.apply_font(want_font)

    def apply_font(self, family: str) -> None:
        """切换正文/预览字体：改 QSS + 给列表和编辑器换字体。"""
        from PyQt5.QtGui import QFont

        from .theme import app_qss, set_text_font

        family = set_text_font(family)
        self.settings.set("font_family", family)
        QApplication.instance().setStyleSheet(app_qss())

        def font_like(widget):
            """沿用控件原来的字号，只换字体族，避免切字体后整界面忽大忽小。"""
            f = QFont(family)
            if widget.font().pixelSize() > 0:
                f.setPixelSize(widget.font().pixelSize())
            elif widget.font().pointSizeF() > 0:
                f.setPointSizeF(widget.font().pointSizeF())
            return f

        for w in (self.note_list, self.topic_list, self.editor):
            w.setFont(font_like(w))
        # 编辑器里的正文框/预览框是构造时设的字体，这里一并换掉
        for w in self.editor.findChildren(QWidget):
            if isinstance(w, (QPlainTextEdit, QTextBrowser)):
                w.setFont(font_like(w))
        self.note_list.viewport().update()
        self.topic_list.viewport().update()
        self.editor.refresh_theme()
        self.update()

    def dedupe_dialog(self):
        """找出正文完全相同的笔记，勾选后移到回收站。"""
        self._flush_save()
        groups = self.lib.duplicate_groups()
        if not groups:
            info(self, "没有发现重复笔记", "所有笔记的正文都不相同。")
            return
        dlg = DuplicatesDialog(self, self.lib, groups)
        if not dlg.exec_():
            return
        ids = dlg.to_delete()
        if not ids:
            return
        n = self.lib.delete_notes(ids)
        self.refresh_topics()
        self._reload_notes()
        self._toast(f"已把 {n} 条重复笔记移到回收站")

    def audit_data(self):
        """看看正文文件、附件跟 library.json 对不对得上。"""
        r = self.lib.audit()
        lines = [f"共 {r['notes']} 条笔记、{r['attachments']} 个附件文件。"]
        problems = False
        if r["missing_md"]:
            problems = True
            lines.append(f"\n正文文件缺失 {len(r['missing_md'])} 条（打开会是空内容）：")
            for _nid, title in r["missing_md"][:8]:
                lines.append(f"  · {title}")
            if len(r["missing_md"]) > 8:
                lines.append(f"  · 还有 {len(r['missing_md']) - 8} 条…")
        if r["missing_attachments"]:
            problems = True
            lines.append(f"\n引用到但磁盘上没有的图片 {len(r['missing_attachments'])} 个：")
            for name in r["missing_attachments"][:6]:
                lines.append(f"  · {name}")
            if len(r["missing_attachments"]) > 6:
                lines.append(f"  · 还有 {len(r['missing_attachments']) - 6} 个…")
        if r["unused_attachments"]:
            problems = True
            lines.append(
                f"\n没有被引用的图片 {len(r['unused_attachments'])} 个"
                "（可用「清理未使用的图片…」删掉）。"
            )
        if not problems:
            lines.append("\n没有发现问题，数据和文件是对得上的。")
        info(self, "数据检查", "\n".join(lines))

    def clean_attachments(self):
        """删掉 attachments/ 里没有任何笔记引用的图片（粘贴后又删掉文字常会留下）。"""
        unused = self.lib.unused_attachments()
        if not unused:
            info(self, "清理未使用的图片", "没有发现多余的图片，附件目录是干净的。")
            return
        size = 0
        for name in unused:
            try:
                size += self.lib.attachment_path(name).stat().st_size
            except OSError:
                pass
        sample = "、".join(unused[:3]) + ("…" if len(unused) > 3 else "")
        if not confirm(
            self,
            "清理未使用的图片",
            f"有 {len(unused)} 个图片文件没有被任何笔记引用（约 {size / 1024 / 1024:.1f} MB）：\n{sample}\n\n"
            "删除后无法恢复（备份里也不含附件）。继续？",
            danger=True,
        ):
            return
        n = self.lib.purge_attachments(unused)
        self._toast(f"已清理 {n} 个文件")

    def export_zip(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出为 Timo ZIP", f"Timo_Desktop_Export_{_stamp()}.zip", "ZIP (*.zip)"
        )
        if not path:
            return
        try:
            out, counts = export_zip(self.lib, path)
        except Exception as e:  # noqa: BLE001
            error(self, "导出失败", str(e))
            return
        info(
            self,
            "导出完成",
            f"已导出到：\n{out}\n\n{counts['topics']} 个主题、{counts['documents']} 条笔记、{counts['attachments']} 个附件。",
        )

    def backup_dialog(self):
        from .dialogs import BackupDialog

        d = BackupDialog(self, self.lib)
        if d.exec_() and d.restored:
            self.editor.set_note(None)
            self.refresh_topics()
            self._reload_notes(animate=True)

    def open_data_dir(self):
        from PyQt5.QtGui import QDesktopServices
        from PyQt5.QtCore import QUrl

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.lib.root)))

    def _toast(self, text: str, ms: int = 1600):
        self.notice.present(text, ms)

    def _export_markdown(self, note: Note):
        content = self.lib.content(note.id)
        md = note_markdown(note, content)
        # 导出包用 ../../../attachments/，单独导出时改成相对当前目录
        md = md.replace("../../../attachments/", "attachments/")
        t = self.lib.topics.get(note.topic_id)
        title = note.display_title(content, t.name if t else "")
        safe = re.sub(r'[\\/:*?"<>|]', "_", title).strip()[:60] or "未命名"
        path, _ = QFileDialog.getSaveFileName(self, "导出为 Markdown", f"{safe}.md", "Markdown (*.md)")
        if not path:
            return
        p = Path(path)
        try:
            p.write_text(md, encoding="utf-8")
            copied = 0
            for rel in sorted(set(re.findall(r"\]\((attachments/[^)\s]+)\)", md))):
                name = rel.split("/", 1)[1]
                src = self.lib.attachment_abspath(name)
                if src and Path(src).exists():
                    dst = p.parent / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    copied += 1
        except OSError as e:
            error(self, "导出失败", f"写入文件时出错：\n{e}")
            return
        extra = f"，图片 {copied} 张" if copied else ""
        info(self, "已导出", f"已保存到\n{p}{extra}")

    def _export_pdf(self, note: Note):
        """用 Markdown 预览的排版生成浅底 PDF。"""
        from PyQt5.QtGui import QTextDocument
        from PyQt5.QtPrintSupport import QPrinter

        from ..render import render

        content = self.lib.content(note.id)
        t = self.lib.topics.get(note.topic_id)
        title = note.display_title(content, t.name if t else "")
        safe = re.sub(r'[\\/:*?"<>|]', "_", title).strip()[:60] or "未命名"
        path, _ = QFileDialog.getSaveFileName(self, "导出为 PDF", f"{safe}.pdf", "PDF (*.pdf)")
        if not path:
            return
        doc = QTextDocument()
        doc.setHtml(render(content, self.lib.root, for_print=True))
        doc.setDocumentMargin(0)
        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(path)
        printer.setPageMargins(16, 16, 16, 16, QPrinter.Millimeter)
        try:
            doc.print_(printer)
        except Exception as e:  # pragma: no cover - 打印机子系统异常
            error(self, "导出失败", f"写入 PDF 时出错：\n{e}")
            return
        if not Path(path).exists():
            error(self, "导出失败", "没有生成文件，请换个位置再试。")
            return
        self._toast(f"已导出 {Path(path).name}")

    def export_md_tree(self):
        """导出成「主题/笔记.md + attachments/」，方便交给别的编辑器。"""
        from ..exporter import export_markdown_tree

        out = QFileDialog.getExistingDirectory(self, "选择导出位置", str(Path.home()))
        if not out:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            counts = export_markdown_tree(self.lib, out)
        except OSError as e:
            QApplication.restoreOverrideCursor()
            error(self, "导出失败", f"写入时出错：\n{e}")
            return
        QApplication.restoreOverrideCursor()
        info(
            self,
            "导出完成",
            f"主题 {counts['topics']} 个、笔记 {counts['notes']} 条、图片 {counts['attachments']} 张。\n\n{out}",
        )

    def set_sort(self, mode: str):
        if mode == getattr(self, "sort_mode", "updated"):
            return
        with self.undoable("切换排序", state_keys=("sort_mode",)):
            self._apply_sort(mode)

    def _apply_sort(self, mode: str):
        """真正换排序方式（撤销栈恢复界面状态时也走这里，不再记录一遍）。"""
        self.sort_mode = mode
        self.settings.set("sort_mode", mode)
        self._sync_sort_menu()
        self._reload_notes(animate=True)

    def _sync_sort_menu(self):
        labels = {"updated": "最近更新", "created": "最近创建", "title": "标题"}
        for act in self.sort_menu.actions():
            act.setChecked(act.data() == self.sort_mode)
        label = labels.get(self.sort_mode, "最近更新")
        self.btn_sort.setText(label)
        self.btn_sort.setToolTip(f"排序：{label}")

    def set_theme(self, mode: str) -> None:
        """切换浅色 / 深色 / 跟随系统，立即生效并记住选择。"""
        self.workspace.clear_transition()
        apply_theme(QApplication.instance(), resolve_theme(mode))
        self.settings.set("theme_mode", mode)
        if self._theme_group:
            for act in self._theme_group.actions():
                act.setChecked(act.data() == mode)
        self._sync_chrome_icons()
        self.note_list.viewport().update()
        self.topic_list.viewport().update()
        self._edge.update()
        self.editor.refresh_theme()
        self.update()

    def trash_topics_dialog(self):
        """回收站里的主题：恢复（连同笔记）或彻底删除。"""
        from .dialogs import TrashTopicsDialog

        trash_ids = [t.id for t in self.lib.topics.values() if t.state == "trash"]
        note_ids = [n.id for n in self.lib.notes.values() if n.topic_id in set(trash_ids)]
        with self.undoable("整理已删除的主题", note_ids=note_ids, topic_ids=trash_ids):
            dlg = TrashTopicsDialog(self, self.lib)
            dlg.exec_()
            changed = dlg.changed
        if changed:
            self.refresh_topics()
            self._reload_notes(animate=True)

    def show_about(self):
        from timodesk import __version__

        from .dialogs import AboutDialog

        AboutDialog(self, self.lib, __version__).exec_()

    def _restore_geometry(self):
        try:
            geo = self.settings.get("window_geometry")
            if geo:
                from PyQt5.QtCore import QByteArray

                self.restoreGeometry(QByteArray.fromBase64(str(geo).encode()))
        except Exception:
            pass
        # 换过显示器 / 拔掉外接屏后，上次的位置可能落在屏幕外，捡回来
        if not self._on_screen():
            self.resize(1280, 830)
            self._center_on_screen()

    def _on_screen(self) -> bool:
        rect = self.frameGeometry()
        for scr in QApplication.screens():
            avail = scr.availableGeometry()
            inter = avail.intersected(rect)
            if inter.width() >= 160 and inter.height() >= 120:
                return True
        return False

    def _center_on_screen(self):
        scr = QApplication.primaryScreen()
        if scr is None:
            return
        avail = scr.availableGeometry()
        self.move(avail.center() - self.rect().center())

    def _save_geometry(self):
        try:
            self.settings.set("window_geometry", bytes(self.saveGeometry().toBase64()).decode())
        except Exception:
            pass

    def closeEvent(self, e):  # noqa: N802
        self._save_geometry()
        self._save_view()
        self.save_now()
        self.lib.save()
        # 有托盘时，关闭 = 收进托盘；从托盘菜单退出才算真退出
        if self.tray is not None and not self._quitting:
            e.ignore()
            self.hide()
            if not self._tray_hinted:
                self._tray_hinted = True
                self.tray.showMessage(
                    "寒淋笔记",
                    "已收进托盘。点托盘图标可以重新打开，右键可以退出。",
                    QSystemTrayIcon.Information,
                    4000,
                )
            return
        super().closeEvent(e)

    # ---------------- 无边框窗口 ----------------
    def toggle_maximized(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self.title_bar.sync_max_button()
        self._apply_mask()

    def _apply_mask(self):
        if not hasattr(self, "title_bar"):
            return
        if self.isMaximized() or self.isFullScreen():
            self.clearMask()
        else:
            self.setMask(rounded_mask(self.width(), self.height()))

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._apply_mask()
        edge = getattr(self, "_edge", None)
        if edge is not None:
            edge.setGeometry(self.centralWidget().rect())
            edge.raise_()
        if hasattr(self, "notice"):
            self.notice.reposition()

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        self._apply_mask()
        self.title_bar.sync_max_button()
        if not getattr(self, "_focused_initial_page", False):
            self._focused_initial_page = True
            if self.workspace.currentWidget() is self.document_page:
                self.editor.focus_document()
            else:
                (self.note_list if self.note_list.count() else self.nav).setFocus()

    def changeEvent(self, e):  # noqa: N802
        if e.type() == QEvent.WindowStateChange:
            QTimer.singleShot(0, self._on_window_state)
        super().changeEvent(e)

    def _on_window_state(self):
        if hasattr(self, "title_bar"):
            self.title_bar.sync_max_button()
        self._apply_mask()

    def _hit_border(self, pos: QPoint) -> int:
        """鼠标在窗口边缘时返回 Win32 命中码，交给系统处理缩放。"""
        w, h = self.width(), self.height()
        x, y = pos.x(), pos.y()
        edge = RESIZE_BORDER
        left, right = x <= edge, x >= w - edge
        top, bottom = y <= edge, y >= h - edge
        if top and left:
            return HTTOPLEFT
        if top and right:
            return HTTOPRIGHT
        if bottom and left:
            return HTBOTTOMLEFT
        if bottom and right:
            return HTBOTTOMRIGHT
        if left:
            return HTLEFT
        if right:
            return HTRIGHT
        if top:
            return HTTOP
        if bottom:
            return HTBOTTOM
        return 0

    def nativeEvent(self, eventType, message):  # noqa: N802
        if eventType not in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            return super().nativeEvent(eventType, message)
        try:
            msg = wintypes.MSG.from_address(int(message))
        except Exception:  # noqa: BLE001
            return super().nativeEvent(eventType, message)
        if msg.message == WM_NCHITTEST and not self.isMaximized():
            pos = self.mapFromGlobal(QCursor.pos())
            if 0 <= pos.x() < self.width() and 0 <= pos.y() < self.height():
                hit = self._hit_border(pos)
                if hit:
                    return True, hit
        return super().nativeEvent(eventType, message)


def _stamp() -> str:
    import time

    return time.strftime("%Y%m%d_%H%M%S")
