"""操作的撤销 / 重做栈。

界面上几乎每个写操作都会先在这里留一条记录 —— 排序方式、主题排版、置顶、
归档、移动、删除、恢复、清空回收站、新建笔记、主题改名…… Ctrl+Z 撤回上一步，
Ctrl+Shift+Z / Ctrl+Y 重做。

正文编辑不经过这里：那由编辑器自己的撤销栈负责（QPlainTextEdit 内置），
主窗口只在焦点不在文本框里时才接管 Ctrl+Z，两套互不打扰。

每条记录保存的是「操作前 / 操作后」的局部快照（`store.Snapshot`，只含受影响的
对象），所以撤销一百次也只是几百条笔记的内存，不会整库复制。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from .store import Snapshot

LIMIT = 60


@dataclass
class Command:
    """一次可撤销的操作。"""

    label: str
    before: Snapshot
    after: Snapshot
    note_ids: set[str] = field(default_factory=set)
    topic_ids: set[str] = field(default_factory=set)
    # 界面状态（排序方式等）：撤销恢复 before，重做恢复 after
    before_state: dict = field(default_factory=dict)
    after_state: dict = field(default_factory=dict)
    # 回滚数据之后调一下，让界面跟着变（参数是「该恢复成哪一份界面状态」）
    on_change: Callable[[dict], None] | None = None


class UndoStack:
    """LIFO 的撤销栈，超过上限就把最早的丢掉。"""

    def __init__(self, lib, limit: int = LIMIT):
        self.lib = lib
        self.limit = limit
        self._done: deque[Command] = deque(maxlen=limit)
        self._undone: list[Command] = []

    # ---------- 查询 ----------
    def can_undo(self) -> bool:
        return bool(self._done)

    def can_redo(self) -> bool:
        return bool(self._undone)

    def undo_label(self) -> str:
        return self._done[-1].label if self._done else ""

    def redo_label(self) -> str:
        return self._undone[-1].label if self._undone else ""

    def depth(self) -> tuple[int, int]:
        return len(self._done), len(self._undone)

    # ---------- 记录 ----------
    def push(self, cmd: Command) -> None:
        if not cmd.note_ids and not cmd.topic_ids and not cmd.before_state:
            return
        if cmd.before.notes == cmd.after.notes and cmd.before.topics == cmd.after.topics:
            if cmd.before_state == cmd.after_state:
                # 什么都没变（比如在对话框里点了取消），不必占一格
                return
        self._done.append(cmd)
        self._undone.clear()

    def clear(self) -> None:
        self._done.clear()
        self._undone.clear()

    # ---------- 撤销 / 重做 ----------
    def undo(self) -> str | None:
        if not self._done:
            return None
        cmd = self._done.pop()
        self.lib.restore(cmd.before, cmd.note_ids, cmd.topic_ids)
        self._undone.append(cmd)
        if cmd.on_change:
            cmd.on_change(cmd.before_state)
        return cmd.label

    def redo(self) -> str | None:
        if not self._undone:
            return None
        cmd = self._undone.pop()
        self.lib.restore(cmd.after, cmd.note_ids, cmd.topic_ids)
        self._done.append(cmd)
        if cmd.on_change:
            cmd.on_change(cmd.after_state)
        return cmd.label
