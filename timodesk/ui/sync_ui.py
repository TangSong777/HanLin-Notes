"""Notes 云同步：首次配对，之后由一个按钮或后台定时器触发。"""
from __future__ import annotations

import socket

from PyQt5.QtCore import Qt, QThread
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from .title_bar import FramelessDialog

DEFAULT_SERVER = "https://sync.yanghanlin.cn"


def _button(text: str, name: str = "Plain"):
    from .dialogs import _button as make

    return make(text, name)


class _Worker(QThread):
    """跑一个阻塞函数；结果由主线程轮询取回，避免跨线程触碰界面。"""

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self.result = None
        self.error = None

    def run(self):  # noqa: D401
        try:
            self.result = self._fn()
        except BaseException as exc:  # noqa: BLE001 - 统一回主线程处理
            self.error = exc


def describe_error(exc: BaseException) -> str:
    """把同步异常翻成一句用户能看懂的话。"""
    name = type(exc).__name__
    text = str(exc) or name
    pairing_errors = {
        "totp_required": "请输入 Authenticator 的六位动态验证码。",
        "totp_invalid": "动态验证码不正确，请核对手机上的当前验证码。",
        "totp_reused": "这个验证码已经用过，请等下一个。",
        "totp_unavailable": "服务器尚未启用动态验证码配对。",
        "bad_code": "服务器尚未启用动态验证码配对。",
        "too_many_failures": "尝试次数过多，请稍后再试。",
    }
    if getattr(exc, "code", None) in pairing_errors:
        return pairing_errors[exc.code]
    if name == "AuthError":
        return f"设备未获授权，请重新配对。\n{text}"
    if name == "ConflictError":
        head = getattr(exc, "remote_head", None)
        hint = f"云端最新版本 {str(head)[:8]}" if head else "云端已有新版本"
        return f"{hint}。已保留本地与云端两份。\n{text}"
    if name == "UnsafeLibraryError":
        return f"库里有明文密码字段，为防止外泄没有上传。请先在笔记里清理。\n{text}"
    if name == "SnapshotUnstable":
        return f"笔记正在改动，快照不稳定，请稍后重试。\n{text}"
    if name == "BusyError":
        return f"库正在被写入或已有同步在跑，请稍后再试。\n{text}"
    if name in ("QuotaError", "TooLargeError"):
        return f"服务端容量不足或单个文件超过上限。\n{text}"
    if name == "BlobVerifyError":
        return f"下载内容校验失败，云端数据可能损坏。\n{text}"
    if name == "NetworkError":
        return f"网络连接失败，请检查服务器地址与网络。\n{text}"
    return text


class OperationDialog(FramelessDialog):
    """Keep the modal window alive while a background operation owns its state."""

    def __init__(self, parent, title):
        super().__init__(parent, title)
        self._worker = None

    def reject(self):
        if self._worker is None:
            super().reject()

    def closeEvent(self, event):  # noqa: N802
        if self._worker is not None:
            event.ignore()
        else:
            super().closeEvent(event)

    def _start(self, fn, done, failed):
        if self._worker is not None:
            return
        worker = _Worker(fn, self)
        self._worker = worker
        self._bar.btn_close.setEnabled(False)

        def complete():
            self._worker = None
            self._bar.btn_close.setEnabled(True)
            result, issue = worker.result, worker.error
            worker.deleteLater()
            if issue is None:
                done(result)
            else:
                failed(issue)

        worker.finished.connect(complete)
        worker.start()


