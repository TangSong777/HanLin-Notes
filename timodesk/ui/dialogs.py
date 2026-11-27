"""自绘对话框：消息、确认、进度。全部无边框，与主窗口风格一致。"""
from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon, QTextCharFormat
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QListWidget,
    QListWidgetItem,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
)

from .title_bar import FramelessDialog, app_icon
from .visuals import glyph



def _button(text: str, name: str = "Plain") -> QPushButton:
    b = QPushButton(text)
    b.setObjectName(name)
    b.setCursor(Qt.PointingHandCursor)
    b.setMinimumHeight(36)
    return b


class MessageDialog(FramelessDialog):
    def __init__(self, parent, title: str, text: str, ok_text="好", cancel_text=None, *, danger=False):
        super().__init__(parent, title)
        self.ok = False

        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(28, 22, 28, 20)
        lay.setSpacing(18)

        message = QHBoxLayout()
        message.setSpacing(14)
        symbol = QLabel(objectName="DialogSymbol")
        symbol.setFixedSize(38, 38)
        symbol.setAlignment(Qt.AlignCenter)
        symbol.setPixmap(glyph("attention" if danger else "select", "danger" if danger else "accent_text").pixmap(20))
        message.addWidget(symbol, alignment=Qt.AlignTop)
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setTextFormat(Qt.PlainText)
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        message.addWidget(lab, 1, Qt.AlignVCenter)
        lay.addLayout(message)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        if cancel_text:
            b = _button(cancel_text)
            b.clicked.connect(self.reject)
            row.addWidget(b)
        ok = _button(ok_text, "Danger" if danger else "Primary")
        ok.clicked.connect(self._accept)
        ok.setDefault(True)
        row.addWidget(ok)
        lay.addLayout(row)

        self.setMinimumWidth(420)
        self.adjustSize()
        self.add_edge()

    def _accept(self):
        self.ok = True
        self.accept()


class ProgressDialog(FramelessDialog):
    def __init__(self, parent, title: str, text: str):
        super().__init__(parent, title)
        self._canceled = False

        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(28, 22, 28, 20)
        lay.setSpacing(14)

        self.label = QLabel(text)
        self.label.setWordWrap(True)
        header = QHBoxLayout()
        header.addWidget(self.label, 1)
        self.progress_count = QLabel(objectName="FieldLabel")
        header.addWidget(self.progress_count)
        lay.addLayout(header)

        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setRange(0, 100)
        lay.addWidget(self.bar)

        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_cancel = _button("取消", "Link")
        self.btn_cancel.setMinimumHeight(26)
        self.btn_cancel.clicked.connect(self._cancel)
        row.addWidget(self.btn_cancel)
        lay.addLayout(row)

        self.setFixedWidth(460)
        self.add_edge()

    def _cancel(self):
        self._canceled = True
        self.label.setText("正在取消…")

    def was_canceled(self) -> bool:
        return self._canceled

    def set_progress(self, done: int, total: int, text: str | None = None):
        self.bar.setMaximum(max(1, total))
        self.bar.setValue(done)
        self.progress_count.setText(f"{done} / {total}" if total else "")
        if text:
            self.label.setText(text)


def info(parent, title: str, text: str, ok_text="好") -> None:
    MessageDialog(parent, title, text, ok_text).exec_()


def confirm(parent, title: str, text: str, ok_text="继续", cancel_text="取消", *, danger=False) -> bool:
    d = MessageDialog(parent, title, text, ok_text, cancel_text, danger=danger)
    d.exec_()
    return d.ok


def error(parent, title: str, text: str) -> None:
    MessageDialog(parent, title, text, "好").exec_()


