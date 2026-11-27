"""定时同步，只在确认本地未改动时应用云端快照。"""
from __future__ import annotations

import logging
import math
import os
import socket
import tempfile
import time
from pathlib import Path

from PyQt5.QtCore import QObject, QThread, QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import QApplication

log = logging.getLogger("timodesk.sync")

_TRACE_ENV = "HANLIN_SYNC_DEBUG"


def trace_path() -> Path:
    return Path(tempfile.gettempdir()) / "hanlin-notes-sync.log"


def _trace(message: str) -> None:
    """仅在设置 HANLIN_SYNC_DEBUG 时把自动同步的决策写进临时日志，便于现场排障。"""
    if not os.environ.get(_TRACE_ENV):
        return
    try:
        with trace_path().open("a", encoding="utf-8") as handle:
            handle.write(f"{time_stamp()} {message}\n")
    except OSError:
        pass


def time_stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")

FIRST_CHECK_MS = 20_000
CHECK_INTERVAL_MS = 10 * 60 * 1000


def status_text(result) -> str:
    state = (result or {}).get("state")
    labels = {
        "pushed": "已同步到云端。",
        "pulled": "已同步云端更新，原内容已备份。",
        "same": "本地和云端已同步。",
        "diverged": "本地和云端都有改动，两份内容都已保留。",
        "unpaired": "配对后会自动同步。",
    }
    count = (result or {}).get("deduplicated", 0)
    if count:
        return f"已同步，{count} 条重复笔记已移入回收站。"
    return labels.get(state, "暂无法确定同步状态，两端内容保持原样。")


class _SyncThread(QThread):
    finished_ok = pyqtSignal(object)
    finished_error = pyqtSignal(object)
    ready_to_apply = pyqtSignal(object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):  # noqa: D401
        try:
            result = self._fn()
        except BaseException as exc:  # noqa: BLE001
            self.finished_error.emit(exc)
            return
        self.finished_ok.emit(result)


class AutoSync(QObject):
    """挂在主窗口上的后台同步器。"""

    def __init__(self, window, settings, lib):
        super().__init__(window)
        self.window = window
        self.settings = settings
        self.lib = lib
        self._thread = None
        self.last_status = ""
        self._restore_guard = False
        self._failures = 0
        self._paused = False
        self._next_check = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.check)

    # ---------------- 配置 ----------------
    def _settings(self):
        # 主窗口的 settings 实例可能在 app.run() 里被替换，始终以窗口当前实例为准。
        return getattr(self.window, "settings", None) or self.settings

    def config(self) -> dict:
        return dict(self._settings().get("sync") or {})

    def enabled(self) -> bool:
        cfg = self.config()
        if not cfg.get("enabled"):
            return False
        return bool(cfg.get("autoSync", True))

    # ---------------- 生命周期 ----------------
    def start(self) -> None:
        if self.enabled() and not self._paused:
            now = time.monotonic()
            if self._next_check is None:
                self._next_check = now + FIRST_CHECK_MS / 1000
            remaining = math.ceil((self._next_check - now) * 1000)
            self._timer.start(max(0, remaining))

    def stop(self) -> None:
        self._timer.stop()

    def running(self) -> bool:
        return self._thread is not None

    def pause(self) -> None:
        self._paused = True
        self.stop()

    def resume(self) -> None:
        self._paused = False
        self.restart()

    def restart(self) -> None:
        self.stop()
        self.start()

    def note_settings_changed(self) -> None:
        """设置页保存后调用：重新读取间隔，必要时启停。"""
        self.restart()

    # ---------------- 检查 ----------------
    def check(self) -> None:
        if self._paused:
            return
        _trace("check: begin")
        if not self.enabled():
            _trace("check: disabled")
            self._timer.stop()
            return
        if self._next_check is not None and time.monotonic() < self._next_check:
            # Coarse timers may fire a little early.
            self.start()
            return
        self._next_check = time.monotonic() + CHECK_INTERVAL_MS / 1000
        self.start()
        if self._thread is not None:
            _trace("check: thread busy")
            return
        cfg = self.config()
        window = self.window
        if getattr(window, "_quitting", False):
            _trace("check: quitting")
            return
        try:
            if not window._flush_save():
                return
        except Exception as exc:  # noqa: BLE001 - 保存失败时跳过本轮
            _trace(f"check: flush failed {exc!r}")
            return
        from .sync import SyncClient, SyncConfig

        config = SyncConfig(
            server_url=str(cfg.get("serverUrl") or "").rstrip("/"),
            device_name=str(cfg.get("deviceName") or socket.gethostname()),
        )
        root = self.lib.root

        def before_apply():
            decision = {}
            thread.ready_to_apply.emit(decision)
            if not decision.get("allow"):
                from .sync import BusyError
                raise BusyError("笔记正在编辑或使用设置，稍后会重试。")

        def work():
            client = SyncClient(config, data_dir=root)
            return client.sync_once(before_apply=before_apply)

        thread = _SyncThread(work, self)
        thread.ready_to_apply.connect(self._prepare_restore, Qt.BlockingQueuedConnection)
        thread.finished_ok.connect(self._done)
        thread.finished_error.connect(self._failed)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        thread.start()

    def _prepare_restore(self, decision):
        window = self.window
        if self._paused or window._quitting or QApplication.activeModalWidget() is not None:
            return
        if not window.save_now():
            return
        window.setEnabled(False)
        self._restore_guard = True
        decision["allow"] = True

    def _release_restore(self, reload_library=False):
        if self._restore_guard:
            try:
                if reload_library:
                    self.window._reload_library()
            finally:
                self._restore_guard = False
                self.window.setEnabled(True)

    def _done(self, result) -> None:
        _trace(f"done: {result}")
        self._thread = None
        self._failures = 0
        state = (result or {}).get("state")
        self._release_restore(state == "pulled")
        message = status_text(result)
        if message != self.last_status and state in ("pushed", "diverged"):
            self._toast_once(message)
        self.last_status = message

    def _failed(self, exc) -> None:
        self._release_restore()
        self._thread = None
        self._failures += 1
        self.last_status = "同步稍后重试，本地内容已保留。"
        _trace(f"failed: {type(exc).__name__}")
        log.warning("auto-sync failed (%s)", type(exc).__name__)
        if self._failures in (3, 6, 12):
            self._toast_once(f"云同步失败：{type(exc).__name__}；可在设置里查看")

    def _toast_once(self, text: str) -> None:
        try:
            self.window._toast(text, 2600)
        except Exception:  # noqa: BLE001
            pass
