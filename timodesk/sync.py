"""寒淋笔记云同步客户端（纯标准库 + ctypes）。

模块定位
========

把本地库（Data 目录：library.json + notes/ + attachments/ + history/）与寒淋云
同步服务（hanlin-sync-service）做增量同步。协议以
hanlin-sync-service/app/server.mjs 为准：一对线性历史、blob 先去重上传、commit
作为发布点、parent 必须等于当前 head、opId 幂等。

公开 API
========

SyncConfig
    连接与服务参数；credential_path 默认
    %LOCALAPPDATA%\\HanLin-Notes\\sync-credential.bin。
CredentialStore
    凭据文件读写。Windows 上用 DPAPI（crypt32.CryptProtectData）加密，并把文件/
    目录 ACL 收紧到当前用户；DPAPI 不可用时退化为「仅本机文件权限」并在 warning
    里明确报出（此时文件是未加密的 JSON，仅供开发/无 DPAPI 环境使用）。
build_snapshot(data_dir, library=None) -> Snapshot
    在临时目录构造一致性快照：先让 Library.save() 落盘内存状态，再复制可同步的
    四个路径；每个文件复制前后各校验一次 (size, mtime_ns, sha256)，发现写者正在
    编辑就抛 SnapshotUnstable（调用方重试），绝不上传撕裂快照。
find_unsafe_content(root) -> list
    扫描 library.json、notes/**、history/** 里疑似账号明文密码字段（键名匹配
    passw/pwd/secret/token 等且值非空），返回命中项（不含密码值）。
class SyncClient
    pair(code) / status() / push() / pull() / restore_commit()。
    所有请求带 Cache-Control: no-store、超时、Connection: close，不跟随重定向
    （因此也不会把 Authorization 带到别的主机）；429/5xx 指数退避，401 抛
    AuthError，网络错误抛 NetworkError。

冲突与恢复语义
==============

* push() 以本地凭据里的 baseCommit 作为 parent：服务端当前 head 与之不同则返回
  409，客户端抛 ConflictError.remote_head，调用方应 pull() 合并（或放弃本地改动）
  后再 push。
* pull(commit_id=None) 默认拉最新 head；下载到 data_dir 同卷的暂存目录，逐文件
  校验长度与 sha256、校验 library.json 可解析且 notes 引用完整，再把旧库备份到
  data_dir/../.sync-backup-<时间戳>，最后用两次目录重命名原子切换（保留
  settings.json、backup/、.hanlin-portable 等本地文件）。切换失败自动回滚；回滚
  也失败时保留 .sync-restore-<库名>.json 状态文件，下次调用 pull() 会先恢复。
* restore_commit(commit_id) 等价于 pull(commit_id)：只恢复本地历史版本，不自动
  上传（恢复后 baseCommit 指向该历史 commit，之后 push 若 head 已前进会得到
  ConflictError，由调用方决定是否覆盖）。

安全边界
========

* push() 在发出任何网络请求之前扫描快照内容，发现疑似账号明文密码即抛
  UnsafeLibraryError 并中止（服务端不会收到任何 blob/commit）。
* 凭据文件不含明文 token（Windows DPAPI 加密）；异常消息、日志不含 token。
* 服务端可读取全部同步内容（不做端到端加密），文档见 notes-sync-impl.md。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as _wt
import hashlib
import http.client
import json
import logging
import os
import random
import re
import secrets
import shutil
import socket
import ssl
import stat
import subprocess
import tempfile
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

LOGGER = logging.getLogger("timodesk.sync")

__all__ = [
    "SyncError",
    "NetworkError",
    "AuthError",
    "ConflictError",
    "SnapshotUnstable",
    "UnsafeLibraryError",
    "BusyError",
    "BlobVerifyError",
    "InvalidCommitError",
    "TooLargeError",
    "QuotaError",
    "SyncConfig",
    "CredentialStore",
    "SnapshotEntry",
    "Snapshot",
    "PushResult",
    "PullResult",
    "build_snapshot",
    "collect_local_manifest",
    "find_unsafe_content",
    "default_credential_path",
    "SyncClient",
]

#: 服务端只接受这一个 app 名（notes 库）。
APP_NAME = "notes"
#: 客户端写入 commit 的标识，方便服务端审计辨认。
CLIENT_NAME = "HanLin-Notes-Sync"
CLIENT_VERSION = "1.0.0"
PROTOCOL_VERSION = 1

#: 可同步的相对路径（服务端 allowlist 的子集：客户端只发这四类）。
SYNC_FILE = "library.json"
SYNC_DIRS = ("notes", "attachments", "history")
SYNC_ENTRIES = (SYNC_FILE,) + SYNC_DIRS

#: 应用写锁约定：设置页在写库期间可以创建该文件，同步客户端见到即拒绝切换。
DEFAULT_LOCK_FILES = (".hanlin-sync.lock",)

#: 凭据文件默认位置（与库目录分开，避免被同步/备份带出）。
CREDENTIAL_DIR = "HanLin-Notes"
CREDENTIAL_FILENAME = "sync-credential.bin"

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 4
DEFAULT_RETRY_BASE_DELAY = 0.5
DEFAULT_RETRY_MAX_DELAY = 8.0
DEFAULT_MAX_FILE_BYTES = 90 * 1024 * 1024
DEFAULT_MAX_MANIFEST_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_CHAIN_DEPTH = 100
DEFAULT_MAX_JSON_BYTES = 4 * 1024 * 1024

#: 敏感字段键名（大小写不敏感）。
SENSITIVE_KEY_RE = re.compile(
    r"passw(?:or)?d|pwd|secret|token|credential|api[_-]?key|access[_-]?key",
    re.IGNORECASE,
)
#: 笔记正文里的 password: value / token = value 形式。
SENSITIVE_TEXT_RE = re.compile(
    r"""(?ix)
    (?<![A-Za-z0-9_.-])
    (?:passw(?:or)?d|pwd|secret|token|credential|api[_-]?key|access[_-]?key)
    [ \t]* [:=] [ \t]*
    (?:
        "([^"\n]*)" |
        '([^'\n]*)' |
        ([^\s,;，。；\n]+)
    )
    """
)
#: 明显是占位符的值不算敏感（减少误报）。
MASKED_VALUES = frozenset(
    {
        "", "none", "null", "nil", "n/a", "na", "-", "--", "—", "无", "空",
        "***", "****", "******", "sensitive", "redacted", "hidden",
    }
)


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class SyncError(Exception):
    """同步相关错误的基类。

    code 尽量复用服务端返回的 error 字符串（如 parent_mismatch），status 是
    HTTP 状态码（本地错误为 None）。
    """

    def __init__(self, message: str, *, code: str | None = None, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status

    def __str__(self) -> str:
        text = super().__str__()
        if self.code and self.code not in text:
            return f"{text}（{self.code}）"
        return text


class NetworkError(SyncError):
    """连接失败、超时、TLS 失败、响应截断等网络层错误。"""


class AuthError(SyncError):
    """401：令牌失效/未配对/配对码无效，需要重新配对。"""


class ConflictError(SyncError):
    """409：parent 过期、opId 复用，或历史分叉。

    remote_head 是服务端返回的当前 head id（parent_mismatch 时取 detail；
    detail 为字符串 "null" 时表示远端是空库）；reused_op_id 对应 op_id_reused。
    """

    def __init__(
        self,
        message: str,
        *,
        remote_head: str | None = None,
        code: str | None = None,
        status: int | None = 409,
        reused_op_id: bool = False,
    ):
        super().__init__(message, code=code, status=status)
        self.remote_head = remote_head
        self.reused_op_id = reused_op_id


class SnapshotUnstable(SyncError):
    """快照期间源文件被写者改动；调用方应重试 build_snapshot/push。"""


class UnsafeLibraryError(SyncError):
    """库内容疑似含账号明文密码字段，拒绝上传。

    findings 是 find_unsafe_content() 的返回结构（只含路径/键名/行号，绝不含
    密码值），异常消息同样不回显值。
    """

    def __init__(self, findings: Sequence[dict[str, Any]]):
        self.findings = list(findings)
        where = "、".join(_format_finding(item) for item in self.findings[:5])
        more = f" 等 {len(self.findings)} 处" if len(self.findings) > 5 else ""
        super().__init__(
            "库内容包含疑似账号明文密码字段，已拒绝上传（"
            + where
            + more
            + "）。请改用应用内账号密码管理，或在设置中确认后清理这些字段。",
            code="unsafe_library",
        )


class BusyError(SyncError):
    """目标库正在被写（检测到锁文件）或调用方声明处于维护写状态。"""


class BlobVerifyError(SyncError):
    """下载的 blob 长度或 sha256 与 commit 清单不符（服务端数据损坏）。"""


class InvalidCommitError(SyncError):
    """commit 不存在、清单非法、library.json 不可解析或引用不完整。"""


class TooLargeError(SyncError):
    """单文件超过服务端上限（413）。"""


class QuotaError(SyncError):
    """服务端配额/磁盘不足（507）。"""


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


@dataclass
class SyncConfig:
    """同步客户端配置。

    server_url: 服务端根地址，如 https://sync.example.com 或
        http://127.0.0.1:18080；可带路径前缀（反向代理场景）。
    device_name: 配对时展示的设备名。
    credential_path: 凭据文件路径；None 用默认位置
        %LOCALAPPDATA%\\HanLin-Notes\\sync-credential.bin。
    timeout: 单次 HTTP 请求超时（秒）。
    max_retries: 429/5xx 的最大重试次数（不含首次请求）。
    retry_base_delay: 指数退避起始延迟（秒）。
    retry_max_delay: 退避上限（秒）。
    max_file_bytes: 单文件上限；默认与服务端一致（90 MiB）。
    max_manifest_bytes: commit 清单 JSON 上限。
    max_chain_depth: status() 回溯历史链的最大深度。
    lock_files: 视为「应用正在写库」的锁文件名（相对 data_dir）。
    verify_tls: HTTPS 是否校验证书（仅测试允许 False）。
    user_agent: User-Agent。
    """

    server_url: str
    device_name: str = field(default_factory=lambda: socket.gethostname() or "HanLin-Notes")
    credential_path: Path | None = None
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    retry_base_delay: float = DEFAULT_RETRY_BASE_DELAY
    retry_max_delay: float = DEFAULT_RETRY_MAX_DELAY
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_manifest_bytes: int = DEFAULT_MAX_MANIFEST_BYTES
    max_chain_depth: int = DEFAULT_MAX_CHAIN_DEPTH
    lock_files: tuple[str, ...] = DEFAULT_LOCK_FILES
    verify_tls: bool = True
    user_agent: str = f"{CLIENT_NAME}/{CLIENT_VERSION}"

    def __post_init__(self) -> None:
        self.server_url = normalize_server_url(self.server_url)
        parts = urllib.parse.urlsplit(self.server_url)
        if parts.username or parts.password:
            raise SyncError("server_url 不能包含用户名/密码，请改用配对令牌")
        if parts.query or parts.fragment:
            raise SyncError("server_url 不能包含查询串或锚点")
        if parts.scheme == "http" and not _is_loopback_host(parts.hostname or ""):
            raise SyncError("云同步服务器必须使用 HTTPS，本机测试除外。")
        if self.timeout <= 0:
            raise SyncError("timeout 必须大于 0")
        if self.max_retries < 0:
            raise SyncError("max_retries 不能为负")
        if self.credential_path is not None:
            self.credential_path = Path(self.credential_path)

    @property
    def insecure_http(self) -> bool:
        """是否使用非回环的明文 HTTP（令牌会明文传输，仅自建内网可用）。"""
        parts = urllib.parse.urlsplit(self.server_url)
        return parts.scheme == "http" and not _is_loopback_host(parts.hostname or "")

    def base_path(self) -> str:
        """server_url 的路径前缀（无尾部斜杠）。"""
        return urllib.parse.urlsplit(self.server_url).path.rstrip("/")


@dataclass
class PushResult:
    """push() 的结果。"""

    commit_id: str
    parent: str | None
    at: str
    device: str
    file_count: int
    bytes: int
    replay: bool
    op_id: str
    uploaded: int = 0
    existed: int = 0
    deduplicated: int = 0
    unchanged: bool = False

    def __repr__(self) -> str:
        return (
            f"PushResult(commit_id={self.commit_id!r}, parent={self.parent!r}, "
            f"file_count={self.file_count}, bytes={self.bytes}, replay={self.replay}, "
            f"uploaded={self.uploaded}, existed={self.existed})"
        )


@dataclass
class PullResult:
    """pull()/restore_commit() 的结果。"""

    commit_id: str
    parent: str | None
    device: str
    file_count: int
    bytes: int
    changed: bool
    backup_dir: Path | None = None
    downloaded: int = 0
    deduplicated: int = 0

    def __repr__(self) -> str:
        return (
            f"PullResult(commit_id={self.commit_id!r}, changed={self.changed}, "
            f"file_count={self.file_count}, bytes={self.bytes}, backup_dir={self.backup_dir!r})"
        )


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _local_stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except FileNotFoundError as exc:
        raise SnapshotUnstable(f"快照期间文件消失了：{path}") from exc
    except OSError as exc:
        raise SyncError(f"读取文件失败：{path}", code="read_failed") from exc
    return digest.hexdigest()


def _normalize_rel_path(rel: str) -> str:
    """把相对路径规范成 posix 形式；拒绝穿越/绝对路径/反斜杠。"""
    if not rel or not isinstance(rel, str):
        raise InvalidCommitError("manifest 含空路径")
    if "\\" in rel or rel.startswith("/") or ":" in rel:
        raise InvalidCommitError(f"manifest 路径非法：{rel!r}")
    parts = rel.split("/")
    if any(part in ("", ".", "..") or part.endswith((" ", ".")) or
           re.search(r'[<>|"?*\x00-\x1f]', part) or
           re.fullmatch(r"(?i:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?", part)
           for part in parts):
        raise InvalidCommitError(f"manifest 路径非法：{rel!r}")
    return rel


def _safe_join(root: Path, rel: str) -> Path:
    rel = _normalize_rel_path(rel)
    return root.joinpath(*rel.split("/"))


def _skip_entry_name(name: str) -> bool:
    """快照时跳过隐藏文件、临时文件、锁文件和同步状态文件。"""
    if not name or name.startswith("."):
        return True
    lowered = name.lower()
    if lowered.endswith((".tmp", ".part", ".swp", ".lock", ".crdownload", "~")):
        return True
    if ".sync-" in lowered or ".hanlin-sync" in lowered:
        return True
    if lowered.startswith("library.json."):  # library.json.broken-xxx 等损坏副本
        return True
    return False


def _assert_plain_file_path(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise InvalidCommitError(f"同步目录包含符号链接或重解析点：{path.name}")


def _iter_sync_files(root: Path) -> Iterator[tuple[str, Path]]:
    """遍历可同步文件，产出 (posix 相对路径, 绝对路径)。"""
    root = Path(root)
    library = root / SYNC_FILE
    if not library.is_file():
        raise SyncError(
            f"数据目录缺少 {SYNC_FILE}：{root}（请先初始化笔记库并保存）",
            code="library_missing",
        )
    _assert_plain_file_path(root)
    _assert_plain_file_path(library)
    yield SYNC_FILE, library
    for dirname in SYNC_DIRS:
        base = root / dirname
        if not base.is_dir():
            continue
        _assert_plain_file_path(base)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(name for name in dirnames if not _skip_entry_name(name))
            current = Path(dirpath)
            for name in dirnames:
                _assert_plain_file_path(current / name)
            for name in sorted(filenames):
                if _skip_entry_name(name):
                    continue
                path = current / name
                try:
                    _assert_plain_file_path(path)
                    info = path.stat()
                except OSError as exc:
                    raise SnapshotUnstable(f"快照期间文件消失了：{path}") from exc
                if not stat.S_ISREG(info.st_mode):
                    continue
                yield path.relative_to(root).as_posix(), path


def _is_loopback_host(host: str) -> bool:
    host = (host or "").strip().lower()
    if host in {"localhost", "127.0.0.1", "::1", "[::1]"}:
        return True
    return host.startswith("127.")


def normalize_server_url(url: str) -> str:
    """规范化服务端根地址（去尾部斜杠、校验 scheme/host）。"""
    text = (url or "").strip()
    if not text:
        raise SyncError("server_url 不能为空")
    parts = urllib.parse.urlsplit(text)
    if parts.scheme not in ("http", "https"):
        raise SyncError("server_url 必须以 http:// 或 https:// 开头")
    if not parts.hostname:
        raise SyncError("server_url 缺少主机名")
    path = parts.path.rstrip("/")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def default_credential_path() -> Path:
    """凭据文件默认路径：%LOCALAPPDATA%\\HanLin-Notes\\sync-credential.bin。"""
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata) / CREDENTIAL_DIR / CREDENTIAL_FILENAME
    return Path.home() / ".hanlin-notes" / CREDENTIAL_FILENAME


def _format_finding(item: dict[str, Any]) -> str:
    path = str(item.get("path") or "?")
    field_name = str(item.get("field") or item.get("key") or "?")
    line = item.get("line")
    suffix = f":{line}" if line else ""
    return f"{path}{suffix} 的 {field_name}"


def _nonempty_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (bytes, bytearray)):
        return len(value) > 0
    if isinstance(value, (dict, list, tuple, set)):
        return len(value) > 0
    return True


# ---------------------------------------------------------------------------
# 快照
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SnapshotEntry:
    """快照清单中的一项。"""

    path: str
    sha256: str
    size: int

    def to_manifest(self) -> dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "size": self.size}


@dataclass
class Snapshot:
    """build_snapshot() 的产物。

    root 是临时快照目录（上传时从这里读），source_dir 是原库目录，entries 是
    按 posix 相对路径排序的文件清单。用完后调用 cleanup()，或作为上下文管理器
    使用（with build_snapshot(...) as snap: ...）。
    """

    root: Path
    source_dir: Path
    entries: tuple[SnapshotEntry, ...]
    created_at: str

    def manifest(self) -> list[dict[str, Any]]:
        """给 POST /v1/apps/notes/commits 用的 files 数组。"""
        return [entry.to_manifest() for entry in self.entries]

    def path_map(self) -> dict[str, Path]:
        """相对路径 -> 快照目录内的绝对路径。"""
        return {entry.path: _safe_join(self.root, entry.path) for entry in self.entries}

    def entry(self, rel: str) -> SnapshotEntry | None:
        for item in self.entries:
            if item.path == rel:
                return item
        return None

    @property
    def file_count(self) -> int:
        return len(self.entries)

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.entries)

    def cleanup(self) -> None:
        shutil.rmtree(str(self.root), ignore_errors=True)

    def __enter__(self) -> "Snapshot":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.cleanup()
        return False

    def __iter__(self) -> Iterator[SnapshotEntry]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)


def _stat_signature(path: Path) -> tuple[int, int]:
    """返回 (size, mtime_ns)；文件消失视为快照不稳定。"""
    try:
        info = path.stat()
    except FileNotFoundError as exc:
        raise SnapshotUnstable(f"快照期间文件消失了：{path}") from exc
    return int(info.st_size), int(info.st_mtime_ns)


def build_snapshot(target: Any, library: Any = None) -> Snapshot:
    """在临时目录构造一致快照，返回 Snapshot。

    target 可以是 Library 实例（有 save/root 属性）或 Data 目录路径。传入
    Library 时会先 save() 把内存状态落盘。复制前记录源文件 (size, mtime_ns,
    sha256)，复制后再次校验（并核对副本 sha256），任何变化都抛
    SnapshotUnstable，绝不让调用方上传撕裂快照。
    """
    if library is None:
        if hasattr(target, "save") and hasattr(target, "root"):
            library = target
        else:
            library = False
    if library is not False and library is not None:
        root = Path(getattr(library, "root"))
        try:
            library.save()
        except Exception as exc:  # Library.save 失败通常意味着磁盘/权限问题
            raise SyncError(f"保存当前笔记库失败，无法生成快照：{exc}", code="save_failed") from exc
    else:
        root = Path(target)
    library = None
    if not root.exists():
        raise SyncError(f"数据目录不存在：{root}", code="data_dir_missing")
    stage = Path(tempfile.mkdtemp(prefix="hanlin-sync-snap-"))
    entries: list[SnapshotEntry] = []
    signatures = {}
    try:
        for rel, src in _iter_sync_files(root):
            try:
                size_before, mtime_before = _stat_signature(src)
            except SnapshotUnstable:
                raise
            sha_before = _sha256_file(src)
            dst = _safe_join(stage, rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copyfile(str(src), str(dst))
            except FileNotFoundError as exc:
                raise SnapshotUnstable(f"快照期间文件消失了：{rel}") from exc
            except OSError as exc:
                raise SyncError(f"复制文件失败：{rel}（{exc}）", code="snapshot_copy_failed") from exc
            size_after, mtime_after = _stat_signature(src)
            sha_after = _sha256_file(src)
            sha_copy = _sha256_file(dst)
            if (
                size_after != size_before
                or mtime_after != mtime_before
                or sha_after != sha_before
                or sha_copy != sha_before
            ):
                raise SnapshotUnstable(
                    f"库文件在快照期间被修改：{rel}（写者正在编辑，请重试）"
                )
            entries.append(SnapshotEntry(path=rel, sha256=sha_before, size=size_before))
            signatures[rel] = (size_before, mtime_before)
        if {rel: _stat_signature(path) for rel, path in _iter_sync_files(root)} != signatures:
            raise SnapshotUnstable("笔记库在生成同步清单时发生了变化，请重试。")
    except BaseException:
        shutil.rmtree(str(stage), ignore_errors=True)
        raise
    entries.sort(key=lambda item: item.path)
    return Snapshot(
        root=stage,
        source_dir=root,
        entries=tuple(entries),
        created_at=_utc_now_iso(),
    )


def _as_library_root(value: Any) -> Path | None:
    """把 Library 实例转换成它的 Data 目录；路径类输入返回 None。

    注意 pathlib.Path 自身也有 .root 属性（Windows 盘符根），所以不能只用
    hasattr(value, "root") 判断，否则会把 Data 路径误当成 Library。
    """
    if isinstance(value, (str, bytes, os.PathLike)):
        return None
    if not hasattr(value, "save") or not hasattr(value, "root"):
        return None
    try:
        return Path(getattr(value, "root"))
    except (TypeError, ValueError):
        return None


def collect_local_manifest(root: Any) -> dict[str, tuple[str, int]]:
    """计算本地库的可同步清单：posix 相对路径 -> (sha256, size)。

    root 可以是 Library 实例或 Data 目录。用于 status() 判断本地是否领先基线。
    """
    library_root = _as_library_root(root)
    root = library_root if library_root is not None else Path(root)
    manifest: dict[str, tuple[str, int]] = {}
    for rel, path in _iter_sync_files(root):
        size, _mtime = _stat_signature(path)
        manifest[rel] = (_sha256_file(path), size)
    return manifest


# ---------------------------------------------------------------------------
# 敏感内容检查
# ---------------------------------------------------------------------------


def _scan_json_value(value: Any, rel: str, findings: list[dict[str, Any]], prefix: str = "") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            field = f"{prefix}.{key_text}" if prefix else key_text
            if SENSITIVE_KEY_RE.search(key_text) and _nonempty_value(item):
                findings.append({"path": rel, "field": field, "line": None})
            _scan_json_value(item, rel, findings, field)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _scan_json_value(item, rel, findings, f"{prefix}[{index}]")


def _scan_json_file(path: Path, rel: str, findings: list[dict[str, Any]]) -> None:
    try:
        raw = path.read_bytes()
    except OSError:
        return
    if len(raw) > DEFAULT_MAX_JSON_BYTES:
        return
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        # JSON 坏了也按文本扫一遍，避免漏过明文密码。
        text = raw.decode("utf-8", errors="replace")
        _scan_text(text, rel, findings)
        return
    _scan_json_value(data, rel, findings)


def _scan_text(text: str, rel: str, findings: list[dict[str, Any]]) -> None:
    for lineno, line in enumerate(text.splitlines(), 1):
        for match in SENSITIVE_TEXT_RE.finditer(line):
            raw_value = match.group(1) or match.group(2) or match.group(3) or ""
            value = raw_value.strip().strip("\"'")
            if value.lower() in MASKED_VALUES:
                continue
            field = match.group(0).split(":", 1)[0].split("=", 1)[0].strip().lower()
            findings.append({"path": rel, "field": field or "text", "line": lineno})


def find_unsafe_content(root: Any) -> list[dict[str, Any]]:
    """扫描库内疑似账号明文密码字段，返回命中列表（不含任何值）。

    覆盖 library.json、notes/** 文本与 history/**/*.json（历史快照里同样保存
    password 字段）。键名匹配 SENSITIVE_KEY_RE 且值非空即视为命中；笔记正文
    按 password: xxx / token = xxx 形式匹配。
    """
    library_root = _as_library_root(root)
    base = library_root if library_root is not None else Path(root)
    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int | None]] = set()

    def add(items: list[dict[str, Any]]) -> None:
        for item in items:
            marker = (item["path"], item["field"], item.get("line"))
            if marker not in seen:
                seen.add(marker)
                findings.append(item)

    library = base / SYNC_FILE
    if library.is_file():
        _scan_json_file(library, SYNC_FILE, findings)
    notes_dir = base / "notes"
    if notes_dir.is_dir():
        for dirpath, dirnames, filenames in os.walk(notes_dir):
            dirnames[:] = sorted(name for name in dirnames if not _skip_entry_name(name))
            for name in sorted(filenames):
                if _skip_entry_name(name):
                    continue
                path = Path(dirpath) / name
                if path.suffix.lower() not in (".md", ".markdown", ".txt", ".json"):
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                rel = path.relative_to(base).as_posix()
                collected: list[dict[str, Any]] = []
                _scan_text(text, rel, collected)
                add(collected)
    history_dir = base / "history"
    if history_dir.is_dir():
        for dirpath, dirnames, filenames in os.walk(history_dir):
            dirnames[:] = sorted(name for name in dirnames if not _skip_entry_name(name))
            for name in sorted(filenames):
                if _skip_entry_name(name) or not name.lower().endswith(".json"):
                    continue
                path = Path(dirpath) / name
                rel = path.relative_to(base).as_posix()
                collected = []
                _scan_json_file(path, rel, collected)
                add(collected)
    return findings


# ---------------------------------------------------------------------------
# 凭据存储（Windows DPAPI）
# ---------------------------------------------------------------------------

_DPAPI_ENTROPY = b"HanLin-Notes/sync-credential"
_DPAPI_UI_FORBIDDEN = 0x1
_CRED_MAGIC_DPAPI = b"HLNSYNC1D"
_CRED_MAGIC_PLAIN = b"HLNSYNC1P"


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", _wt.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _allocate_blob(data: bytes) -> tuple[_DataBlob, Any]:
    """构造 DATA_BLOB；返回 (blob, keepalive) 防止底层 buffer 被回收。"""
    buffer = ctypes.create_string_buffer(data, max(len(data), 1))
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    return blob, buffer


class _DPAPI:
    """crypt32 DPAPI 的薄封装（当前用户作用域，需同账户才能解密）。"""

    def __init__(self) -> None:
        self.available = False
        self.error = ""
        if os.name != "nt":
            self.error = "当前平台不是 Windows"
            return
        try:
            self._crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
            self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            prot = self._crypt32.CryptProtectData
            prot.argtypes = [
                ctypes.POINTER(_DataBlob), _wt.LPCWSTR, ctypes.POINTER(_DataBlob),
                ctypes.c_void_p, ctypes.c_void_p, _wt.DWORD, ctypes.POINTER(_DataBlob),
            ]
            prot.restype = _wt.BOOL
            unprot = self._crypt32.CryptUnprotectData
            unprot.argtypes = [
                ctypes.POINTER(_DataBlob), ctypes.POINTER(_wt.LPWSTR),
                ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                _wt.DWORD, ctypes.POINTER(_DataBlob),
            ]
            unprot.restype = _wt.BOOL
            free = self._kernel32.LocalFree
            free.argtypes = [ctypes.c_void_p]
            free.restype = ctypes.c_void_p
        except (OSError, AttributeError) as exc:
            self.error = f"DPAPI 函数不可用：{exc}"
            return
        self.available = True

    def protect(self, data: bytes) -> bytes:
        if not self.available:
            raise SyncError(self.error or "DPAPI 不可用", code="dpapi_unavailable")
        blob_in, _keep_in = _allocate_blob(data)
        entropy, _keep_entropy = _allocate_blob(_DPAPI_ENTROPY)
        out = _DataBlob()
        ok = self._crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, ctypes.byref(entropy), None, None,
            _DPAPI_UI_FORBIDDEN, ctypes.byref(out),
        )
        if not ok:
            raise SyncError(
                "DPAPI 加密失败（WinError %d）" % ctypes.get_last_error(),
                code="dpapi_failed",
            )
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            if out.pbData:
                self._kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))

    def unprotect(self, data: bytes) -> bytes:
        if not self.available:
            raise SyncError(self.error or "DPAPI 不可用", code="dpapi_unavailable")
        blob_in, _keep_in = _allocate_blob(data)
        entropy, _keep_entropy = _allocate_blob(_DPAPI_ENTROPY)
        out = _DataBlob()
        ok = self._crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, ctypes.byref(entropy), None, None,
            _DPAPI_UI_FORBIDDEN, ctypes.byref(out),
        )
        if not ok:
            raise SyncError(
                "DPAPI 解密失败（WinError %d）：凭据不属于当前 Windows 账户或已损坏"
                % ctypes.get_last_error(),
                code="dpapi_unprotect_failed",
            )
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            if out.pbData:
                self._kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def _current_user_spec() -> str | None:
    user = os.environ.get("USERNAME") or os.environ.get("USER")
    if not user:
        return None
    domain = os.environ.get("USERDOMAIN")
    if domain:
        return f"{domain}\\{user}"
    return user


def _run_icacls(args: list[str]) -> bool:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=15,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _harden_windows_path(path: Path, is_dir: bool) -> bool:
    """用 icacls 移除继承并只授予当前用户完全控制；失败返回 False。"""
    user = _current_user_spec()
    if not user:
        return False
    grant = f"{user}:(OI)(CI)F" if is_dir else f"{user}:F"
    return _run_icacls(["icacls", str(path), "/inheritance:r", "/grant:r", grant])


def _harden_path(path: Path, is_dir: bool) -> bool:
    """尽力把文件/目录权限收紧到仅当前用户；POSIX 上 chmod 0700/0600。"""
    try:
        if os.name == "nt":
            return _harden_windows_path(path, is_dir)
        os.chmod(path, 0o700 if is_dir else 0o600)
        return True
    except OSError:
        return False


class CredentialStore:
    """同步凭据文件（token / deviceId / 基线 commit）的读写。

    文件格式：8 字节魔数 + 载荷。Windows 上载荷是 DPAPI 密文（HLNSYNC1D）；
    DPAPI 不可用时退化为明文 JSON（HLNSYNC1P），仅靠文件系统权限保护，并在
    warnings 里明确报出。文件默认位于
    %LOCALAPPDATA%\\HanLin-Notes\\sync-credential.bin，目录与文件权限尽力
    收紧到仅当前用户。
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path is not None else default_credential_path()
        self.warnings: list[str] = []
        self.protection = "unknown"
        self._dpapi = _DPAPI()
        self._fallback_warned = False
        self._permission_warned = False
        if not self._dpapi.available:
            self.warnings.append(
                "DPAPI 不可用（"
                + (self._dpapi.error or "未知原因")
                + "）：同步凭据退化为仅本机文件权限保护（未加密），请注意备份安全"
            )

    def load(self) -> dict[str, dict[str, Any]]:
        """读取凭据；文件不存在/不可解密返回空 dict（调用方应重新配对）。"""
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            self.protection = "dpapi" if self._dpapi.available else "file-permissions"
            return {}
        except OSError as exc:
            self.warnings.append(f"读取凭据文件失败：{exc}")
            return {}
        payload = b""
        if raw.startswith(_CRED_MAGIC_DPAPI):
            if not self._dpapi.available:
                self.warnings.append(
                    "凭据文件由 DPAPI 加密，但当前环境无法解密；请重新配对"
                )
                self.protection = "unavailable"
                return {}
            try:
                payload = self._dpapi.unprotect(raw[len(_CRED_MAGIC_DPAPI):])
            except SyncError as exc:
                self.warnings.append(str(exc))
                self.protection = "unavailable"
                return {}
            self.protection = "dpapi"
        elif raw.startswith(_CRED_MAGIC_PLAIN):
            payload = raw[len(_CRED_MAGIC_PLAIN):]
            self.protection = "file-permissions"
            if not self._fallback_warned:
                self._fallback_warned = True
                self.warnings.append("凭据文件未加密（仅本机文件权限保护）：" + str(self.path))
        else:
            payload = raw
            self.protection = "file-permissions"
        try:
            data = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            self.warnings.append(f"凭据文件损坏，已忽略：{exc}")
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, data: dict[str, Any]) -> None:
        payload = json.dumps(
            data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if self._dpapi.available:
            raw = _CRED_MAGIC_DPAPI + self._dpapi.protect(payload)
            self.protection = "dpapi"
        else:
            raw = _CRED_MAGIC_PLAIN + payload
            self.protection = "file-permissions"
            if not self._fallback_warned:
                self._fallback_warned = True
                self.warnings.append(
                    "DPAPI 不可用：凭据将以未加密形式写入 " + str(self.path)
                )
        parent = self.path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SyncError(f"无法创建凭据目录：{parent}", code="credential_dir_failed") from exc
        if not _harden_path(parent, True) and not self._permission_warned:
            self._permission_warned = True
            self.warnings.append(f"无法收紧凭据目录权限：{parent}")
        tmp_path: Path | None = None
        try:
            fd, tmp_name = tempfile.mkstemp(dir=str(parent), prefix=".sync-cred-", suffix=".tmp")
            tmp_path = Path(tmp_name)
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            _harden_path(tmp_path, False)
            os.replace(str(tmp_path), str(self.path))
            tmp_path = None
        except OSError as exc:
            raise SyncError(f"写入凭据文件失败：{exc}", code="credential_save_failed") from exc
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(str(tmp_path))
                except OSError:
                    pass
        if not _harden_path(self.path, False) and not self._permission_warned:
            self._permission_warned = True
            self.warnings.append(f"无法收紧凭据文件权限：{self.path}")

    def clear(self) -> None:
        try:
            os.unlink(str(self.path))
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise SyncError(f"删除凭据文件失败：{exc}", code="credential_remove_failed") from exc

    def __repr__(self) -> str:
        return f"CredentialStore(path={str(self.path)!r}, protection={self.protection!r})"


# --- 以下：HTTP / SyncClient ---


# ---------------------------------------------------------------------------
# HTTP 客户端
# ---------------------------------------------------------------------------

_JSON_RESPONSE_LIMIT = 8 * 1024 * 1024
_DOWNLOAD_CHUNK = 256 * 1024
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_OCTET_STREAM = "application/octet-stream"
_JSON_CONTENT_TYPE = "application/json"


def _parse_json_response(payload: bytes, what: str) -> Any:
    try:
        return json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise SyncError(f"{what}不是合法 JSON：{exc}", code="bad_response") from exc


class _HttpClient:
    """同步 HTTP 客户端。

    每个请求新建连接（Connection: close）；响应不跟随重定向，因此
    Authorization 不会被带到别的主机；所有请求都带 Cache-Control: no-store
    并受 timeout 约束；429/5xx 做有限次指数退避重试。
    """

    def __init__(self, config: SyncConfig) -> None:
        self.config = config
        parts = urllib.parse.urlsplit(config.server_url)
        self.scheme = parts.scheme
        self.host = parts.hostname or ""
        self.port = parts.port
        self.prefix = parts.path.rstrip("/")
        self._ssl_context: ssl.SSLContext | None = None
        if self.scheme == "https":
            if config.verify_tls:
                self._ssl_context = ssl.create_default_context()
            else:
                self._ssl_context = ssl._create_unverified_context()

    def _path(self, suffix: str) -> str:
        if not suffix.startswith("/"):
            suffix = "/" + suffix
        return self.prefix + suffix

    def _connection(self) -> http.client.HTTPConnection:
        if self.scheme == "https":
            return http.client.HTTPSConnection(
                self.host, self.port, timeout=self.config.timeout, context=self._ssl_context
            )
        return http.client.HTTPConnection(self.host, self.port, timeout=self.config.timeout)

    def _headers(
        self, token: str | None, content_type: str | None = None, accept: str = _JSON_CONTENT_TYPE
    ) -> dict[str, str]:
        headers = {
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Connection": "close",
            "User-Agent": self.config.user_agent,
            "Accept": accept,
            "X-HanLin-Client": CLIENT_VERSION,
        }
        if content_type:
            headers["Content-Type"] = content_type
        if token:
            headers["Authorization"] = "Bearer " + token
        return headers

    def _sleep_before_retry(self, attempt: int, response_headers: dict[str, str]) -> None:
        base = float(self.config.retry_base_delay)
        delay = min(base * (2 ** attempt), float(self.config.retry_max_delay))
        delay *= 0.5 + random.random() * 0.5
        retry_after = response_headers.get("retry-after")
        if retry_after:
            try:
                delay = max(delay, min(float(retry_after), 30.0))
            except ValueError:
                pass
        if delay > 0:
            time.sleep(delay)

    def _exchange(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes | None,
        limit: int,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = self._connection()
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            status = response.status
            response_headers = {str(k).lower(): str(v) for k, v in response.getheaders()}
            length = response.length
            if length is not None and length > limit:
                raise SyncError(
                    f"服务端响应体过大（{length} 字节，上限 {limit}）",
                    code="response_too_large",
                    status=status,
                )
            payload = response.read(limit)
            return status, response_headers, payload
        except (socket.timeout, TimeoutError) as exc:
            raise NetworkError(f"请求超时（{method} {path}，{self.config.timeout}s）") from exc
        except ssl.SSLError as exc:
            raise NetworkError(f"TLS 连接失败（{method} {path}）：{exc}") from exc
        except (http.client.HTTPException, OSError) as exc:
            raise NetworkError(f"网络错误（{method} {path}）：{exc}") from exc
        finally:
            try:
                connection.close()
            except OSError:
                pass

    def _error_from(self, status: int, payload: bytes) -> SyncError:
        code: str | None = None
        detail: Any = None
        try:
            data = json.loads(payload.decode("utf-8", errors="replace"))
        except ValueError:
            data = None
        if isinstance(data, dict):
            if isinstance(data.get("error"), str):
                code = data["error"]
            detail = data.get("detail")
        text = f"服务端返回 HTTP {status}"
        if code:
            text += f"（{code}）"
        if isinstance(detail, (str, int)) and detail not in ("", None):
            text += f"：{detail}"
        error: SyncError
        if status == 401:
            error = AuthError(
                "认证失败或令牌已失效，请重新配对：" + text,
                code=code or "unauthorized",
                status=status,
            )
        elif status == 403:
            error = SyncError("服务端拒绝访问：" + text, code=code, status=status)
        elif status == 413:
            error = TooLargeError(
                "文件超过服务端单文件上限：" + text,
                code=code or "file_too_large",
                status=status,
            )
        elif status == 507:
            error = QuotaError(
                "服务端存储配额或磁盘空间不足：" + text,
                code=code,
                status=status,
            )
        else:
            error = SyncError(text, code=code, status=status)
        try:
            error.detail = detail  # type: ignore[attr-defined]
        except AttributeError:
            pass
        return error

    def request(
        self,
        method: str,
        suffix: str,
        *,
        token: str | None = None,
        json_body: Any = None,
        raw_body: bytes | None = None,
        content_type: str | None = None,
        expected: Sequence[int] = (200,),
        allow_404: bool = False,
        retry: bool = True,
    ) -> tuple[int, dict[str, str], bytes]:
        """发请求并返回 (status, headers, body)。

        expected 之外的状态码抛对应异常；allow_404 时把 404 作为正常状态返回
        （用于 commits/latest 空库）。429/5xx 只在 retry=True 时重试，且调用方
        必须保证请求可安全重放（GET、按 sha 上传的 PUT、带 opId 的 POST）。
        """
        path = self._path(suffix)
        body = raw_body
        if json_body is not None:
            body = json.dumps(
                json_body, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            content_type = _JSON_CONTENT_TYPE
        attempt = 0
        while True:
            headers = self._headers(token, content_type)
            status, response_headers, payload = self._exchange(
                method, path, headers, body, _JSON_RESPONSE_LIMIT
            )
            if status in _REDIRECT_STATUSES:
                raise SyncError(
                    f"服务端返回重定向 {status}，已拒绝跟随（避免令牌泄漏到其他地址）",
                    code="unexpected_redirect",
                    status=status,
                )
            retryable = status == 429 or 500 <= status < 600
            if retryable and retry and attempt < self.config.max_retries:
                LOGGER.warning("服务端返回 %s，第 %d 次重试 %s %s", status, attempt + 1, method, suffix)
                self._sleep_before_retry(attempt, response_headers)
                attempt += 1
                continue
            if status in expected or (allow_404 and status == 404):
                return status, response_headers, payload
            raise self._error_from(status, payload)

    def download_blob(
        self,
        sha256_hex: str,
        token: str,
        destination: Path,
        *,
        expected_size: int | None = None,
    ) -> int:
        """流式下载 blob 到 destination，边下边算 sha256 并校验。

        只对「尚未开始写文件」的 429/5xx 重试；一旦开始写，出错就让调用方把
        整个暂存目录丢掉重来。返回写入字节数。
        """
        path = self._path(f"/v1/apps/notes/blobs/{sha256_hex}")
        attempt = 0
        while True:
            connection = self._connection()
            try:
                headers = self._headers(token, accept=_OCTET_STREAM)
                connection.request("GET", path, headers=headers)
                response = connection.getresponse()
                status = response.status
                response_headers = {str(k).lower(): str(v) for k, v in response.getheaders()}
                if status != 200:
                    payload = response.read(64 * 1024)
                    retryable = status == 429 or 500 <= status < 600
                    if retryable and attempt < self.config.max_retries:
                        self._sleep_before_retry(attempt, response_headers)
                        attempt += 1
                        continue
                    raise self._error_from(status, payload)
                declared = response.length
                if expected_size is not None and declared is not None and declared != expected_size:
                    raise BlobVerifyError(
                        f"服务端 blob 长度与清单不符：{sha256_hex}（声明 {declared}，清单 {expected_size}）"
                    )
                digest = hashlib.sha256()
                total = 0
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with open(destination, "wb") as handle:
                        while True:
                            chunk = response.read(_DOWNLOAD_CHUNK)
                            if not chunk:
                                break
                            total += len(chunk)
                            if expected_size is not None and total > expected_size:
                                raise BlobVerifyError(
                                    f"下载内容超过清单长度：{sha256_hex}"
                                )
                            digest.update(chunk)
                            handle.write(chunk)
                except OSError as exc:
                    raise SyncError(
                        f"写入暂存文件失败：{destination}", code="stage_write_failed"
                    ) from exc
                actual = digest.hexdigest()
                if actual != sha256_hex:
                    raise BlobVerifyError(
                        f"下载内容 sha256 校验失败：{sha256_hex}（实际 {actual}），已丢弃"
                    )
                if expected_size is not None and total != expected_size:
                    raise BlobVerifyError(
                        f"下载内容长度与清单不符：{sha256_hex}（实际 {total}，清单 {expected_size}）"
                    )
                return total
            except (socket.timeout, TimeoutError) as exc:
                raise NetworkError(f"下载 blob 超时：{sha256_hex}") from exc
            except ssl.SSLError as exc:
                raise NetworkError(f"下载 blob 时 TLS 失败：{exc}") from exc
            except (http.client.HTTPException, OSError) as exc:
                raise NetworkError(f"下载 blob 失败：{sha256_hex}（{exc}）") from exc
            finally:
                try:
                    connection.close()
                except OSError:
                    pass


# --- 以下：SyncClient ---


# ---------------------------------------------------------------------------
# 同步客户端
# ---------------------------------------------------------------------------

# 测试钩子：键为钩子名，值为无参可调用对象。仅测试使用，生产代码默认空表。
_TEST_HOOKS: dict[str, Any] = {}


class _SimulatedCrash(BaseException):
    """测试注入的模拟崩溃（继承 BaseException，业务层不会把它当普通错误吞掉）。"""


def _run_test_hook(name: str) -> None:
    hook = _TEST_HOOKS.get(name)
    if hook is not None:
        hook()


def _slug_for(path: Path) -> str:
    name = Path(path).name or "data"
    return re.sub(r"[^0-9A-Za-z._-]+", "-", name)[:40] or "data"


def _unique_path(parent: Path, prefix: str, *, suffix: str = "") -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    candidate = parent / f"{prefix}{stamp}{suffix}"
    index = 1
    while candidate.exists():
        candidate = parent / f"{prefix}{stamp}-{index}{suffix}"
        index += 1
    return candidate


_OP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _manifest_matches(local: dict[str, tuple[str, int]], files: Any) -> bool:
    """把服务端 files 数组转成 {path: (sha256, size)} 再与本地清单比较。"""
    if not isinstance(files, list):
        return False
    remote: dict[str, tuple[str, int]] = {}
    for item in files:
        if not isinstance(item, dict):
            return False
        path = item.get("path")
        sha = item.get("sha256")
        size = item.get("size")
        if not isinstance(path, str) or not isinstance(sha, str) or not isinstance(size, int):
            return False
        if path in remote:
            return False
        remote[path] = (sha, size)
    return local == remote


def _duplicate_notes(data: dict[str, Any], body_hashes: dict[str, str]) -> list[dict[str, Any]]:
    ignored = {"id", "created_at", "updated_at", "order"}
    signatures: set[tuple[str, str]] = set()
    duplicates = []
    for note in sorted(data["notes"], key=lambda n: (str(n.get("created_at") or ""), n["id"])):
        if note.get("state", "active") == "trash":
            continue
        body_hash = body_hashes.get(f"notes/{note['id']}.md")
        if body_hash is None:
            continue
        metadata = json.dumps({k: v for k, v in note.items() if k not in ignored},
                              sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        key = (body_hash, metadata)
        if key in signatures:
            duplicates.append(note)
        signatures.add(key)
    return duplicates


def _normalized_notes(data: dict[str, Any], body_hashes: dict[str, str]) -> tuple[bytes, int]:
    """Place exact copies into the recycle bin while keeping their IDs and history."""
    duplicates = _duplicate_notes(data, body_hashes)
    if not duplicates:
        return b"", 0
    for note in duplicates:
        note["state"] = "trash"
        note["deleted_at"] = data.get("savedAt") or note.get("updated_at")
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), len(duplicates)


def _deduplicate_notes(stage: Path, data: dict[str, Any], body_hashes: dict[str, str]) -> int:
    payload, count = _normalized_notes(data, body_hashes)
    if count:
        (stage / SYNC_FILE).write_bytes(payload)
    return count


class SyncClient:
    r"""笔记库云同步客户端。

    用法::

        config = SyncConfig(server_url="http://127.0.0.1:18080", device_name="我的笔记本")
        client = SyncClient(config, data_dir=r"D:\HanLin-Notes\Data", library=lib)
        client.pair("ABCD-EFGH")        # 首次配对（配对码由服务端 CLI 生成）
        client.status()                 # same / ahead / behind / diverged / unknown
        client.push()                   # 上传本地新内容
        client.pull()                   # 下载服务端最新提交并原子切换本地库

    线程安全性：一个 SyncClient 实例不应并发调用 push/pull；进程间通过
    data_dir 同级的 .sync-lock-<库名> 文件互斥，切换库目录前还会检测应用锁文件。
    """

    LATEST_PATH = "/v1/apps/notes/commits/latest"
    COMMITS_PATH = "/v1/apps/notes/commits"
    BLOBS_PATH = "/v1/apps/notes/blobs"
    PAIR_PATH = "/v1/pair"
    HEALTH_PATH = "/v1/health"

    def __init__(
        self,
        config: SyncConfig,
        data_dir: str | os.PathLike[str] | None = None,
        library: Any = None,
        *,
        credential_store: CredentialStore | None = None,
    ) -> None:
        if data_dir is not None and library is None and hasattr(data_dir, "save") and hasattr(data_dir, "root"):
            library = data_dir
            data_dir = None
        if data_dir is None and library is None:
            raise TypeError("SyncClient 需要 data_dir 或 Library 实例")
        if data_dir is not None and library is not None:
            raise TypeError("data_dir 与 library 只能传一个")
        self.config = config
        self.library = library
        self.data_dir = Path(getattr(library, "root")) if library is not None else Path(data_dir)  # type: ignore[arg-type]
        self.credentials = credential_store if credential_store is not None else CredentialStore(config.credential_path)
        self._http = _HttpClient(config)
        self._key = normalize_server_url(config.server_url)
        self._store: dict[str, Any] = self.credentials.load()

    # -- 凭据视图 ---------------------------------------------------------

    @property
    def warnings(self) -> list[str]:
        return list(self.credentials.warnings)

    @property
    def protection(self) -> str:
        return self.credentials.protection

    @property
    def credential(self) -> dict[str, Any]:
        entry = self._store.get(self._key)
        return dict(entry) if isinstance(entry, dict) else {}

    @property
    def token(self) -> str | None:
        token = self.credential.get("token")
        return token if isinstance(token, str) and token else None

    @property
    def device_id(self) -> str | None:
        value = self.credential.get("deviceId")
        return value if isinstance(value, str) and value else None

    @property
    def device_name(self) -> str:
        value = self.credential.get("name")
        if isinstance(value, str) and value:
            return value
        return self.config.device_name or ""

    @property
    def base_commit(self) -> str | None:
        value = self.credential.get("baseCommit")
        return value if isinstance(value, str) and value else None

    @property
    def paired(self) -> bool:
        return self.token is not None

    def _update_credential(self, changes: Mapping[str, Any]) -> None:
        entry = self.credential
        entry.update(dict(changes))
        entry.setdefault("app", APP_NAME)
        entry["serverUrl"] = self._key
        self._store[self._key] = entry
        self.credentials.save(self._store)

    def _require_token(self) -> str:
        token = self.token
        if not token:
            raise AuthError(
                "设备尚未配对，请在云同步设置中重新配对。",
                code="not_paired",
                status=None,
            )
        return token

    # -- 配对与连接 -------------------------------------------------------

    def pair(self, code: str) -> dict[str, Any]:
        """验证一次性凭据并保存设备令牌。用过的凭据不能自动重试。"""
        code_text = str(code or "").strip()
        if re.fullmatch(r"[0-9]{6}", code_text):
            endpoint = urllib.parse.urlsplit(self.config.server_url)
            if endpoint.scheme != "https" and endpoint.hostname not in ("127.0.0.1", "::1", "localhost"):
                raise SyncError("动态验证码只能发送到 HTTPS 服务器。", code="pair_requires_https")
            body = {"app": APP_NAME, "totp": code_text, "name": self.config.device_name}
        else:
            code_text = code_text.strip("-").replace(" ", "").upper()
            if len(code_text) < 8:
                raise SyncError("请输入 Authenticator 的六位动态验证码。", code="bad_pair_code")
            body = {"app": APP_NAME, "code": code_text}
        _, _, payload = self._http.request(
            "POST",
            self.PAIR_PATH,
            json_body=body,
            expected=(200, 201),
            retry=False,
        )
        data = _parse_json_response(payload, "配对响应")
        if not isinstance(data, dict):
            raise SyncError("配对响应格式不正确", code="bad_response")
        if data.get("app") not in (None, APP_NAME):
            raise SyncError("该配对有效凭据属于另一款应用。", code="wrong_app")
        token = data.get("token")
        if not isinstance(token, str) or not token:
            raise SyncError("配对响应缺少 token", code="bad_response")
        entry: dict[str, Any] = {
            "token": token,
            "deviceId": str(data.get("deviceId") or ""),
            "name": str(data.get("name") or self.config.device_name or ""),
            "app": str(data.get("app") or APP_NAME),
            "protocol": data.get("protocol"),
            "pairedAt": _utc_now_iso(),
            "baseCommit": None,
            "lastSyncAt": None,
            "serverUrl": self._key,
        }
        self._store[self._key] = entry
        self.credentials.save(self._store)
        LOGGER.info("配对成功：deviceId=%s", entry["deviceId"] or "(未知)")
        return dict(entry)

    def unpair(self) -> None:
        """删除本机保存的该服务端凭据（不影响服务端设备记录）。"""
        if self._key in self._store:
            del self._store[self._key]
            self.credentials.save(self._store)

    def health(self) -> dict[str, Any]:
        _, _, payload = self._http.request("GET", self.HEALTH_PATH, expected=(200,), retry=False)
        data = _parse_json_response(payload, "健康检查响应")
        if not isinstance(data, dict):
            raise SyncError("健康检查响应格式不正确", code="bad_response")
        return data

    def latest_remote(self) -> dict[str, Any] | None:
        """服务端当前最新提交（无提交时返回 None）。"""
        return self._remote_head(self._require_token())

    def history(self, limit: int = 30) -> list[dict[str, Any]]:
        """最近提交列表（服务端顺序：最新在前）。"""
        return self._recent_commits(self._require_token(), limit)

    def sync_once(self, *, before_apply=None) -> dict[str, Any]:
        """Choose the safe direction automatically and never publish an unchanged snapshot."""
        if not self.paired:
            return {"state": "unpaired"}
        local = self._local_manifest()
        remote = self.latest_remote()
        if local is None:
            raise SnapshotUnstable("本地笔记库还没有保存。")
        remote_files = remote.get("files") if remote else None
        if remote and self.base_commit != remote["id"] and not isinstance(remote_files, list):
            remote_files = self._commit_files(str(remote["id"]), self._require_token())
        if not remote:
            state = "ahead" if self.base_commit is None else "diverged"
        elif _manifest_matches(local, remote_files) and self.base_commit != remote["id"]:
            state = "behind"  # Exact replay after an acknowledgement or baseline was lost.
        elif self.base_commit is None:
            library = json.loads((self.data_dir / SYNC_FILE).read_text(encoding="utf-8"))
            populated = bool(library.get("notes") or library.get("topics") or len(local) > 1)
            state = "diverged" if populated else "behind"
        else:
            state = self.status()

        if state == "diverged" and remote and remote.get("device") == self.device_id and remote.get("parent") == self.base_commit:
            library = json.loads((self.data_dir / SYNC_FILE).read_text(encoding="utf-8"))
            payload, count = _normalized_notes(library, {path: sha for path, (sha, _) in local.items()})
            if count and _manifest_matches(
                {**local, SYNC_FILE: (hashlib.sha256(payload).hexdigest(), len(payload))}, remote_files
            ):
                state = "behind"

        if state == "same":
            library = json.loads((self.data_dir / SYNC_FILE).read_text(encoding="utf-8"))
            if _duplicate_notes(library, {path: sha for path, (sha, _) in local.items()}):
                state = "ahead"
        if state == "ahead":
            result = self.push(note="笔记同步")
            if result.deduplicated:
                applied = self.pull(result.commit_id, before_apply=before_apply, expected_local=local)
                return {"state": "pulled" if applied.changed else "same", "commit": result.commit_id,
                        "deduplicated": result.deduplicated}
            return {"state": "same" if result.unchanged else "pushed", "commit": result.commit_id}
        if state == "behind" and remote:
            result = self.pull(str(remote["id"]), before_apply=before_apply, expected_local=local)
            return {"state": "pulled" if result.changed else "same", "commit": result.commit_id,
                    "deduplicated": result.deduplicated}
        return {"state": state}

    # -- 状态比较 ---------------------------------------------------------

    def status(self) -> str:
        """比较本地基线与服务端最新提交。

        返回 same / ahead / behind / diverged / unknown：
        same      本地基线与服务端最新一致（且本地文件与基线一致）
        ahead     本地有尚未上传的改动（本地基线就是服务端最新）
        behind    服务端有新提交，本地无改动
        diverged  双方都有新改动，或基线已不在服务端历史中
        unknown   无法判定（历史过深、远端提交不可读等）
        """
        token = self._require_token()
        remote = self._remote_head(token)
        base = self.base_commit
        if remote is None:
            return "same" if base is None else "unknown"
        remote_id = str(remote.get("id") or "")
        if not remote_id:
            return "unknown"
        if base is None:
            return "behind"
        if base == remote_id:
            local = self._local_manifest()
            if local is None:
                return "same"
            files = self._commit_files(base, token)
            if files is None:
                return "unknown"
            return "same" if _manifest_matches(local, files) else "ahead"
        limit = max(1, min(int(self.config.max_chain_depth), 100))
        chain = self._recent_commits(token, limit)
        ids = [str(item.get("id") or "") for item in chain]
        if base in ids:
            local = self._local_manifest()
            if local is None:
                return "behind"
            files = self._commit_files(base, token)
            if files is None:
                return "unknown"
            return "behind" if _manifest_matches(local, files) else "diverged"
        if len(chain) >= limit:
            return "unknown"
        return "diverged"

    # -- 内部：远端读取 ---------------------------------------------------

    def _remote_head(self, token: str) -> dict[str, Any] | None:
        status, _, payload = self._http.request(
            "GET", self.LATEST_PATH, token=token, expected=(200,), allow_404=True
        )
        if status == 404:
            return None
        data = _parse_json_response(payload, "最新提交响应")
        if not isinstance(data, dict):
            raise SyncError("最新提交响应格式不正确", code="bad_response")
        return data

    def _recent_commits(self, token: str, limit: int) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        _, _, payload = self._http.request(
            "GET", f"{self.COMMITS_PATH}?limit={limit}", token=token, expected=(200,)
        )
        data = _parse_json_response(payload, "提交列表响应")
        if not isinstance(data, dict) or not isinstance(data.get("commits"), list):
            raise SyncError("提交列表响应格式不正确", code="bad_response")
        return [item for item in data["commits"] if isinstance(item, dict)]

    def _fetch_commit(self, commit_id: str, token: str) -> dict[str, Any]:
        if not isinstance(commit_id, str) or not re.fullmatch(r"[0-9a-fA-F]{16,64}", commit_id):
            raise InvalidCommitError(f"提交 id 格式不正确：{commit_id!r}")
        status, _, payload = self._http.request(
            "GET",
            f"{self.COMMITS_PATH}/{commit_id}",
            token=token,
            expected=(200,),
            allow_404=True,
        )
        if status == 404:
            raise InvalidCommitError(f"服务端没有提交 {commit_id}")
        data = _parse_json_response(payload, "提交详情响应")
        if not isinstance(data, dict) or not isinstance(data.get("files"), list):
            raise InvalidCommitError(f"提交 {commit_id} 的详情不完整")
        return data

    def _commit_files(self, commit_id: str, token: str) -> list[dict[str, Any]] | None:
        try:
            commit = self._fetch_commit(commit_id, token)
        except SyncError:
            return None
        return [item for item in commit["files"] if isinstance(item, dict)]

    # -- 内部：本地清单 ---------------------------------------------------

    def _local_manifest(self) -> dict[str, tuple[str, int]] | None:
        if not self.data_dir.is_dir() or not (self.data_dir / SYNC_FILE).is_file():
            return None
        try:
            return collect_local_manifest(self.data_dir)
        except SyncError:
            return None

    def _local_matches(self, files: Any) -> bool:
        local = self._local_manifest()
        if local is None:
            return False
        return _manifest_matches(local, files)


# --- 以下：SyncClient push / pull / 恢复 ---


    # -- 上传 -------------------------------------------------------------

    def push(self, note: str | None = None, op_id: str | None = None) -> PushResult:
        """上传本地快照：先补齐服务端缺失的 blob，再以本地基线为 parent 提交。

        - 快照不稳定（写者正在编辑）自动重试若干次，仍不稳定抛 SnapshotUnstable；
        - 敏感内容检查在任何网络请求之前完成，命中抛 UnsafeLibraryError；
        - 服务端 parent 过期抛 ConflictError（remote_head 为服务端当前 head）。
          此时本方法不会产生服务端提交；先前上传的 blob 可能成为孤儿（服务端无害）。
        - op_id 由调用方指定时可实现幂等重放：同一 opId 且负载相同，服务端返回
          replay=true 的原提交。
        """
        token = self._require_token()
        lock = self._acquire_sync_lock()
        try:
            self._recover_interrupted()
            snapshot = self._snapshot_with_retry()
            try:
                manifest = snapshot.manifest()
                self._check_manifest(manifest)
                findings = find_unsafe_content(snapshot.root)
                if findings:
                    raise UnsafeLibraryError(findings)
                library = self._validate_staged_library(snapshot.root, manifest)
                count = _deduplicate_notes(snapshot.root, library, {item["path"]: item["sha256"] for item in manifest})
                if count:
                    index = snapshot.root / SYNC_FILE
                    snapshot.entries = tuple(
                        SnapshotEntry(SYNC_FILE, _sha256_file(index), index.stat().st_size)
                        if entry.path == SYNC_FILE else entry for entry in snapshot.entries
                    )
                    manifest = snapshot.manifest()
                parent = self.base_commit
                version_note = str(note)[:500] if note else ""
                seed = json.dumps([self.device_id, parent, manifest, version_note], ensure_ascii=False, separators=(",", ":"))
                resolved_op = self._resolve_op_id(op_id if op_id is not None else
                                                  hashlib.sha256(seed.encode("utf-8")).hexdigest())
                if op_id is None and parent is not None:
                    head = self._remote_head(token)
                    if head and head.get("id") == parent:
                        cloud_files = head.get("files")
                        if not isinstance(cloud_files, list):
                            cloud_files = self._commit_files(parent, token)
                    else:
                        cloud_files = None
                    if _manifest_matches(
                        {entry.path: (entry.sha256, entry.size) for entry in snapshot.entries}, cloud_files
                    ):
                        return PushResult(parent, head.get("parent"), str(head.get("at") or ""),
                                          str(head.get("device") or ""), len(manifest), snapshot.total_bytes,
                                          False, resolved_op, deduplicated=count, unchanged=True)
                uploaded = 0
                existed = 0
                sent = set()
                path_map = snapshot.path_map()
                for entry in snapshot.entries:
                    if entry.sha256 in sent:
                        continue
                    sent.add(entry.sha256)
                    blob_status = self._upload_blob(entry, path_map[entry.path], token)
                    if blob_status == 201:
                        uploaded += 1
                    else:
                        existed += 1
                body: dict[str, Any] = {
                    "parent": parent,
                    "opId": resolved_op,
                    "files": manifest,
                }
                if note:
                    body["note"] = str(note)[:500]
                try:
                    _, _, payload = self._http.request(
                        "POST",
                        self.COMMITS_PATH,
                        token=token,
                        json_body=body,
                        expected=(200, 201),
                    )
                except SyncError as exc:
                    conflict = self._commit_conflict(exc)
                    if conflict is not None:
                        raise conflict from exc
                    raise
                data = _parse_json_response(payload, "提交响应")
                if not isinstance(data, dict) or not isinstance(data.get("id"), str) or not data["id"]:
                    raise SyncError("提交响应缺少 id", code="bad_response")
                commit_id = str(data["id"])
                parent_value = data.get("parent")
                self._update_credential(
                    {"baseCommit": commit_id, "lastSyncAt": _utc_now_iso(), "app": APP_NAME}
                )
                result = PushResult(
                    commit_id=commit_id,
                    parent=parent_value if isinstance(parent_value, str) else None,
                    at=str(data.get("at") or ""),
                    device=str(data.get("device") or self.device_id or ""),
                    file_count=int(data.get("fileCount") or len(manifest)),
                    bytes=int(data.get("bytes") or snapshot.total_bytes),
                    replay=bool(data.get("replay")),
                    op_id=resolved_op,
                    uploaded=uploaded,
                    existed=existed,
                    deduplicated=count,
                )
                LOGGER.info(
                    "push 完成：commit=%s files=%d uploaded=%d existed=%d replay=%s",
                    result.commit_id,
                    result.file_count,
                    result.uploaded,
                    result.existed,
                    result.replay,
                )
                return result
            finally:
                snapshot.cleanup()
        finally:
            self._release_sync_lock(lock)

    def _snapshot_with_retry(self, attempts: int = 4) -> Snapshot:
        last: SnapshotUnstable | None = None
        for index in range(attempts):
            try:
                if self.library is not None:
                    return build_snapshot(self.library)
                return build_snapshot(self.data_dir)
            except SnapshotUnstable as exc:
                last = exc
                if index + 1 < attempts:
                    LOGGER.warning("生成快照时库文件被修改，第 %d 次重试：%s", index + 1, exc)
                    time.sleep(0.2 * (index + 1))
        raise SnapshotUnstable("笔记库持续被修改，无法生成一致快照，请稍后重试") from last

    def _check_manifest(self, manifest: list[dict[str, Any]]) -> None:
        if not manifest:
            raise SyncError("快照为空，拒绝提交", code="empty_snapshot")
        encoded = len(json.dumps(manifest, ensure_ascii=False).encode("utf-8"))
        if encoded > self.config.max_manifest_bytes:
            raise SyncError(
                f"提交清单过大（{encoded} 字节，上限 {self.config.max_manifest_bytes}）",
                code="manifest_too_large",
            )
        for item in manifest:
            size = item.get("size")
            if isinstance(size, int) and size > self.config.max_file_bytes:
                raise TooLargeError(
                    f"文件 {item.get('path')} 大小 {size} 字节，超过单文件上限 {self.config.max_file_bytes}",
                    code="file_too_large",
                )

    def _resolve_op_id(self, op_id: str | None) -> str:
        if op_id is None:
            return "op-" + secrets.token_hex(16)
        text = str(op_id).strip()
        if not _OP_ID_RE.fullmatch(text):
            raise SyncError("opId 必须是 8-64 位 [A-Za-z0-9_-] 字符", code="bad_op_id")
        return text

    def _upload_blob(self, entry: SnapshotEntry, source: Path, token: str) -> int:
        try:
            payload = source.read_bytes()
        except OSError as exc:
            raise SyncError(f"读取待上传文件失败：{source}", code="read_failed") from exc
        if len(payload) > self.config.max_file_bytes:
            raise TooLargeError(
                f"文件 {entry.path} 大小 {len(payload)} 字节，超过单文件上限 {self.config.max_file_bytes}",
                code="file_too_large",
            )
        actual = hashlib.sha256(payload).hexdigest()
        if actual != entry.sha256:
            raise SnapshotUnstable(f"快照文件在复制后被改动：{entry.path}")
        status, _, _ = self._http.request(
            "PUT",
            f"{self.BLOBS_PATH}/{entry.sha256}",
            token=token,
            raw_body=payload,
            content_type=_OCTET_STREAM,
            expected=(200, 201),
        )
        return status

    def _commit_conflict(self, error: SyncError) -> ConflictError | None:
        """把服务端 409 错误翻译成带语义的 ConflictError；非 409 返回 None。"""
        if error.status != 409:
            return None
        code = getattr(error, "code", None)
        detail = getattr(error, "detail", None)
        if code == "parent_mismatch":
            remote_head = None if detail in (None, "", "null") else str(detail)
            suffix = f"（远端 head {remote_head}）" if remote_head else "（远端是空库）"
            return ConflictError(
                "本地基线已过期：服务端已有新提交" + suffix + "，请先 pull 再 push",
                remote_head=remote_head,
                code=code,
                status=409,
            )
        if code == "op_id_reused":
            return ConflictError(
                "opId 已被用于另一份提交内容，请换一个 opId",
                code=code,
                status=409,
                reused_op_id=True,
            )
        if code in ("missing_blob", "blob_size_mismatch"):
            return ConflictError(
                "提交引用的 blob 在服务端缺失或大小不符，可重试 push",
                code=code,
                status=409,
            )
        return ConflictError("提交被服务端拒绝：" + str(error), code=code, status=409)


# --- 以下：SyncClient pull / 恢复 / 锁 ---


    # -- 下载与切换 -------------------------------------------------------

    def pull(self, commit_id: str | None = None, *, maintenance: bool = False,
             before_apply=None, expected_local=None) -> PullResult:
        """下载目标提交并原子切换本地库。

        commit_id 为 None 时拉取服务端最新提交；指定 id 时等价于恢复历史版本。
        切换前会把旧库整体备份到 data_dir 同级的 .sync-backup-<时间戳> 目录；
        若目标应用正在写（检测到锁文件）或 maintenance=True，则拒绝切换。
        下载/校验全部在暂存目录完成，任何一步失败都不会改动当前库。
        """
        token = self._require_token()
        lock = self._acquire_sync_lock()
        try:
            self._recover_interrupted()
            self._ensure_not_busy(maintenance)
            target_id = commit_id
            if target_id is None:
                remote = self._remote_head(token)
                if remote is None:
                    raise SyncError("服务端还没有任何提交可拉取", code="no_commits")
                target_id = str(remote.get("id") or "")
            if not target_id:
                raise InvalidCommitError("目标提交 id 为空")
            commit = self._fetch_commit(target_id, token)
            files = [item for item in commit["files"] if isinstance(item, dict)]
            if not files:
                raise InvalidCommitError(f"提交 {target_id} 的文件清单为空")
            stage = self._stage_dir()
            try:
                downloaded = self._download_commit(stage, files, token)
                library = self._validate_staged_library(stage, files)
                count = _deduplicate_notes(stage, library, {item["path"]: item["sha256"] for item in files})
                if count:
                    index = stage / SYNC_FILE
                    files = [
                        {"path": SYNC_FILE, "sha256": _sha256_file(index), "size": index.stat().st_size}
                        if item["path"] == SYNC_FILE else item for item in files
                    ]
                # Download first, then let the GUI freeze and flush editors before the final check.
                if before_apply is not None:
                    before_apply()
                self._ensure_not_busy(maintenance)
                if expected_local is not None and self._local_manifest() != expected_local:
                    raise SnapshotUnstable("下载期间本地笔记有改动，已保留本地和云端两份。")
                parent_value = commit.get("parent")
                if self._local_matches(files):
                    self._update_credential(
                        {"baseCommit": target_id, "lastSyncAt": _utc_now_iso()}
                    )
                    LOGGER.info("pull 跳过切换：本地库已与提交 %s 一致", target_id)
                    return PullResult(
                        commit_id=target_id,
                        parent=parent_value if isinstance(parent_value, str) else None,
                        device=str(commit.get("device") or ""),
                        file_count=len(files),
                        bytes=int(commit.get("bytes") or downloaded),
                        changed=False,
                        backup_dir=None,
                        downloaded=downloaded,
                    )
                backup = self._backup_current()
                self._swap_in(stage, backup, target_id)
                return PullResult(
                    commit_id=target_id,
                    parent=parent_value if isinstance(parent_value, str) else None,
                    device=str(commit.get("device") or ""),
                    file_count=len(files),
                    bytes=int(commit.get("bytes") or downloaded),
                    changed=True,
                    backup_dir=backup,
                    downloaded=downloaded,
                    deduplicated=count,
                )
            finally:
                shutil.rmtree(str(stage), ignore_errors=True)
        finally:
            self._release_sync_lock(lock)

    def restore_commit(self, commit_id: str) -> PullResult:
        """把本地库恢复为历史提交 commit_id 的内容（只下载，不自动上传）。"""
        if not commit_id:
            raise InvalidCommitError("commit id 不能为空")
        return self.pull(commit_id=commit_id)

    def _stage_dir(self) -> Path:
        parent = self.data_dir.parent
        parent.mkdir(parents=True, exist_ok=True)
        slug = _slug_for(self.data_dir)
        for index in range(10):
            candidate = parent / f".sync-stage-{slug}-{secrets.token_hex(4)}"
            try:
                candidate.mkdir()
            except FileExistsError:
                continue
            return candidate
        raise SyncError("无法创建同步暂存目录", code="stage_failed")

    @staticmethod
    def _path_allowed(rel: str) -> bool:
        if rel in (SYNC_FILE, ".hanlin-portable"):
            return True
        for directory in SYNC_DIRS:
            if rel.startswith(directory + "/"):
                return True
        return False

    def _download_commit(self, stage: Path, files: list[dict[str, Any]], token: str) -> int:
        seen: set[str] = set()
        downloaded: dict[str, tuple[Path, int]] = {}
        total = 0
        for item in files:
            raw_path = item.get("path")
            rel = _normalize_rel_path(str(raw_path or ""))
            if not self._path_allowed(rel):
                raise InvalidCommitError(f"提交包含不允许同步的路径：{rel}")
            lowered = rel.lower()
            if lowered in seen:
                raise InvalidCommitError(f"提交清单含重复路径：{rel}")
            seen.add(lowered)
            sha = item.get("sha256")
            size = item.get("size")
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
                raise InvalidCommitError(f"清单中 {rel} 的 sha256 非法")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise InvalidCommitError(f"清单中 {rel} 的 size 非法")
            if size > self.config.max_file_bytes:
                raise TooLargeError(
                    f"提交包含超大文件 {rel}（{size} 字节），超过本地单文件上限",
                    code="file_too_large",
                )
            destination = _safe_join(stage, rel)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if sha in downloaded:
                source, original_size = downloaded[sha]
                if size != original_size:
                    raise InvalidCommitError(f"相同摘要的文件大小不一致：{rel}")
                shutil.copyfile(source, destination)
            else:
                total += self._http.download_blob(sha, token, destination, expected_size=size)
                downloaded[sha] = destination, size
        return total

    def _validate_staged_library(self, stage: Path, files: list[dict[str, Any]]) -> dict[str, Any]:
        rels = {item.get("path") for item in files if isinstance(item.get("path"), str)}
        if SYNC_FILE not in rels:
            raise InvalidCommitError("提交缺少 library.json，不能作为笔记库恢复")
        library_path = stage / SYNC_FILE
        try:
            payload = library_path.read_bytes()
        except OSError as exc:
            raise InvalidCommitError(f"读取暂存 library.json 失败：{exc}") from exc
        if len(payload) > DEFAULT_MAX_JSON_BYTES:
            raise InvalidCommitError("library.json 过大，拒绝作为笔记库恢复")
        try:
            data = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise InvalidCommitError(f"library.json 无法解析：{exc}") from exc
        if not isinstance(data, dict):
            raise InvalidCommitError("library.json 顶层不是对象")
        notes = data.get("notes")
        topics = data.get("topics")
        if not isinstance(notes, list) or not isinstance(topics, list):
            raise InvalidCommitError("library.json 缺少 notes/topics 列表")
        topic_ids: set[str] = set()
        for topic in topics:
            if not isinstance(topic, dict):
                raise InvalidCommitError("library.json 的 topics 含非对象条目")
            topic_id = topic.get("id")
            if not isinstance(topic_id, str) or not topic_id:
                raise InvalidCommitError("library.json 的主题缺少 id")
            if topic_id in topic_ids:
                raise InvalidCommitError(f"library.json 主题 id 重复：{topic_id}")
            topic_ids.add(topic_id)
        note_ids: set[str] = set()
        for note in notes:
            if not isinstance(note, dict):
                raise InvalidCommitError("library.json 的 notes 含非对象条目")
            note_id = note.get("id")
            if not isinstance(note_id, str) or not re.fullmatch(r"[0-9A-Za-z_-]{1,120}", note_id):
                raise InvalidCommitError(f"library.json 的笔记 id 非法：{note_id!r}")
            if note_id in note_ids:
                raise InvalidCommitError(f"library.json 笔记 id 重复：{note_id}")
            note_ids.add(note_id)
            rel = "notes/" + note_id + ".md"
            if rel not in rels or not (stage / "notes" / (note_id + ".md")).is_file():
                raise InvalidCommitError(f"library.json 引用了缺失的笔记文件：{rel}")
            topic_id = note.get("topic_id")
            if isinstance(topic_id, str) and topic_id and topic_id not in topic_ids:
                raise InvalidCommitError(
                    f"笔记 {note_id} 引用了不存在的主题：{topic_id}"
                )
            images = note.get("images", [])
            if not isinstance(images, list) or any(not isinstance(image, str) for image in images):
                raise InvalidCommitError(f"笔记 {note_id} 的附件清单格式错误")
            for image in images:
                path = image.replace("\\", "/")
                if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", path):
                    continue
                rel = path if path.startswith("attachments/") else "attachments/" + path.rsplit("/", 1)[-1]
                if _normalize_rel_path(rel) not in rels:
                    raise InvalidCommitError(f"笔记 {note_id} 缺少图库附件：{rel}")
        return data


# --- 以下：切换 / 恢复 / 备份 / 锁 ---


    # -- 备份、切换与中断恢复 ----------------------------------------------

    def _backup_current(self) -> Path | None:
        if not self.data_dir.is_dir():
            return None
        parent = self.data_dir.parent
        backup = _unique_path(parent, ".sync-backup-")
        ignore = shutil.ignore_patterns("*.tmp", "*.part", "*.lock", ".hanlin-sync.lock")
        try:
            shutil.copytree(str(self.data_dir), str(backup), ignore=ignore)
        except (OSError, shutil.Error) as exc:
            shutil.rmtree(str(backup), ignore_errors=True)
            raise SyncError(
                f"备份当前笔记库失败，已中止切换（当前库未改动）：{exc}",
                code="backup_failed",
            ) from exc
        LOGGER.info("已备份当前笔记库到 %s", backup)
        return backup

    def _swap_in(self, stage: Path, backup: Path | None, commit_id: str) -> None:
        parent = self.data_dir.parent
        slug = _slug_for(self.data_dir)
        old_dir = parent / f".sync-old-{slug}-{secrets.token_hex(4)}"
        data_existed = self.data_dir.exists()
        state = {
            "phase": "switching",
            "dataDir": str(self.data_dir),
            "stageDir": str(stage),
            "oldDir": str(old_dir) if data_existed else None,
            "backupDir": str(backup) if backup is not None else None,
            "commitId": commit_id,
            "startedAt": _utc_now_iso(),
        }
        self._write_state(state)
        if data_existed:
            try:
                os.replace(str(self.data_dir), str(old_dir))
            except OSError as exc:
                self._remove_state()
                raise SyncError(
                    f"无法移开旧库目录（可能被应用占用）：{exc}",
                    code="swap_failed",
                ) from exc
        try:
            _run_test_hook("after_park")
        except BaseException:
            # 测试注入的模拟崩溃：进程在「旧库已移开、新库未就位」时死掉，
            # 状态文件与 old 目录都留在磁盘上，交由下次同步的 _recover_interrupted 处理。
            raise
        try:
            os.replace(str(stage), str(self.data_dir))
        except OSError as exc:
            self._rollback_park(old_dir if data_existed else None)
            raise SyncError(
                f"无法把新库切换到目标目录：{exc}",
                code="swap_failed",
            ) from exc
        if data_existed:
            try:
                self._merge_preserved(old_dir)
            except BaseException:
                failed = parent / f".sync-failed-{slug}-{secrets.token_hex(4)}"
                try:
                    os.replace(str(self.data_dir), str(failed))
                except OSError:
                    failed = None
                try:
                    os.replace(str(old_dir), str(self.data_dir))
                except OSError as exc:
                    LOGGER.error("回滚失败：无法还原旧库目录 %s（%s）", old_dir, exc)
                if failed is not None:
                    shutil.rmtree(str(failed), ignore_errors=True)
                self._remove_state()
                raise
            shutil.rmtree(str(old_dir), ignore_errors=True)
        try:
            self._update_credential({"baseCommit": commit_id, "lastSyncAt": _utc_now_iso()})
        except SyncError as exc:
            LOGGER.warning("库已切换但基线未写入凭据文件，下次 status 会重新判断：%s", exc)
        self._remove_state()
        LOGGER.info("笔记库已切换到提交 %s（旧库备份：%s）", commit_id, backup)

    def _rollback_park(self, old_dir: Path | None) -> None:
        if old_dir is not None and old_dir.is_dir() and not self.data_dir.exists():
            try:
                os.replace(str(old_dir), str(self.data_dir))
            except OSError as exc:
                LOGGER.error("回滚失败：无法还原旧库目录 %s（%s）", old_dir, exc)
                return
        self._remove_state()

    def _merge_preserved(self, old_dir: Path) -> None:
        """把旧库中不参与同步的本地条目（settings.json、backup/、便携标记等）搬进新库。"""
        if not old_dir.is_dir() or not self.data_dir.is_dir():
            return
        for entry in sorted(old_dir.iterdir(), key=lambda item: item.name):
            name = entry.name
            if name in SYNC_ENTRIES or name in self.config.lock_files:
                continue
            if name.startswith(".sync-") or ".sync-" in name:
                continue
            if name.endswith((".tmp", ".part", ".lock")) or name == ".hanlin-sync.lock":
                continue
            if not (entry.is_dir() or entry.is_file()):
                continue
            target = self.data_dir / name
            if target.exists():
                continue
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    shutil.copytree(entry, target, symlinks=True)
                else:
                    shutil.copy2(entry, target)
            except OSError as exc:
                raise SyncError(
                    f"迁移本地保留项 {name} 失败：{exc}",
                    code="merge_failed",
                ) from exc

    # -- 恢复状态文件 -----------------------------------------------------

    def _state_path(self) -> Path:
        return self.data_dir.parent / f".sync-restore-{_slug_for(self.data_dir)}.json"

    def _write_state(self, state: dict[str, Any]) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        payload = json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8")
        with open(tmp, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp), str(path))

    def _read_state(self) -> dict[str, Any] | None:
        path = self._state_path()
        try:
            payload = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            LOGGER.warning("读取恢复状态文件失败：%s", exc)
            return None
        try:
            data = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise SyncError(f"恢复记录损坏：{path}。请保留现有目录并检查备份。", code="recover_failed") from exc
        if not isinstance(data, dict):
            raise SyncError(f"恢复记录格式错误：{path}", code="recover_failed")
        return data

    def _remove_state(self) -> None:
        try:
            os.unlink(str(self._state_path()))
        except OSError:
            pass

    def recover_local(self) -> None:
        lock = self._acquire_sync_lock()
        try:
            self._recover_interrupted()
        finally:
            self._release_sync_lock(lock)

    def _recover_interrupted(self) -> None:
        state = self._read_state()
        if state is not None:
            self._recover_from_state(state)
        self._cleanup_orphans()

    def _recover_from_state(self, state: dict[str, Any]) -> None:
        if state.get("phase") != "switching":
            self._remove_state()
            return

        def _path_of(key: str) -> Path | None:
            value = state.get(key)
            return Path(value) if isinstance(value, str) and value else None

        data_dir = self.data_dir.resolve()
        old_dir = _path_of("oldDir")
        stage_dir = _path_of("stageDir")
        slug = _slug_for(data_dir)
        for entry, prefix in ((old_dir, f".sync-old-{slug}-"), (stage_dir, f".sync-stage-{slug}-")):
            if entry is not None and (not entry.name.startswith(prefix) or entry.parent.resolve() != data_dir.parent
                                      or entry.resolve() != entry.parent.resolve() / entry.name):
                raise SyncError("恢复记录中的目录与当前笔记库不匹配。", code="recover_failed")
        if old_dir is not None and old_dir.is_dir() and not data_dir.exists():
            LOGGER.warning("检测到中断的库切换，正在还原旧库：%s", old_dir)
            try:
                os.replace(str(old_dir), str(data_dir))
            except OSError as exc:
                raise SyncError(
                    f"恢复中断的同步失败：无法把旧库目录还原到 {data_dir}（{exc}）",
                    code="recover_failed",
                ) from exc
        elif old_dir is not None and old_dir.is_dir() and data_dir.is_dir():
            # 新库已就位但合并/清理尚未完成：补做本地保留项合并后删除旧目录。
            try:
                self._merge_preserved(old_dir)
            except SyncError as exc:
                raise SyncError(f"恢复时迁移本地文件失败，旧库保留在 {old_dir}。", code="recover_failed") from exc
            else:
                shutil.rmtree(str(old_dir), ignore_errors=True)
        if stage_dir is not None:
            shutil.rmtree(str(stage_dir), ignore_errors=True)
        self._remove_state()
        LOGGER.info("已从中断的同步中恢复")

    def _cleanup_orphans(self) -> None:
        parent = self.data_dir.parent
        if not parent.is_dir():
            return
        slug = _slug_for(self.data_dir)
        for pattern in (f".sync-stage-{slug}-*", f".sync-failed-{slug}-*"):
            for path in parent.glob(pattern):
                if path.is_dir():
                    shutil.rmtree(str(path), ignore_errors=True)
        for path in parent.glob(f".sync-old-{slug}-*"):
            if not path.is_dir():
                continue
            if not self.data_dir.is_dir():
                LOGGER.warning("发现残留旧库目录但当前库不存在，已保留等待恢复：%s", path)
                continue
            try:
                self._merge_preserved(path)
            except SyncError as exc:
                LOGGER.warning("清理残留旧库时合并失败：%s", exc)
            else:
                shutil.rmtree(str(path), ignore_errors=True)

    # -- 忙碌检测与同步互斥锁 ----------------------------------------------

    def _ensure_not_busy(self, maintenance: bool) -> None:
        if maintenance:
            raise BusyError(
                "调用方声明应用处于维护写状态，已拒绝切换库目录",
                code="busy",
            )
        for name in self.config.lock_files:
            candidate = self.data_dir / name
            if candidate.exists():
                raise BusyError(
                    f"检测到应用锁文件 {candidate}，请先关闭应用再同步",
                    code="busy",
                )

    def _lock_path(self) -> Path:
        return self.data_dir.parent / f".sync-lock-{_slug_for(self.data_dir)}"

    def _acquire_sync_lock(self) -> Path:
        parent = self.data_dir.parent
        parent.mkdir(parents=True, exist_ok=True)
        path = self._lock_path()
        for attempt in range(2):
            try:
                handle = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError as exc:
                if attempt == 0 and self._lock_is_stale(path):
                    LOGGER.warning("同步锁已过期，接管：%s", path)
                    self._release_sync_lock(path)
                    continue
                raise BusyError(
                    f"另一个同步操作正在进行（锁文件 {path}）",
                    code="locked",
                ) from exc
            try:
                os.write(handle, str(os.getpid()).encode("ascii", "ignore"))
            finally:
                os.close(handle)
            return path
        raise BusyError(f"无法获取同步锁：{path}", code="locked")

    def _release_sync_lock(self, path: Path | None = None) -> None:
        target = path if path is not None else self._lock_path()
        try:
            os.unlink(str(target))
        except OSError:
            pass

    @staticmethod
    def _lock_is_stale(path: Path, max_age: float = 3600.0) -> bool:
        try:
            return (time.time() - path.stat().st_mtime) > max_age
        except OSError:
            return False



def local_restore_pending(root: Path) -> bool:
    return (root.parent / f".sync-restore-{_slug_for(root)}.json").is_file()


def recover_local_restore(root: Path) -> None:
    client = SyncClient(SyncConfig("https://sync.yanghanlin.cn"), data_dir=root)
    client.recover_local()