class ReminderDialog(FramelessDialog):
    """设置提醒时间：快捷按钮 + 日期 + 时间。"""

    def __init__(self, parent, when=None, has_reminder: bool = False):
        from datetime import datetime, timedelta

        super().__init__(parent, "设置提醒")
        from PyQt5.QtCore import QDate, QTime
        from PyQt5.QtWidgets import QDateEdit, QHBoxLayout, QLabel, QPushButton, QTimeEdit

        if isinstance(when, str):  # 兼容直接传 ISO 字符串
            from ..store import from_iso

            when = from_iso(when)
        base = when or (datetime.now() + timedelta(hours=1))
        self.cleared = False
        self.result_when: datetime | None = None

        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(14)

        tip = QLabel("到时间后会从右下角弹出提醒。", objectName="FieldLabel")
        lay.addWidget(tip)

        quick = QHBoxLayout()
        quick.setSpacing(8)
        now = datetime.now()
        for label, target in (
            ("一小时后", now + timedelta(hours=1)),
            ("今天 20:00", now.replace(hour=20, minute=0, second=0, microsecond=0)),
            ("明天 9:00", (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)),
        ):
            b = QPushButton(label)
            b.setObjectName("Plain")
            b.clicked.connect(lambda _=False, t=target: self._set(t))
            quick.addWidget(b)
        quick.addStretch(1)
        lay.addLayout(quick)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        calendar = self.date.calendarWidget()
        for day in (Qt.Saturday, Qt.Sunday):
            calendar.setWeekdayTextFormat(day, QTextCharFormat())
        for name, text in (("qt_calendar_prevmonth", "‹"), ("qt_calendar_nextmonth", "›")):
            button = calendar.findChild(QToolButton, name)
            if button is not None:
                button.setIcon(QIcon())
                button.setText(text)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.setDate(QDate(base.year, base.month, base.day))
        self.time = QTimeEdit()
        self.time.setDisplayFormat("HH:mm")
        self.time.setTime(QTime(base.hour, base.minute))
        row.addWidget(self.date, 2)
        row.addWidget(self.time, 1)
        row.addStretch(1)
        lay.addLayout(row)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        if has_reminder:
            b_clear = QPushButton("删除提醒")
            b_clear.setObjectName("Danger")
            b_clear.clicked.connect(self._clear)
            btns.addWidget(b_clear)
        btns.addStretch(1)
        b_cancel = QPushButton("取消")
        b_cancel.setObjectName("Plain")
        b_cancel.clicked.connect(self.reject)
        b_ok = QPushButton("确定")
        b_ok.setObjectName("Primary")
        b_ok.clicked.connect(self._accept)
        btns.addWidget(b_cancel)
        btns.addWidget(b_ok)
        lay.addLayout(btns)
        lay.addStretch(1)

        self.setMinimumWidth(420)
        self.adjustSize()
        self.add_edge()

    def _set(self, dt):
        from PyQt5.QtCore import QDate, QTime

        self.date.setDate(QDate(dt.year, dt.month, dt.day))
        self.time.setTime(QTime(dt.hour, dt.minute))

    def _accept(self):
        from datetime import datetime

        d = self.date.date()
        t = self.time.time()
        self.result_when = datetime(d.year(), d.month(), d.day(), t.hour(), t.minute())
        self.accept()

    def _clear(self):
        self.cleared = True
        self.result_when = None
        self.accept()


class InputDialog(FramelessDialog):
    """自绘的单行输入框，用于新建/重命名主题。"""

    def __init__(self, parent, title: str, label: str, text: str = "", placeholder: str = ""):
        super().__init__(parent, title)
        self.value = None
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(10)

        lab = QLabel(label, objectName="FieldLabel")
        lay.addWidget(lab)

        self.edit = QLineEdit()
        self.edit.setText(text)
        self.edit.selectAll()
        if placeholder:
            self.edit.setPlaceholderText(placeholder)
        self.edit.returnPressed.connect(self._ok)
        lay.addWidget(self.edit)

        row = QHBoxLayout()
        row.addStretch(1)
        b_cancel = QPushButton("取消", objectName="Plain")
        b_cancel.clicked.connect(self.reject)
        b_ok = QPushButton("确定", objectName="Primary")
        b_ok.setDefault(True)
        b_ok.clicked.connect(self._ok)
        row.addWidget(b_cancel)
        row.addWidget(b_ok)
        lay.addLayout(row)

        self.setMinimumWidth(420)
        self.adjustSize()
        self.add_edge()
        self.edit.setFocus()

    def _ok(self):
        text = self.edit.text().strip()
        if not text:
            return
        self.value = text
        self.accept()