class SyncDialog(OperationDialog):
    def __init__(self, parent, settings, lib, *, credential_path=None):
        super().__init__(parent, "云同步")
        self.settings = settings
        self.lib = lib
        self.credential_path = credential_path
        self.restored = False
        self.paired = False

        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(24, 16, 24, 18)
        lay.setSpacing(10)
        self.state = QLabel("正在检查配对…", objectName="FieldHeading")
        lay.addWidget(self.state)
        self.detail = QLabel("", objectName="FieldLabel")
        self.detail.setWordWrap(True)
        lay.addWidget(self.detail)

        self.pair_fields = QWidget()
        fields = QVBoxLayout(self.pair_fields)
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setSpacing(6)
        config = self.settings.get("sync") or {}
        fields.addWidget(QLabel("服务器地址", objectName="FieldLabel"))
        self.server = QLineEdit(config.get("serverUrl") or DEFAULT_SERVER)
        fields.addWidget(self.server)
        pair_row = QHBoxLayout()
        self.code = QLineEdit()
        self.code.setPlaceholderText("Authenticator 六位动态验证码")
        self.code.setMaxLength(32)
        self.code.setEchoMode(QLineEdit.Password)
        pair_row.addWidget(self.code, 1)
        self.pair_button = _button("配对", "Primary")
        self.pair_button.clicked.connect(self._pair)
        pair_row.addWidget(self.pair_button)
        fields.addLayout(pair_row)
        lay.addWidget(self.pair_fields)
        self.pair_fields.hide()

        privacy = QLabel("配对后会自动同步。切换本地库前会备份正文和附件。"
                         "服务器可读取同步内容，换机后请重新配对。", objectName="FieldLabel")
        privacy.setWordWrap(True)
        lay.addWidget(privacy)
        lay.addStretch(1)
        footer = QHBoxLayout()
        footer.addStretch(1)
        self.sync_button = _button("立即同步", "Primary")
        self.sync_button.clicked.connect(self._sync)
        footer.addWidget(self.sync_button)
        lay.addLayout(footer)
        self.sync_button.hide()
        self.setFixedSize(500, 288)
        self.add_edge()
        factory = self._factory()
        self._run(lambda: factory().paired, self._show_state)

    def _factory(self):
        from ..sync import SyncClient, SyncConfig
        endpoint = self.server.text().strip() or DEFAULT_SERVER
        device = (self.settings.get("sync") or {}).get("deviceName") or socket.gethostname()
        root, credentials = self.lib.root, self.credential_path
        return lambda: SyncClient(SyncConfig(endpoint, device, credentials), data_dir=root)

    def _show_state(self, paired):
        self.paired = bool(paired)
        self.state.setText("自动同步已开启" if paired else "尚未配对")
        self.pair_fields.setVisible(not paired)
        self.sync_button.setVisible(paired)
        if paired:
            automated = getattr(self.parentWidget(), "autosync", None)
            self.detail.setText(automated.last_status if automated and automated.last_status else "等待首次同步。")
        else:
            self.detail.setText("填写为这台设备签发的配对码。")

    def _run(self, job, done, message="正在处理…"):
        self.detail.setText(message)
        self.pair_fields.setEnabled(False)
        self.sync_button.setEnabled(False)

        def complete(result):
            self.pair_fields.setEnabled(True)
            self.sync_button.setEnabled(True)
            done(result)

        def failed(exc):
            self.pair_fields.setEnabled(True)
            self.sync_button.setEnabled(True)
            if type(exc).__name__ == "AuthError":
                self._show_state(False)
            self.detail.setText(describe_error(exc))

        self._start(job, complete, failed)

    def _pair(self):
        code, server = self.code.text().strip(), self.server.text().strip() or DEFAULT_SERVER
        if not code:
            self.detail.setText("请输入 Authenticator 的六位动态验证码。")
            return
        factory = self._factory()
        self.code.clear()

        def done(_result):
            config = dict(self.settings.get("sync") or {})
            config.update(enabled=True, autoSync=True, serverUrl=server)
            self.settings.update({"sync": config})
            self._show_state(True)
            self.detail.setText("配对成功，将自动同步。")

        self._run(lambda: factory().pair(code), done, "正在配对…")

    def _sync(self):
        from ..autosync import status_text
        factory = self._factory()

        def done(result):
            self.detail.setText(status_text(result))
            if result.get("state") == "pulled":
                self.restored = True
                self.accept()

        self._run(lambda: factory().sync_once(), done, "正在同步…")
