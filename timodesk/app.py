"""寒淋笔记（HanLin-Notes）启动入口。"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path

from PyQt5.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from .importer import import_export
from .instance import LibraryInstance
from .render import warm_up as _warm_up_render
from .settings import Settings
from .store import Library
from .ui.main_window import MainWindow
from .ui.theme import apply_theme
from .ui.hover_tip import HoverTips
from .ui.title_bar import BrandPanel, FramelessDialog, app_icon
from .ui.dialogs import ProgressDialog, error, info
from .ui.tray import TrayIcon

from . import __version__

APP_NAME = "寒淋笔记"
APP_VERSION = __version__


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """便携优先：程序目录/Data；不可写时退到用户目录。"""
    env = os.environ.get("HANLIN_DATA_DIR") or os.environ.get("TIMO_DATA_DIR")
    desktop_data = base_dir() / "Data"
    d = desktop_data if (desktop_data / ".hanlin-portable").is_file() or not env else Path(env)
    from .sync import local_restore_pending

    if local_restore_pending(d):
        return d
    try:
        d.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=d):
            pass
        return d
    except Exception:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        alt = base / "HanLinNotes"
        legacy = base / "TimoDesktop"
        if not alt.exists() and legacy.exists():
            try:
                legacy.rename(alt)      # 旧版本的用户目录，搬过来
            except OSError:
                pass
        alt.mkdir(parents=True, exist_ok=True)
        return alt


def _install_excepthook(root: Path) -> None:
    """windowed 模式没有控制台，未捕获异常写进 Data/error.log。"""
    log = root / "error.log"

    state = {"shown": False}

    def hook(tp, val, tb):
        import os as _os
        import traceback
        from datetime import datetime

        text = "".join(traceback.format_exception(tp, val, tb))
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}]\n{text}")
        except Exception:  # noqa: BLE001
            pass
        # windowed 模式用户看不到任何东西，至少给个提示（防递归：只弹一次）
        if not state["shown"] and not _os.environ.get("TIMO_NO_DIALOG"):
            state["shown"] = True
            try:
                from PyQt5.QtWidgets import QApplication

                app = QApplication.instance()
                if app is not None:
                    from .ui.dialogs import error

                    error(
                        app.activeWindow(),
                        "出了点问题",
                        f"操作没有完成：{val}\n\n详细信息已记录在：\n{log}",
                    )
            except Exception:  # noqa: BLE001
                pass
        sys.__excepthook__(tp, val, tb)

    sys.excepthook = hook


def _schedule_delete(paths: list[str]) -> None:
    """安装器把删除自己的活儿交给主程序：等安装包退出后再删掉它。"""
    if not paths:
        return
    import threading
    import time

    def work(p: Path) -> None:
        for _ in range(60):
            try:
                if not p.exists():
                    return
                p.unlink()
                return
            except OSError:
                time.sleep(0.5)

    for item in paths:
        threading.Thread(target=work, args=(Path(item),), daemon=True).start()


class WelcomeDialog(FramelessDialog):
    """首次启动：导入笔记包，或从空库开始。"""

    def __init__(self, parent=None):
        super().__init__(parent, "欢迎使用寒淋笔记")
        self.choice = "cancel"
        self.path: str | None = None
        self.setWindowTitle("欢迎使用寒淋笔记")
        self.setFixedSize(660, 398)
        split = QHBoxLayout(self.body)
        split.setContentsMargins(1, 0, 1, 1)
        split.setSpacing(0)
        split.addWidget(BrandPanel())
        lay = QVBoxLayout()
        lay.setContentsMargins(30, 24, 30, 23)
        lay.setSpacing(10)
        split.addLayout(lay, 1)
        lay.addWidget(QLabel("打开笔记库", objectName="WelcomeTitle"))
        desc = QLabel(
            "创建一个本地库，或从已有的笔记包（ZIP / 解压后的文件夹）迁入主题、笔记和附件。",
            objectName="WelcomeDesc",
        )
        desc.setWordWrap(True)
        lay.addWidget(desc)
        lay.addStretch(1)

        btn_new = QPushButton("从空白开始", objectName="WelcomePrimary")
        btn_new.clicked.connect(self._new_empty)
        lay.addWidget(btn_new)

        row = QHBoxLayout()
        btn_import = QPushButton("导入笔记包 ZIP", objectName="WelcomePlain")
        btn_import.clicked.connect(self._pick_zip)
        row.addWidget(btn_import, 1)
        btn_folder = QPushButton("导入解压文件夹", objectName="WelcomePlain")
        btn_folder.clicked.connect(self._pick_folder)
        row.addWidget(btn_folder, 1)
        lay.addLayout(row)

        quit_row = QHBoxLayout()
        quit_row.addWidget(QLabel("数据保存在此电脑", objectName="WelcomeSub"))
        quit_row.addStretch(1)
        b = QPushButton("退出", objectName="Link")
        b.setFlat(True)
        b.clicked.connect(self.reject)
        quit_row.addWidget(b)
        lay.addLayout(quit_row)

        self.add_edge()

    def _pick_zip(self):
        p, _ = QFileDialog.getOpenFileName(self, "选择笔记包", "", "笔记包 (*.zip);;所有文件 (*)")
        if p:
            self.choice, self.path = "import", p
            self.accept()

    def _pick_folder(self):
        p = QFileDialog.getExistingDirectory(self, "选择包含 manifest.json 的文件夹")
        if p:
            self.choice, self.path = "import", p
            self.accept()

    def _new_empty(self):
        self.choice = "empty"
        self.accept()


def recover_library(lib, notify: bool = True) -> bool:
    """library.json 读坏了：先试着用最近一份备份覆盖，不行就提示用户。"""
    from .backup import list_all, restore
    from .ui.dialogs import error, info

    items = list_all(lib.root)
    if items:
        try:
            restore(lib, items[0]["path"])
            lib.load()
            lib.load_error = None
            if notify:
                info(None, "数据文件损坏", f"笔记索引读取失败，已用最近的备份（{items[0]['name']}）恢复。")
            return True
        except Exception:  # noqa: BLE001
            pass
    if notify:
        error(
            None,
            "数据文件损坏",
            "笔记索引（library.json）读取失败，坏掉的文件已留档为 library.json.broken-时间戳。\n\n"
            "可以先退出，从 Data/backup 里挑一份备份，用「数据与设置 → 备份与恢复」恢复。",
        )
    return False


def _import_with_progress(lib: Library, path: str) -> bool:
    pr = ProgressDialog(None, "导入笔记包", "正在导入，请稍候…")
    pr.show()
    QApplication.processEvents()

    def pg(done, total):
        pr.set_progress(done, total)
        QApplication.processEvents()

    try:
        report = import_export(path, lib, progress=pg)
        lib.save()
        pr.close()
        info(None, "导入完成", report.summary())
    except Exception as e:  # noqa: BLE001
        pr.close()
        error(None, "导入失败", str(e))
        return False
    return True


def resolve_theme_mode(mode: str) -> str:
    """light / dark / system → 实际生效的 light|dark。"""
    from .ui.theme import resolve

    return resolve(mode)


def run():
    if sys.platform == "win32":
        # 显式的应用标识：任务栏、通知和托盘按这个名字分组，不沿用别的程序留下的图标缓存
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HanLin.Notes.Desktop")
        except Exception:
            pass
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    hover_tips = HoverTips(app)
    app.installEventFilter(hover_tips)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName("")
    app.setOrganizationName("HanLin-Notes")
    app.setFont(QFont("Microsoft YaHei UI", 9))
    root = data_dir()
    _install_excepthook(root)
    # 字体要在生成 QSS 之前定下来，否则样式表里写的是旧字体
    from .ui.theme import set_text_font

    apply_theme(app, "light")
    app.setWindowIcon(app_icon())

    args = list(sys.argv[1:])
    delete_targets: list[str] = []
    if "--delete" in args:
        i = args.index("--delete")
        if i + 1 < len(args):
            delete_targets.append(args[i + 1])
            del args[i : i + 2]
    # 尽早开始，避免被欢迎对话框等模态窗口挡住
    _schedule_delete(delete_targets)

    cli = next((str(Path(a).resolve()) for a in args if a and Path(a).exists()), "")
    instance = LibraryInstance(root, app)
    ownership = instance.start(cli)
    if ownership != "primary":
        if ownership == "busy":
            error(None, "无法打开笔记库", "笔记库正由另一个实例使用，暂时无法唤出窗口。请稍后重试。")
        return 0 if ownership == "forwarded" else 1
    try:
        from .sync import local_restore_pending, recover_local_restore

        if local_restore_pending(root):
            try:
                recover_local_restore(root)
            except Exception as exc:  # Leave an interrupted library available for recovery.
                error(None, "笔记恢复尚未完成", str(exc))
                return 1
        # Keep secondary launches lightweight, and let Python finish a pending
        # Markdown initialization before unloading the packaged interpreter.
        threading.Thread(target=_warm_up_render, name="HanLinMarkdownWarmup").start()
        settings = Settings(root)
        set_text_font(settings.get("font_family", "宋体"))
        apply_theme(app, resolve_theme_mode(settings.get("theme_mode", "light")))
        return _run_primary(app, root, settings, cli, instance)
    finally:
        instance.close()


def _run_primary(app, root, settings, cli, instance):
    lib = Library(root).load()
    if lib.load_error:
        recover_library(lib)
    elif lib.bad_files:
        info(
            None,
            "部分笔记读取异常",
            f"有 {len(lib.bad_files)} 个正文文件打不开（可能被删除或用别的程序存成了其他编码），"
            "已尽量读出能读的部分。需要的话可以在「数据与设置 → 备份与恢复」里挑一份备份恢复。",
        )

    if cli:
        if not _import_with_progress(lib, cli):
            return 1
    elif not lib.notes and not lib.topics:
        dlg = WelcomeDialog()
        if dlg.exec_() != QDialog.Accepted:
            return 0
        if dlg.choice == "import" and dlg.path:
            if not _import_with_progress(lib, dlg.path):
                return 1

    try:
        from .backup import create as _make_backup, due as _backup_due

        if lib.notes and _backup_due(lib):
            _make_backup(lib)
    except Exception:
        pass

    # 默认不自动清理回收站：删数据的事交给用户自己点「清空回收站」。
    # 想开的话在 settings.json 里写 "trash_days": 30 之类。
    purged = 0
    try:
        purged = lib.purge_trash(int(settings.get("trash_days", 0) or 0))
    except Exception:
        purged = 0

    win = MainWindow(lib)
    win.settings = settings
    tray = TrayIcon(win)
    win.tray = tray
    tray.show()
    app.setQuitOnLastWindowClosed(False)
    win.show()

    def activate_existing(path):
        tray.show_window()
        if path and Path(path).exists():
            def import_when_ready():
                if app.activeModalWidget() is not None or not win.isEnabled():
                    QTimer.singleShot(200, import_when_ready)
                elif not win._quitting:
                    win._do_import(path)
            QTimer.singleShot(0, import_when_ready)

    instance.set_handler(activate_existing)
    if (root / ".hanlin-portable").is_file():
        request = root / ".hanlin-exit"

        def exit_for_update():
            if app.activeModalWidget() is not None or win.autosync.running():
                return
            try:
                request.unlink()
            except OSError:
                return
            tray.quit_app()

        update_timer = QTimer(app)
        update_timer.timeout.connect(exit_for_update)
        update_timer.start(400)
    if purged:
        QTimer.singleShot(1500, lambda: win._toast(f"回收站里 {purged} 条超过 30 天的笔记已清理"))
    result = app.exec_()
    tray.detach()
    win.tray = None
    tray.deleteLater()
    win.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    return result


if __name__ == "__main__":
    sys.exit(run())