class BackupDialog(FramelessDialog):
    """备份列表：立即备份、从备份恢复。"""

    def __init__(self, parent, lib):
        super().__init__(parent, "备份与恢复")
        from PyQt5.QtWidgets import QHBoxLayout, QListWidget

        self.lib = lib
        self.restored = False
        self.setFixedSize(574, 456)

        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(12)

        tip = QLabel(
            "每次启动最多每天自动备份一次，只保留最近 7 份。备份包含笔记正文与库结构，不含图片附件。",
            objectName="FieldLabel",
        )
        tip.setWordWrap(True)
        lay.addWidget(tip)

        header = QHBoxLayout()
        header.addWidget(QLabel("备份记录", objectName="FieldHeading"))
        header.addStretch(1)
        b_now = _button("立即备份", "Plain")
        b_now.clicked.connect(self._make_now)
        header.addWidget(b_now)
        lay.addLayout(header)

        self.list = QListWidget()
        self.list.setObjectName("BackupList")
        self.list.setMouseTracking(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(self.list, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        b_open = _button("打开备份目录", "Link")
        b_open.clicked.connect(self._open_dir)
        row.addWidget(b_open)
        row.addStretch(1)
        b_close = _button("关闭", "Plain")
        b_close.clicked.connect(self.accept)
        row.addWidget(b_close)
        self.b_restore = _button("恢复所选", "Primary")
        self.b_restore.clicked.connect(self._restore)
        row.addWidget(self.b_restore)
        lay.addLayout(row)

        self.status = QLabel(objectName="FieldLabel")
        lay.addWidget(self.status)
        self._reload()

    def _reload(self):
        from PyQt5.QtWidgets import QListWidgetItem

        from .. import backup

        self.list.clear()
        items = backup.list_all(self.lib.root)
        for it in items:
            size = it["size"] / 1024
            label = it["name"]
            for pre in ("hanlin-backup-", "timo-backup-"):
                label = label.replace(pre, "")
            label = label.replace(".zip", "")
            text = f"{label}    ·    {it['mtime']:%m-%d %H:%M}    ·    {size:.0f} KB"
            row = QListWidgetItem(text)
            row.setData(0x0100, str(it["path"]))
            self.list.addItem(row)
        if items:
            self.list.setCurrentRow(0)
        self.b_restore.setEnabled(bool(items))
        self.status.setText(f"共 {len(items)} 份备份。" if items else "还没有备份。")

    def _make_now(self):
        from .. import backup

        try:
            p = backup.create(self.lib)
        except Exception as e:
            self.status.setText(f"备份失败：{e}")
            return
        self._reload()
        self.status.setText(f"已备份到 {p.name}")

    def _restore(self):
        from .. import backup

        item = self.list.currentItem()
        if item is None:
            return
        path = item.data(0x0100)
        if not confirm(self, "恢复备份", "当前库会被这份备份覆盖（覆盖前会先备份一次现状）。\n确定继续吗？", "恢复"):
            return
        try:
            backup.restore(self.lib, path)
        except Exception as e:
            error(self, "恢复失败", str(e))
            return
        self.restored = True
        self.accept()

    def _open_dir(self):
        from .. import backup
        import os

        d = backup.backup_dir(self.lib.root)
        try:
            os.startfile(str(d))
        except Exception:
            pass


class SettingsDialog(FramelessDialog):
    """系列设置。字段留在对话框内，保存时一起写入。"""

    def __init__(self, parent, settings, theme_mode: str, sort_mode: str, section_index=0):
        super().__init__(parent, "设置")
        from PyQt5.QtCore import QSize
        from PyQt5.QtWidgets import (
            QCheckBox,
            QComboBox,
            QScrollArea,
            QSpinBox,
            QTabWidget,
            QWidget,
        )

        self.setWindowTitle("寒淋笔记 · 设置")
        self.setWindowIcon(app_icon())
        self._bar.setObjectName("SeriesSettingsTitleBar")
        self._bar.setFixedHeight(46)
        icon = QLabel()
        icon.setPixmap(app_icon().pixmap(QSize(26, 26)))
        self._bar.layout().insertWidget(0, icon)
        self.settings = settings
        self.theme_changed = False
        self._orig_theme = theme_mode or "light"
        work_area = parent.screen().availableGeometry() if parent else self.screen().availableGeometry()
        self.setFixedSize(min(600, work_area.width() - 24), min(454, work_area.height() - 24))

        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(20, 12, 20, 14)
        lay.setSpacing(12)
        tabs = QTabWidget(objectName="SeriesSettingsTabs")
        tabs.tabBar().setExpanding(False)
        tabs.setAccessibleName("设置分类")
        lay.addWidget(tabs, 1)

        def section(title: str):
            scroll = QScrollArea(objectName="SeriesSettingsScroll")
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setWidgetResizable(True)
            page = QWidget(objectName="SeriesSettingsPage")
            fields = QVBoxLayout(page)
            fields.setContentsMargins(0, 16, 4, 8)
            fields.setSpacing(8)
            scroll.setWidget(page)
            tabs.addTab(scroll, title)
            return fields

        def field(layout, label: str, widget) -> None:
            caption = QLabel(label, objectName="FieldLabel")
            caption.setBuddy(widget)
            widget.setAccessibleName(label)
            layout.addWidget(caption)
            layout.addWidget(widget)

        general = section("常规")
        appearance = section("外观")
        data = section("数据")
        about = section("关于")

        self.cmb_theme = QComboBox()
        for key, text in (("light", "浅色"), ("dark", "深色"), ("system", "跟随系统")):
            self.cmb_theme.addItem(text, key)
        idx = max(0, self.cmb_theme.findData(theme_mode or "light"))
        self.cmb_theme.setCurrentIndex(idx)
        field(appearance, "颜色主题", self.cmb_theme)

        from .theme import available_text_fonts

        self.cmb_font = QComboBox()
        for label, family in available_text_fonts():
            self.cmb_font.addItem(label, family)
        self.cmb_font.setCurrentIndex(
            max(0, self.cmb_font.findData(settings.get("font_family", "宋体")))
        )
        field(appearance, "正文字体", self.cmb_font)
        appearance.addStretch(1)

        self.cmb_sort = QComboBox()
        for key, text in (("updated", "最近更新"), ("created", "最近创建"), ("title", "标题")):
            self.cmb_sort.addItem(text, key)
        self.cmb_sort.setCurrentIndex(max(0, self.cmb_sort.findData(sort_mode or "updated")))
        field(general, "默认排序", self.cmb_sort)
        general.addStretch(1)

        path = QLineEdit(str(settings.root.resolve()))
        path.setReadOnly(True)
        field(data, "数据文件夹", path)
        if parent is not None:
            data_actions = QHBoxLayout()
            open_data = _button("打开数据文件夹")
            open_data.clicked.connect(parent.open_data_dir)
            data_actions.addWidget(open_data)
            backups = _button("本地备份…")
            backups.clicked.connect(parent.backup_dialog)
            data_actions.addWidget(backups)
            data_actions.addStretch(1)
            data.addLayout(data_actions)

        sync_head = QHBoxLayout()
        sync_head.setSpacing(8)
        sync_head.addWidget(QLabel("云同步", objectName="FieldHeading"))
        sync_head.addStretch(1)
        if parent is not None:
            sync_open = _button("云同步…")
            sync_open.clicked.connect(parent.open_sync_dialog)
            sync_head.addWidget(sync_open)
        data.addLayout(sync_head)

        self.chk_auto_sync = QCheckBox("每 10 分钟自动云同步")
        self.chk_auto_sync.setChecked(bool((settings.get("sync") or {}).get("autoSync", True)))
        data.addWidget(self.chk_auto_sync)
        sync_hint = QLabel("配对后生效。关闭后仍可手动同步。", objectName="FieldLabel")
        sync_hint.setWordWrap(True)
        data.addWidget(sync_hint)

        self.spin_trash = QSpinBox()
        self.spin_trash.setRange(0, 3650)
        self.spin_trash.setValue(int(settings.get("trash_days", 0) or 0))
        self.spin_trash.setSuffix(" 天")
        self.spin_trash.setSpecialValueText("不自动清理")
        field(data, "回收站清理周期", self.spin_trash)

        hint = QLabel(
            "设定天数后，程序启动时会永久删除超过该期限的回收站笔记。",
            objectName="FieldLabel",
        )
        hint.setWordWrap(True)
        data.addWidget(hint)
        data.addStretch(1)

        from .. import __version__

        about.addWidget(QLabel("寒淋笔记", objectName="AboutTitle"))
        about.addWidget(QLabel(f"版本 {__version__}", objectName="FieldLabel"))
        if parent is not None:
            details = _button("快捷键与库统计…")
            details.clicked.connect(parent.show_about)
            about.addWidget(details, alignment=Qt.AlignLeft)
        about.addStretch(1)

        self.message = QLabel("", objectName="SeriesSettingsError")
        self.message.setWordWrap(True)
        self.message.hide()
        lay.addWidget(self.message)
        btns = QHBoxLayout()
        btns.setSpacing(8)
        btns.addStretch(1)
        cancel = _button("取消")
        cancel.setMinimumWidth(84)
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        done = _button("保存", "Primary")
        done.setMinimumWidth(84)
        done.setDefault(True)
        done.clicked.connect(self.accept)
        btns.addWidget(done)
        lay.addLayout(btns)

        self.cmb_theme.currentIndexChanged.connect(self._on_theme)
        tabs.setCurrentIndex(section_index)
        tabs.tabBar().setFocus()
        self.add_edge()

    def _on_theme(self, _idx: int) -> None:
        self.theme_changed = self.cmb_theme.currentData() != self._orig_theme

    def apply(self) -> None:
        sync = dict(self.settings.get("sync") or {})
        sync["autoSync"] = self.chk_auto_sync.isChecked()
        self.settings.update({
            "theme_mode": self.cmb_theme.currentData(),
            "font_family": self.cmb_font.currentData(),
            "trash_days": int(self.spin_trash.value()),
            "sort_mode": self.cmb_sort.currentData(),
            "sync": sync,
        })

    def accept(self) -> None:
        try:
            self.apply()
        except OSError as exc:
            self.message.setText(f"无法保存设置。请检查数据文件夹权限。\n{exc}")
            self.message.show()
            return
        super().accept()


class ImportModeDialog(FramelessDialog):
    """已有数据时选择：合并（只加新的）或替换（清空后导入）。"""

    def __init__(self, parent, note_count: int):
        super().__init__(parent, "导入笔记包")
        from PyQt5.QtWidgets import QHBoxLayout

        self.mode: str | None = None
        self.setFixedSize(604, 320)

        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(14)

        lay.addWidget(QLabel(f"当前库有 {note_count} 条笔记", objectName="ImportSummary"))
        options = QHBoxLayout()
        options.setSpacing(12)
        for mode, title, description, style in (
            ("merge", "合并导入", "保留当前笔记，加入导出包中的新内容。", "Primary"),
            ("replace", "替换导入", "备份当前库后，用导出包中的内容整体替换。", "Danger"),
        ):
            card = QFrame(objectName="SettingsGroup")
            content = QVBoxLayout(card)
            content.setContentsMargins(15, 13, 15, 13)
            content.setSpacing(8)
            content.addWidget(QLabel(title, objectName="FieldHeading"))
            tip = QLabel(description, objectName="FieldLabel")
            tip.setWordWrap(True)
            content.addWidget(tip)
            content.addStretch(1)
            action = _button("合并" if mode == "merge" else "替换", style)
            action.clicked.connect(lambda _=False, m=mode: self._pick(m))
            content.addWidget(action, alignment=Qt.AlignRight)
            options.addWidget(card, 1)
        lay.addLayout(options, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = _button("取消", "Link")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        lay.addLayout(row)

    def _pick(self, mode: str):
        self.mode = mode
        self.accept()


class AboutDialog(FramelessDialog):
    """关于：版本、数据统计、快捷键。"""

    SHORTCUTS = [
        ("Ctrl+N", "新建笔记"),
        ("Ctrl+Shift+N", "新建主题"),
        ("Ctrl+F", "搜索全部笔记（光标在正文里则在本篇内查找）"),
        ("Ctrl+,", "打开设置"),
        ("Ctrl+S", "立即保存"),
        ("Ctrl+E", "在分屏和阅读间切换"),
        ("Alt+←", "从文档返回笔记库"),
        ("Ctrl+B / I / K", "粗体、斜体、插入链接"),
        ("Ctrl+Shift+T", "待办清单"),
        ("Esc", "关闭查找条"),
        ("双击标题栏", "最大化 / 还原"),
    ]

    def __init__(self, parent, lib, version: str):
        super().__init__(parent, "关于")
        from PyQt5.QtWidgets import QHBoxLayout

        self.setFixedSize(520, 540)

        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(32, 24, 32, 22)
        lay.setSpacing(10)

        title = QLabel("寒淋笔记", objectName="AboutTitle")
        lay.addWidget(title)

        sub = QLabel(f"版本 {version} · Windows 本地笔记，数据只在自己电脑上", objectName="FieldLabel")
        lay.addWidget(sub)

        notes = [n for n in lib.notes.values() if n.state != "trash"]
        topics = [t for t in lib.topics.values() if t.state != "trash"]
        chars = sum(len(lib.content(n.id)) for n in notes)
        att = len([p for p in lib.attachments_dir.glob("*") if p.is_file()]) if lib.attachments_dir.exists() else 0

        stat = QLabel(
            f"{len(topics)} 个主题 · {len(notes)} 条笔记 · {att} 个附件 · 约 {chars:,} 字",
            objectName="LibraryStats",
        )
        lay.addWidget(stat)

        lab = QLabel("快捷键", objectName="SideSection")
        lay.addWidget(lab)
        for key, desc in self.SHORTCUTS:
            row = QHBoxLayout()
            row.setSpacing(10)
            k = QLabel(key, objectName="KeyCap")
            k.setFixedWidth(120)
            row.addWidget(k)
            d = QLabel(desc, objectName="FieldLabel")
            row.addWidget(d, 1)
            lay.addLayout(row)

        lay.addStretch(1)
        bottom = QHBoxLayout()
        path = QLabel(f"数据目录：{Path(lib.root).resolve()}", objectName="FieldLabel")
        path.setToolTip(str(lib.root))
        path.setMinimumWidth(10)
        bottom.addWidget(path, 1)
        btn = _button("好", "Primary")
        btn.clicked.connect(self.accept)
        bottom.addWidget(btn)
        lay.addLayout(bottom)


class TrashTopicsDialog(FramelessDialog):
    """已删除的主题：恢复（连同当初一起进回收站的笔记）或彻底删除。"""

    def __init__(self, parent, lib):
        super().__init__(parent, "已删除的主题")
        from PyQt5.QtWidgets import QListWidget

        self.lib = lib
        self.changed = False
        self.setFixedSize(500, 430)

        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(12)

        tip = QLabel(
            "删掉的主题会先放在这里。恢复时，当初跟着它一起进回收站的笔记也会回到这个主题。",
            objectName="FieldLabel",
        )
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self.empty = QLabel("没有已删除的主题。", objectName="EmptyHint")
        lay.addWidget(self.empty)

        self.list = QListWidget()
        self.list.setObjectName("TrashTopicList")
        self.list.setMouseTracking(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.itemDoubleClicked.connect(lambda _=None: self._restore())
        lay.addWidget(self.list, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_restore = QPushButton("恢复所选")
        self.btn_restore.setObjectName("Primary")
        self.btn_purge = QPushButton("彻底删除")
        self.btn_purge.setObjectName("Danger")
        btn_close = QPushButton("关闭")
        btn_close.setObjectName("Link")
        for b in (self.btn_restore, self.btn_purge):
            b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.btn_restore.clicked.connect(self._restore)
        self.btn_purge.clicked.connect(self._purge)
        btn_close.clicked.connect(self.reject)
        row.addWidget(self.btn_restore)
        row.addWidget(self.btn_purge)
        row.addStretch(1)
        row.addWidget(btn_close)
        lay.addLayout(row)

        self._reload()

    def _reload(self) -> None:
        from PyQt5.QtWidgets import QListWidgetItem

        self.list.clear()
        topics = self.lib.trashed_topics()
        for t in topics:
            cnt = self.lib.topic_note_count(t.id)
            when = (t.deleted_at or "")[:10]
            item = QListWidgetItem(f"{t.name}      {cnt} 条笔记      删除于 {when}")
            item.setData(Qt.UserRole, t.id)
            self.list.addItem(item)
        if topics:
            self.list.setCurrentRow(0)
        self.list.setVisible(bool(topics))
        self.empty.setVisible(not topics)
        self.btn_restore.setEnabled(bool(topics))
        self.btn_purge.setEnabled(bool(topics))

    def _restore(self) -> None:
        item = self.list.currentItem()
        if not item:
            return
        n = self.lib.restore_topic(item.data(Qt.UserRole))
        self.changed = True
        self._reload()
        info(self, "已恢复", f"主题已恢复，带回 {n} 条笔记。" if n else "主题已恢复。")

    def _purge(self) -> None:
        item = self.list.currentItem()
        if not item:
            return
        tid = item.data(Qt.UserRole)
        t = self.lib.topics.get(tid)
        if not t:
            return
        cnt = self.lib.topic_note_count(tid)
        if not confirm(
            self,
            "彻底删除主题",
            f"「{t.name}」及其中 {cnt} 条笔记会被永久删除，无法恢复。",
            ok_text="彻底删除",
            danger=True,
        ):
            return
        n = self.lib.purge_topic(tid)
        self.changed = True
        self._reload()
        info(self, "已删除", f"主题与 {n} 条笔记已永久删除。")


class DuplicatesDialog(FramelessDialog):
    """列出正文重复的笔记，勾选要移到回收站的条目。"""

    def __init__(self, parent, lib, groups) -> None:
        super().__init__(parent, "查找重复笔记")
        self.lib = lib
        self.groups = groups
        self._to_delete: list[str] = []
        self.setFixedSize(640, 500)

        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(10)

        head = QLabel("下面的笔记正文完全相同，勾选的会被移到回收站。")
        head.setObjectName("FieldLabel")
        head.setWordWrap(True)
        lay.addWidget(head)

        self.tree = QTreeWidget()
        self.tree.setMouseTracking(True)
        self.tree.setObjectName("DupeTree")
        self.tree.setHeaderLabels(["笔记", "主题", "时间"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        lay.addWidget(self.tree, 1)

        self.count_label = QLabel("")
        self.count_label.setObjectName("FieldLabel")
        lay.addWidget(self.count_label)

        row = QHBoxLayout()
        row.setSpacing(8)
        btn_all = _button("全选重复项", "Plain")
        btn_all.clicked.connect(self._check_all)
        btn_none = _button("全不选", "Plain")
        btn_none.clicked.connect(self._uncheck_all)
        row.addWidget(btn_all)
        row.addWidget(btn_none)
        row.addStretch(1)
        cancel = _button("取消", "Link")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.btn_ok = _button("移到回收站", "Danger")
        self.btn_ok.clicked.connect(self._accept)
        row.addWidget(self.btn_ok)
        lay.addLayout(row)

        self._fill()
        self.tree.itemChanged.connect(self._on_item_changed)
        self._refresh_count()
        self.add_edge()

    # --- 构建 ---------------------------------------------------------
    def _fill(self) -> None:
        for group in self.groups:
            first = group[0]
            title = first.display_title(self.lib.content(first.id))
            top = QTreeWidgetItem([f"{len(group)} 条重复 · {title}", "", ""])
            top.setFlags(Qt.ItemIsEnabled)
            self.tree.addTopLevelItem(top)
            for i, note in enumerate(group):
                topic = self.lib.topics.get(note.topic_id)
                child = QTreeWidgetItem(
                    [
                        note.display_title(self.lib.content(note.id)),
                        topic.name if topic else "（未分类）",
                        (note.created_at or "")[:10],
                    ]
                )
                child.setData(0, Qt.UserRole, note.id)
                child.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
                # 默认保留最早的一条，其余勾上
                child.setCheckState(0, Qt.Unchecked if i == 0 else Qt.Checked)
                top.addChild(child)
            top.setExpanded(True)

    # --- 交互 ---------------------------------------------------------
    def _iter_items(self):
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                yield top.child(j)

    def _on_item_changed(self, _item, _col) -> None:
        self._refresh_count()

    def _check_all(self) -> None:
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(1, top.childCount()):
                top.child(j).setCheckState(0, Qt.Checked)
        self.tree.blockSignals(False)
        self._refresh_count()

    def _uncheck_all(self) -> None:
        self.tree.blockSignals(True)
        for item in self._iter_items():
            item.setCheckState(0, Qt.Unchecked)
        self.tree.blockSignals(False)
        self._refresh_count()

    def _refresh_count(self) -> None:
        n = sum(1 for item in self._iter_items() if item.checkState(0) == Qt.Checked)
        self.btn_ok.setText(f"移到回收站（{n} 条）" if n else "移到回收站")
        self.btn_ok.setEnabled(n > 0)
        if not n:
            self.count_label.setText("没有勾选任何笔记")
        else:
            # 每组至少要留一条
            warn = False
            for i in range(self.tree.topLevelItemCount()):
                top = self.tree.topLevelItem(i)
                if top.childCount() and all(
                    top.child(j).checkState(0) == Qt.Checked for j in range(top.childCount())
                ):
                    warn = True
                    break
            self.count_label.setText(
                "提示：有一组的笔记被全部勾选，删除后这组内容只存在于回收站里。"
                if warn
                else f"已勾选 {n} 条"
            )

    def _accept(self) -> None:
        self._to_delete = [
            item.data(0, Qt.UserRole) for item in self._iter_items() if item.checkState(0) == Qt.Checked
        ]
        if not self._to_delete:
            return
        self.accept()

    def to_delete(self) -> list[str]:
        return list(self._to_delete)

class HistoryDialog(FramelessDialog):
    """一条笔记的历史版本：左边挑时间，右边看内容，可以恢复到某一版。"""

    def __init__(self, parent, lib, note_id: str):
        super().__init__(parent, "历史版本")
        from ..render import render

        self.lib = lib
        self.note_id = note_id
        self.restored = False
        self._render = render
        note = lib.notes.get(note_id)
        title = note.display_title(lib.content(note_id)) if note else "笔记"

        self.setFixedSize(780, 540)
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(26, 20, 26, 20)
        lay.setSpacing(10)

        tip = QLabel(
            f"「{title}」改动过的版本都在这里，越靠上越新。"
            "恢复会把笔记换成选中的那一版，恢复后还能按 Ctrl+Z 撤回。",
            objectName="FieldLabel",
        )
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self.empty = QLabel("这条笔记还没有历史版本（改过正文之后才会留档）。", objectName="EmptyHint")
        lay.addWidget(self.empty)

        body = QHBoxLayout()
        body.setSpacing(12)
        self.list = QListWidget(objectName="HistoryList")
        self.list.setMouseTracking(True)
        self.list.setFixedWidth(228)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.currentRowChanged.connect(self._show_version)
        body.addWidget(self.list)
        self.preview = QTextBrowser(objectName="HistoryPreview")
        from .theme import READING_SIZE, reading_font

        self.preview.setFont(reading_font(self.preview.font(), READING_SIZE))
        self.preview.setOpenLinks(False)
        body.addWidget(self.preview, 1)
        lay.addLayout(body, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_restore = QPushButton("恢复这一版")
        self.btn_restore.setObjectName("Primary")
        self.btn_restore.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.btn_restore.clicked.connect(self._restore)
        btn_close = QPushButton("关闭")
        btn_close.setObjectName("Link")
        btn_close.clicked.connect(self.reject)
        row.addWidget(self.btn_restore)
        row.addStretch(1)
        row.addWidget(btn_close)
        lay.addLayout(row)

        self._fill()
        self.add_edge()

    def _fill(self) -> None:
        from datetime import datetime

        from ..store import parse_iso

        self.versions = self.lib.history.versions(self.note_id)
        self.list.clear()
        for v in self.versions:
            ts = parse_iso(v.at)
            when = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S") if ts else v.at[:16]
            label = f"{when}  ·  {v.chars} 字"
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, v.stamp)
            if v.title:
                it.setToolTip(v.title)
            self.list.addItem(it)
        has = bool(self.versions)
        self.list.setVisible(has)
        self.preview.setVisible(has)
        self.empty.setVisible(not has)
        self.btn_restore.setEnabled(has)
        if has:
            self.list.setCurrentRow(0)
            self._show_version(0)

    def _show_version(self, row: int) -> None:
        if row < 0 or row >= len(getattr(self, "versions", [])):
            self.preview.setHtml("")
            return
        from datetime import datetime

        from ..store import parse_iso

        data = self.lib.history.read(self.note_id, self.versions[row].stamp) or {}
        ts = parse_iso(str(data.get("at") or ""))
        when = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else str(data.get("at") or "")[:19]
        head = f"<p style='color:#86868b;font-size:12px'>{when}</p>"
        self.preview.setHtml(head + self._render(str(data.get("content") or ""), self.lib.root, "（这一版是空的）"))

    def _restore(self) -> None:
        row = self.list.currentRow()
        if row < 0 or row >= len(self.versions):
            return
        data = self.lib.history.read(self.note_id, self.versions[row].stamp)
        if not data:
            return
        self.restore_data = data
        self.restored = True
        self.accept()

    def version_data(self) -> dict | None:
        return getattr(self, "restore_data", None)

