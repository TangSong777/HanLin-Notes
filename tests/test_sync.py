#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""timodesk.sync 云同步客户端集成测试。

测试自己 spawn 真实的同步服务端（node app/server.mjs），使用临时数据目录与
随机端口，不触碰任何真实用户数据；每个测试用例结束后关闭 node 子进程。

运行方式（在仓库根目录）：
    python tests/test_sync.py
也兼容 unittest 发现：
    python -m unittest tests.test_sync -v
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from timodesk import sync as syncmod
from timodesk.store import Library
from timodesk.sync import (
    AuthError,
    BlobVerifyError,
    BusyError,
    ConflictError,
    CredentialStore,
    PullResult,
    PushResult,
    SyncClient,
    SyncConfig,
    SyncError,
    UnsafeLibraryError,
    build_snapshot,
    collect_local_manifest,
    find_unsafe_content,
    normalize_server_url,
)

SERVER_JS = Path(
    os.environ.get("HANLIN_SYNC_SERVER_JS")
    or r"D:/hanlin-release/hanlin-sync-service/app/server.mjs"
)

LISTEN_RE = re.compile(r"listening on (\d+\.\d+\.\d+\.\d+):(\d+)")


def find_node() -> str:
    candidate = shutil.which("node")
    if candidate:
        return candidate
    for path in (r"D:/nodejs/node.exe", r"C:/Program Files/nodejs/node.exe"):
        if Path(path).is_file():
            return path
    raise RuntimeError("找不到 node 可执行文件，无法启动同步服务端")


