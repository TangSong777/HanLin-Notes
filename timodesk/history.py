"""笔记历史版本。

每次正文改动前，把**旧的那一版**留一份档，于是可以回看「这条笔记以前长什么样、
哪天改过」，也能一键恢复。

存储放在 `Data/history/<笔记 id>/<时间戳>.json`，一条笔记一个目录，一个版本一个文件：

    {"at": "2026-11-26T04:00:00.000Z",   # 这一版是什么时候存的
     "type": "note",                      # 笔记类型
     "title": "标题",                     # 当时的标题
     "content": "正文……",                # 当时的正文
     "fields": {"description": "…", ...}} # 当时的其它字段（只存有值的）

为什么不用 git：
  1. 便携版不能假设目标机器装了 git；
  2. 数据目录要能让用户直接看懂、直接拷走，`.git` 对象库看不懂也不好搬；
  3. 笔记都很小，一份 JSON 就是一份自足的版本，不需要额外的历史命令。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# 一条笔记最多留多少版；超出就删最旧的
KEEP_PER_NOTE = 50
# 同一个字一段时间内的连续保存合并成一版，免得自动保存把历史刷满
MERGE_SECONDS = 90
# 单个版本正文超过这个大小就不留档了（保护磁盘）
MAX_BYTES = 512 * 1024

_FIELD_KEYS = ("description", "url", "account", "password", "remark", "images", "date")


@dataclass
class Version:
    """一份历史版本（不装正文全文，只在需要时读）。"""

    stamp: str
    path: Path
    at: str
    type: str
    title: str
    size: int

    @property
    def when(self) -> float:
        """时间戳（秒），读不出来就当 0。"""
        try:
            return datetime.fromisoformat(self.at.replace("Z", "+00:00")).timestamp()
        except (ValueError, AttributeError):
            return 0.0

    @property
    def chars(self) -> int:
        return self.size


def _stamp(dt: datetime | None = None) -> str:
    dt = dt or datetime.now()
    return dt.strftime("%Y%m%d-%H%M%S") + f"-{dt.microsecond // 1000:03d}"


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.") + f"{datetime.utcnow().microsecond // 1000:03d}Z"


class NoteHistory:
    """`Data/history/` 的读写。"""

    def __init__(self, root: str | os.PathLike, keep: int = KEEP_PER_NOTE, merge: int = MERGE_SECONDS):
        self.root = Path(root)
        self.keep = keep
        self.merge = merge

    @property
    def dir(self) -> Path:
        return self.root / "history"

    def note_dir(self, note_id: str) -> Path:
        return self.dir / note_id

    # ---------- 读 ----------
    def versions(self, note_id: str) -> list[Version]:
        d = self.note_dir(note_id)
        if not d.is_dir():
            return []
        out: list[Version] = []
        for p in d.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.append(
                Version(
                    stamp=p.stem,
                    path=p,
                    at=str(data.get("at") or ""),
                    type=str(data.get("type") or "note"),
                    title=str(data.get("title") or ""),
                    size=len(str(data.get("content") or "")),
                )
            )
        out.sort(key=lambda v: v.stamp, reverse=True)
        return out

    def read(self, note_id: str, stamp: str) -> dict | None:
        p = self.note_dir(note_id) / f"{stamp}.json"
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def count(self, note_id: str) -> int:
        d = self.note_dir(note_id)
        return len(list(d.glob("*.json"))) if d.is_dir() else 0

    # ---------- 写 ----------
    def capture(self, note, content: str) -> bool:
        """给「旧版本」留档。内容与最近一版相同、或太频繁，就不写。

        返回是否真的写了一份。
        """
        text = content or ""
        if len(text.encode("utf-8")) > MAX_BYTES:
            return False
        data = {
            "at": _now_iso(),
            "type": note.type,
            "title": note.title or "",
            "content": text,
            "fields": {},
        }
        for k in _FIELD_KEYS:
            v = getattr(note, k, None)
            if v in (None, "", [], {}):
                continue
            data["fields"][k] = v
        if not text.strip() and not data["title"].strip():
            return False  # 空白笔记不值得留档

        latests = self.versions(note.id)
        if latests:
            newest = latests[0]
            try:
                prev = json.loads(newest.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                prev = {}
            if str(prev.get("content") or "") == text:
                return False  # 内容没变，没必要再来一份
            if time.time() - newest.when < self.merge:
                # 短时间内连续保存：直接覆盖最新那版，避免历史被刷爆
                self._write(newest.path, data)
                return True

        self.note_dir(note.id).mkdir(parents=True, exist_ok=True)
        self._write(self.note_dir(note.id) / f"{_stamp()}.json", data)
        self._prune(note.id)
        return True

    def _write(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8", newline="\n")
        try:
            os.replace(tmp, path)
        except OSError:
            tmp.unlink(missing_ok=True)

    def _prune(self, note_id: str) -> None:
        vs = self.versions(note_id)
        for v in vs[self.keep :]:
            try:
                v.path.unlink()
            except OSError:
                pass

    # ---------- 清 ----------
    def drop(self, note_id: str) -> None:
        """笔记被彻底删除时，历史也一起删掉。"""
        d = self.note_dir(note_id)
        if not d.is_dir():
            return
        for p in d.glob("*"):
            try:
                p.unlink()
            except OSError:
                pass
        try:
            d.rmdir()
        except OSError:
            pass

    def total_size(self) -> int:
        if not self.dir.is_dir():
            return 0
        return sum(p.stat().st_size for p in self.dir.rglob("*.json") if p.is_file())