class SyncServer:
    """一个临时的同步服务端进程（随机端口 + 临时数据目录）。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.data_root = self.root / "server-data"
        self.node = find_node()
        self.proc: subprocess.Popen | None = None
        self.port: int | None = None
        self.logs: list[str] = []
        self._log_lock = threading.Lock()
        self._ready = threading.Event()

    def env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "HANLIN_SYNC_HOST": "127.0.0.1",
                "HANLIN_SYNC_PORT": "0",
                "HANLIN_SYNC_DATA": str(self.data_root),
                "HANLIN_SYNC_TEST": "1",
                # 测试机磁盘余量不重要，关掉服务端 MIN_FREE 检查，避免 507。
                "HANLIN_SYNC_MIN_FREE": "0",
            }
        )
        return env

    def start(self) -> None:
        if not SERVER_JS.is_file():
            raise RuntimeError("同步服务端入口不存在：" + str(SERVER_JS))
        self.root.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(
            [self.node, str(SERVER_JS)],
            cwd=str(SERVER_JS.parent),
            env=self.env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        reader = threading.Thread(target=self._read_stdout, name="sync-server-log", daemon=True)
        reader.start()
        if not self._ready.wait(30):
            tail = "".join(self.logs[-20:])
            raise RuntimeError("同步服务端启动超时或提前退出：\n" + tail)

    def _read_stdout(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        try:
            for line in self.proc.stdout:
                with self._log_lock:
                    self.logs.append(line)
                    del self.logs[:-500]
                match = LISTEN_RE.search(line)
                if match and not self._ready.is_set():
                    self.port = int(match.group(2))
                    self._ready.set()
        finally:
            self._ready.set()

    @property
    def base_url(self) -> str:
        if self.port is None:
            raise RuntimeError("服务端尚未启动")
        return "http://127.0.0.1:%d" % self.port

    def pair(self, name: str) -> str:
        """用服务端 CLI 生成配对码，返回 code。"""
        result = subprocess.run(
            [self.node, str(SERVER_JS), "--pair", "notes", name],
            cwd=str(SERVER_JS.parent),
            env=self.env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "生成配对码失败（rc=%s）：%s%s"
                % (result.returncode, result.stdout, result.stderr)
            )
        text = result.stdout
        start = text.find("{")
        if start < 0:
            raise RuntimeError("配对命令没有输出 JSON：" + text)
        data, _end = json.JSONDecoder().raw_decode(text[start:])
        code = data.get("code")
        if not isinstance(code, str) or not code:
            raise RuntimeError("配对命令输出缺少 code：" + text)
        return code

    def stop(self) -> None:
        proc = self.proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        if proc is not None and proc.stdout is not None:
            try:
                proc.stdout.close()
            except OSError:
                pass


class SyncTestBase(unittest.TestCase):
    """公共夹具：临时目录、服务端进程、客户端工厂与断言辅助。"""

    def setUp(self) -> None:
        if not SERVER_JS.is_file():
            self.skipTest("同步服务端入口不存在：" + str(SERVER_JS))
        try:
            find_node()
        except RuntimeError as exc:
            self.skipTest(str(exc))
        syncmod._TEST_HOOKS.clear()
        self.tmp = Path(tempfile.mkdtemp(prefix="hanlin-sync-test-"))
        self.server = SyncServer(self.tmp / "server")
        self.server.start()
        self.creds = self.tmp / "creds"
        self.creds.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        syncmod._TEST_HOOKS.clear()
        try:
            self.server.stop()
        finally:
            shutil.rmtree(str(self.tmp), ignore_errors=True)

    # -- 客户端与库 -------------------------------------------------------

    def new_client(self, name: str, *, with_library: bool = True, pair: bool = True):
        """创建独立客户端（各自一个 data 目录与凭据文件），返回 (client, library|None)。"""
        data_dir = self.tmp / name / "Data"
        data_dir.parent.mkdir(parents=True, exist_ok=True)
        config = SyncConfig(
            server_url=self.server.base_url,
            device_name="设备-" + name,
            credential_path=str(self.creds / (name + ".bin")),
            timeout=15.0,
            max_retries=2,
            retry_base_delay=0.01,
            retry_max_delay=0.05,
        )
        if with_library:
            library = Library(str(data_dir))
            library.load()
            library.save()
            client = SyncClient(config, library=library)
        else:
            library = None
            client = SyncClient(config, data_dir=data_dir)
        if pair:
            self._pair_client(client, "设备-" + name)
        return client, library

    def _pair_client(self, client: SyncClient, name: str, attempts: int = 3) -> None:
        """配对。配对码一次性；服务端/CLI 在 Windows 上偶发文件竞争导致 5xx 或
        CLI 非零退出，换新码重试。"""
        last_error: Exception | None = None
        for index in range(attempts):
            try:
                client.pair(self.server.pair(name))
                return
            except (SyncError, RuntimeError) as exc:
                last_error = exc
                if index + 1 >= attempts:
                    raise
                time.sleep(0.2 * (index + 1))
        raise last_error  # pragma: no cover

    def add_note(self, library: Library, title: str, content: str):
        topic = next(iter(library.topics.values()), None)
        if topic is None:
            topic = library.add_topic("默认主题")
        return library.add_note(topic.id, type="note", content=content, title=title)

    def set_note_content(self, library: Library, note, content: str) -> None:
        library.set_content(note.id, content)
        library.update_note(note)

    def reload(self, root: Path) -> Library:
        library = Library(str(root))
        library.load()
        return library

    # -- 服务端状态 -------------------------------------------------------

    def blob_files(self) -> list[Path]:
        root = self.server.data_root / "apps" / "notes" / "blobs"
        if not root.is_dir():
            return []
        return sorted(p for p in root.rglob("*") if p.is_file())

    def commit_files(self) -> list[Path]:
        root = self.server.data_root / "apps" / "notes" / "commits"
        if not root.is_dir():
            return []
        # commits 目录里还有服务端自己的 index.json，不属于提交
        return sorted(p for p in root.glob("*.json") if p.name != "index.json")

    def blob_path(self, sha256_hex: str) -> Path:
        return (
            self.server.data_root
            / "apps"
            / "notes"
            / "blobs"
            / sha256_hex[:2]
            / sha256_hex
        )

    def head_id(self, client: SyncClient) -> str | None:
        remote = client.latest_remote()
        return str(remote["id"]) if remote else None

    def commit_detail(self, client: SyncClient, commit_id: str) -> dict:
        return client._fetch_commit(commit_id, client.token)

    def manifest(self, root: Path) -> dict[str, tuple[str, int]]:
        return collect_local_manifest(root)

    def backup_dirs(self, data_dir: Path) -> list[Path]:
        return sorted(data_dir.parent.glob(".sync-backup-*"))

    def state_path(self, data_dir: Path) -> Path:
        return data_dir.parent / (".sync-restore-" + syncmod._slug_for(data_dir) + ".json")

    def old_dirs(self, data_dir: Path) -> list[Path]:
        return sorted(data_dir.parent.glob(".sync-old-*"))

    def assert_same_content(self, left: Path, right: Path, message: str = "") -> None:
        self.assertEqual(self.manifest(left), self.manifest(right), message)


class TestEmptyLibraryFirstSync(SyncTestBase):
    def test_01_empty_library_first_sync(self):
        """空库首次同步：只上传 library.json，settings.json 与 backup/ 被排除。"""
        a, _library = self.new_client("a01")
        self.assertEqual(a.status(), "same")
        # 本地非同步文件，不应进入快照/提交
        (a.data_dir / "settings.json").write_text('{"theme":"dark"}', encoding="utf-8")
        backup_dir = a.data_dir / "backup"
        backup_dir.mkdir()
        (backup_dir / "old.zip").write_bytes(b"PK\x03\x04fake-zip")

        result = a.push(note="首次同步")
        self.assertFalse(result.replay)
        self.assertEqual(result.file_count, 1)

        head = a.latest_remote()
        self.assertIsNotNone(head)
        self.assertEqual(head["fileCount"], 1)
        detail = self.commit_detail(a, head["id"])
        self.assertEqual([item["path"] for item in detail["files"]], ["library.json"])
        self.assertEqual(len(self.blob_files()), 1)
        self.assertEqual(len(self.commit_files()), 1)
        self.assertEqual(a.status(), "same")

        # 另一台设备的空库拉取：只有同步文件，不带 settings.json / backup
        b, _ = self.new_client("b01")
        pulled = b.pull()
        self.assertTrue(pulled.changed)
        self.assertTrue((b.data_dir / "library.json").is_file())
        self.assertFalse((b.data_dir / "settings.json").exists())
        self.assertFalse((b.data_dir / "backup").exists())
        self.assert_same_content(a.data_dir, b.data_dir)
        self.assertEqual(b.status(), "same")


class TestUploadThenPull(SyncTestBase):
    def test_02_a_upload_b_pull_identical(self):
        """A 上传后 B 拉取，文件逐字节一致；B 再次拉取不切换。"""
        a, lib_a = self.new_client("a02")
        note = self.add_note(lib_a, "第一篇", "你好，云同步。")
        pushed = a.push()
        self.assertEqual(pushed.file_count, len(self.manifest(a.data_dir)))

        b, _ = self.new_client("b02")
        self.assertEqual(b.status(), "behind")
        pulled = b.pull()
        self.assertTrue(pulled.changed)
        self.assertEqual(pulled.commit_id, pushed.commit_id)
        self.assert_same_content(a.data_dir, b.data_dir)

        lib_b = self.reload(b.data_dir)
        self.assertIn(note.id, lib_b.notes)
        self.assertEqual(lib_b.content(note.id), "你好，云同步。")
        self.assertEqual(b.status(), "same")

        # 已一致时再 pull：不切换、不产生新备份
        backups = len(self.backup_dirs(b.data_dir))
        again = b.pull()
        self.assertFalse(again.changed)
        self.assertIsNone(again.backup_dir)
        self.assertEqual(len(self.backup_dirs(b.data_dir)), backups)


class TestPullAfterRemoteChange(SyncTestBase):
    def test_03_b_edit_then_a_pull(self):
        """B 修改并上传后，A 拉取到 B 的内容。"""
        a, lib_a = self.new_client("a03")
        note = self.add_note(lib_a, "共享标题", "原始内容")
        a.push()

        b, _ = self.new_client("b03", with_library=False)
        b.pull()
        lib_b = self.reload(b.data_dir)
        self.set_note_content(lib_b, lib_b.notes[note.id], "B 改过的内容")
        b.push()

        self.assertEqual(a.status(), "behind")
        result = a.pull()
        self.assertTrue(result.changed)
        self.assertEqual(result.commit_id, b.base_commit)
        self.assert_same_content(a.data_dir, b.data_dir)
        self.assertEqual(self.reload(a.data_dir).content(note.id), "B 改过的内容")
        self.assertEqual(a.status(), "same")


class TestDivergedConflict(SyncTestBase):
    def test_04_diverged_conflict(self):
        """双边分叉：push 抛带远端 head 的 ConflictError，pull 后一致且有备份。"""
        a, lib_a = self.new_client("a04")
        note = self.add_note(lib_a, "共同基线", "基线内容")
        a.push()

        b, _ = self.new_client("b04", with_library=False)
        b.pull()

        self.set_note_content(lib_a, note, "A 的版本")
        a.push()
        head_a = a.base_commit
        self.assertIsNotNone(head_a)

        lib_b = self.reload(b.data_dir)
        self.set_note_content(lib_b, lib_b.notes[note.id], "B 的版本")
        self.assertEqual(b.status(), "diverged")

        with self.assertRaises(ConflictError) as ctx:
            b.push()
        self.assertEqual(ctx.exception.remote_head, head_a)
        self.assertEqual(ctx.exception.status, 409)

        result = b.pull()
        self.assertTrue(result.changed)
        self.assertEqual(result.commit_id, head_a)
        self.assert_same_content(a.data_dir, b.data_dir)
        self.assertEqual(self.reload(b.data_dir).content(note.id), "A 的版本")
        self.assertTrue(result.backup_dir is not None and Path(result.backup_dir).is_dir())
        self.assertEqual(b.status(), "same")


class TestStaleHeadSameDevice(SyncTestBase):
    def test_05_stale_head_same_device(self):
        """同设备本地基线过期：status behind，push 抛 ConflictError 且基线不变。"""
        a, lib_a = self.new_client("a05")
        note = self.add_note(lib_a, "基线", "v1")
        a.push()
        head1 = a.base_commit

        b, _ = self.new_client("b05", with_library=False)
        b.pull()
        lib_b = self.reload(b.data_dir)
        self.set_note_content(lib_b, lib_b.notes[note.id], "v2")
        b.push()
        head2 = b.base_commit
        self.assertNotEqual(head1, head2)

        self.assertEqual(a.status(), "behind")
        with self.assertRaises(ConflictError) as ctx:
            a.push()
        self.assertEqual(ctx.exception.remote_head, head2)
        # 冲突后本地基线保持原值，等待 pull
        self.assertEqual(a.base_commit, head1)

        result = a.pull()
        self.assertTrue(result.changed)
        self.assert_same_content(a.data_dir, b.data_dir)
        self.assertEqual(self.reload(a.data_dir).content(note.id), "v2")
        self.assertEqual(a.status(), "same")


class TestOpIdReplay(SyncTestBase):
    def test_06_op_id_replay(self):
        """同 opId 同负载重放返回原提交；同 opId 换负载 409 op_id_reused。"""
        # 用 data_dir 模式：快照不调用 Library.save()，重放时 library.json 不变
        a, _ = self.new_client("a06", with_library=False)
        lib = self.reload(a.data_dir)
        self.add_note(lib, "重放", "内容一")
        before = a.base_commit
        self.assertIsNone(before)

        first = a.push(op_id="replay-op-0001")
        self.assertFalse(first.replay)
        self.assertEqual(len(a.history(100)), 1)

        # 模拟「首次响应丢失」：把本地基线退回到提交前，再用同一 opId 重放
        a._update_credential({"baseCommit": before})
        second = a.push(op_id="replay-op-0001")
        self.assertTrue(second.replay)
        self.assertEqual(second.commit_id, first.commit_id)
        self.assertEqual(len(a.history(100)), 1)
        self.assertEqual(second.uploaded, 0)
        self.assertEqual(second.existed, len(self.manifest(a.data_dir)))

        # 同 opId 换负载 → 409 op_id_reused
        lib2 = self.reload(a.data_dir)
        note = next(iter(lib2.notes.values()))
        self.set_note_content(lib2, note, "内容二")
        with self.assertRaises(ConflictError) as ctx:
            a.push(op_id="replay-op-0001")
        self.assertTrue(ctx.exception.reused_op_id)
        self.assertEqual(len(a.history(100)), 1)


class TestExactDuplicates(SyncTestBase):
    def test_exact_duplicates_are_recoverable_and_keep_one_live_note(self):
        a, _ = self.new_client("exact-a", with_library=False)
        b, _ = self.new_client("exact-b", with_library=False)
        lib = self.reload(a.data_dir)
        topic = lib.add_topic("Same topic")
        separate = lib.add_topic("Different topic")
        first = lib.add_note(topic.id, content="Same body", title="Same title")
        duplicate = lib.add_note(topic.id, content="Same body", title="Same title")
        distinct = lib.add_note(separate.id, content="Same body", title="Same title")
        changed = lib.add_note(topic.id, content="Same body", title="Same title")
        changed.remind_at = "2026-12-01T12:00:00Z"
        lib.save()

        orig = self.manifest(a.data_dir)
        outcome = a.sync_once()
        self.assertEqual(outcome["deduplicated"], 1)
        local = self.reload(a.data_dir)
        self.assertEqual(sum(local.notes[n.id].state == "trash" for n in (first, duplicate)), 1)
        self.assertEqual(local.notes[distinct.id].state, "active")
        self.assertEqual(local.notes[changed.id].state, "active")
        self.assertEqual(local.content(duplicate.id), "Same body")
        self.assertTrue(any(self.manifest(p) == orig for p in a.data_dir.parent.glob(".sync-backup-*")))
        b.pull()
        self.assertEqual(self.manifest(a.data_dir), self.manifest(b.data_dir))
        self.assertEqual(a.sync_once()["state"], "same")
        self.assertEqual(b.sync_once()["state"], "same")
        self.assertEqual(len(a.history(100)), 1)

    def test_normalized_commit_can_be_recovered_after_local_acknowledgement_is_lost(self):
        a, _ = self.new_client("ack-a", with_library=False)
        lib = self.reload(a.data_dir)
        topic = lib.add_topic("Topic")
        lib.add_note(topic.id, content="Copy", title="Same")
        lib.add_note(topic.id, content="Copy", title="Same")
        lib.save()
        first = a.push(note="笔记同步")
        a._update_credential({"baseCommit": None})
        recovered = a.sync_once()
        self.assertEqual(recovered["commit"], first.commit_id)
        self.assertEqual(recovered["state"], "pulled")
        self.assertEqual(len(a.history(100)), 1)

    def test_legacy_remote_duplicate_and_repeated_blob_are_normalized_on_pull(self):
        a, _ = self.new_client("legacy-a", with_library=False)
        b, _ = self.new_client("legacy-b", with_library=False)
        lib = self.reload(a.data_dir)
        topic = lib.add_topic("Topic")
        first = lib.add_note(topic.id, content="Same bytes", title="Same note")
        second = lib.add_note(topic.id, content="Same bytes", title="Same note")
        lib.save()
        with build_snapshot(a.data_dir) as snap:
            for entry in snap.entries:
                a._upload_blob(entry, snap.path_map()[entry.path], a.token)
            a._http.request("POST", a.COMMITS_PATH, token=a.token, expected=(201,),
                            json_body={"parent": None, "opId": "legacy-duplicate",
                                       "files": snap.manifest()})
            unique_bytes = sum({entry.sha256: entry.size for entry in snap.entries}.values())
        result = b.pull()
        self.assertEqual(result.deduplicated, 1)
        self.assertEqual(result.downloaded, unique_bytes)
        restored = self.reload(b.data_dir)
        self.assertEqual(sum(restored.notes[n.id].state == "active" for n in (first, second)), 1)
        self.assertEqual(restored.content(first.id), restored.content(second.id))
        self.assertFalse(b.pull().changed)


class TestUnsafeLibraryBlocked(SyncTestBase):
    def test_07_credential_library_rejected(self):
        """库里有明文密码字段时拒传，且必须在发出任何 blob/commit 之前失败。"""
        a, lib = self.new_client("a07")
        note = self.add_note(lib, "账号", "这是正文")
        note.account = "alice"
        note.password = "S3cr3t-Passw0rd-42"
        lib.update_note(note)

        with self.assertRaises(UnsafeLibraryError) as ctx:
            a.push()
        message = str(ctx.exception)
        self.assertNotIn("S3cr3t-Passw0rd-42", message)
        # 服务端没有收到任何 blob / commit
        self.assertEqual(self.blob_files(), [])
        self.assertEqual(self.commit_files(), [])
        self.assertIsNone(a.latest_remote())
        self.assertEqual(a.status(), "same")


class TestDeleteThenRestore(SyncTestBase):
    def test_08_restore_deleted_note(self):
        """删除笔记上传后，restore_commit 恢复旧版本，且不自动上传。"""
        a, lib = self.new_client("a08")
        note = self.add_note(lib, "会被删除", "删除前的内容")
        a.push()
        c1 = a.base_commit

        lib.delete_note(note.id, hard=True)
        a.push()
        c2 = a.base_commit
        self.assertNotEqual(c1, c2)
        self.assertNotIn(note.id, self.reload(a.data_dir).notes)

        result = a.restore_commit(c1)
        self.assertTrue(result.changed)
        self.assertEqual(result.commit_id, c1)
        self.assertEqual(self.head_id(a), c2)
        restored = self.reload(a.data_dir)
        self.assertIn(note.id, restored.notes)
        self.assertEqual(restored.content(note.id), "删除前的内容")
        self.assertTrue(result.backup_dir is not None and Path(result.backup_dir).is_dir())
        self.assertEqual(a.base_commit, c1)
        self.assertEqual(a.status(), "behind")


class TestCorruptBlobNoSwitch(SyncTestBase):
    def test_09_corrupt_blob_no_switch(self):
        """下载内容 sha 校验失败时不切换本地库、不产生新备份。"""
        a, lib_a = self.new_client("a09")
        note = self.add_note(lib_a, "校验", "v1")
        a.push()
        c1 = a.base_commit

        b, _ = self.new_client("b09", with_library=False)
        b.pull()
        before_manifest = self.manifest(b.data_dir)
        backups_before = len(self.backup_dirs(b.data_dir))

        self.set_note_content(lib_a, note, "v2 会让 blob 损坏")
        a.push()
        head = a.base_commit
        detail = self.commit_detail(a, head)
        rel = "notes/" + note.id + ".md"
        sha = next(item["sha256"] for item in detail["files"] if item["path"] == rel)
        blob = self.blob_path(sha)
        self.assertTrue(blob.is_file())
        original = blob.read_bytes()
        self.assertGreater(len(original), 1)
        # 同长度篡改：sha 不符但 Content-Length 一致
        blob.write_bytes(b"X" + original[1:])

        with self.assertRaises(BlobVerifyError):
            b.pull()

        self.assertEqual(self.manifest(b.data_dir), before_manifest)
        self.assertEqual(len(self.backup_dirs(b.data_dir)), backups_before)
        self.assertEqual(b.base_commit, c1)


class TestInterruptedRestoreRetry(SyncTestBase):
    def test_10_interrupted_restore_retry(self):
        """切换过程中模拟崩溃：旧库被 park、状态文件残留；下次 pull 自动恢复并成功。"""
        a, lib_a = self.new_client("a10")
        note = self.add_note(lib_a, "中断", "v1")
        a.push()
        b, _ = self.new_client("b10", with_library=False)
        b.pull()
        self.set_note_content(lib_a, note, "v2")
        a.push()
        target = a.base_commit

        state_path = self.state_path(b.data_dir)
        syncmod._TEST_HOOKS["after_park"] = lambda: (_ for _ in ()).throw(
            syncmod._SimulatedCrash()
        )
        try:
            with self.assertRaises(BaseException):
                b.pull()
        finally:
            syncmod._TEST_HOOKS.pop("after_park", None)

        # 崩溃现场：状态文件在、data_dir 缺失、旧库被 park
        self.assertTrue(state_path.is_file())
        self.assertFalse(b.data_dir.exists())
        self.assertGreaterEqual(len(self.old_dirs(b.data_dir)), 1)
        backups_after_crash = len(self.backup_dirs(b.data_dir))
        self.assertEqual(backups_after_crash, 1)

        # 重跑：先恢复旧库，再正常拉取
        result = b.pull()
        self.assertTrue(result.changed)
        self.assertEqual(result.commit_id, target)
        self.assertFalse(state_path.exists())
        self.assertEqual(self.old_dirs(b.data_dir), [])
        self.assert_same_content(a.data_dir, b.data_dir)
        self.assertEqual(self.reload(b.data_dir).content(note.id), "v2")
        self.assertEqual(b.status(), "same")


class TestCredentialProtection(SyncTestBase):
    def test_11_credential_no_plaintext(self):
        """凭据文件不落明文 token，可正常读回；DPAPI 不可用时明确告警。"""
        a, _ = self.new_client("c11")
        token = a.token
        self.assertIsNotNone(token)
        path = Path(a.config.credential_path)
        self.assertTrue(path.is_file())
        raw = path.read_bytes()
        self.assertGreater(len(raw), 0)
        self.assertNotIn(token.encode("utf-8"), raw)
        self.assertIn(a.protection, ("dpapi", "file-permissions"))
        if a.protection == "file-permissions":
            self.assertTrue(
                any("未加密" in warning or "文件权限" in warning for warning in a.warnings),
                "退化为仅文件权限保护时必须明确报出：" + repr(a.warnings),
            )
        # 权限收紧可用（Windows 上走 icacls；POSIX 上 chmod）
        self.assertTrue(syncmod._harden_path(path, False))

        store = CredentialStore(str(path))
        data = store.load()
        key = normalize_server_url(self.server.base_url)
        self.assertEqual(data[key]["token"], token)
        self.assertEqual(data[key]["deviceId"], a.device_id)
        self.assertEqual(store.protection, a.protection)

        # 新客户端复用同一凭据文件即可获得同一身份
        config = SyncConfig(server_url=self.server.base_url, credential_path=str(path))
        client2 = SyncClient(config, data_dir=self.tmp / "c11b" / "Data")
        self.assertTrue(client2.paired)
        self.assertEqual(client2.token, token)
        self.assertEqual(client2.device_id, a.device_id)
        self.assertEqual(client2.protection, a.protection)


if __name__ == "__main__":
    unittest.main(verbosity=2)
