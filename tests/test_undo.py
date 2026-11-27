"""撤销 / 重做：排序、归档、回收站、移动、主题操作都要能 Ctrl+Z 撤回。"""
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QPlainTextEdit

from timodesk.store import Library, to_iso
from timodesk.ui.main_window import MainWindow
from timodesk.ui.theme import app_qss

app = QApplication(sys.argv)
app.setStyleSheet(app_qss())

ROOT = Path("worklib_undo")
shutil.rmtree(ROOT, ignore_errors=True)

lib = Library(ROOT).load()
primary = lib.add_topic("Z 测试主题")
lib.add_topic("A 测试主题")
for i in range(12):
    note = lib.add_note(primary.id, title=f"测试笔记 {i:02}", content=f"测试正文 {i}")
    note.created_at = note.updated_at = to_iso(datetime(2026, 1, 1) + timedelta(days=i))
discarded = lib.add_note(primary.id, content="回收站试件")
lib.delete_note(discarded.id)
lib.save()
w = MainWindow(lib)
ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"ok   {name}")
    else:
        fail += 1
        print(f"FAIL {name} {extra}")


def nid(i=0):
    return w.lib.sorted_notes(state="active")[i].id


# ---------- 1 排序方式 ----------
w.view = ("topic", primary.id)
w.set_sort("updated")
before_ids = [n.id for n in w._current_notes()]
w.set_sort("title")
check("切换排序生效", w.sort_mode == "title")
check("排序进撤销栈", w.undo_stack.can_undo())
w.undo()
check("撤销排序回到 updated", w.sort_mode == "updated")
check("撤销后列表也回到原顺序", [n.id for n in w._current_notes()] == before_ids)
w.redo()
check("重做排序", w.sort_mode == "title")
w.undo()
w.view = ("all", None)
w._reload_notes()

# ---------- 2 主题排序 ----------
ids_before = [t.id for t in sorted(w.lib.topics.values(), key=lambda t: t.order) if t.state != "trash"]
w.sort_topics("name")
ids_after = [t.id for t in sorted(w.lib.topics.values(), key=lambda t: t.order) if t.state != "trash"]
check("主题排序改变了顺序", ids_before != ids_after)
w.undo()
ids_undo = [t.id for t in sorted(w.lib.topics.values(), key=lambda t: t.order) if t.state != "trash"]
check("撤销主题排序恢复原顺序", ids_undo == ids_before, f"{ids_undo[:3]} vs {ids_before[:3]}")

# ---------- 3 置顶 ----------
target = nid(1)
was_pinned = bool(w.lib.notes[target].pinned_at)
with w.undoable("置顶测试", note_ids=[target]):
    w.lib.toggle_pin(target)
    w.lib.save()
check("置顶生效", bool(w.lib.notes[target].pinned_at) != was_pinned)
w.undo()
check("撤销置顶", bool(w.lib.notes[target].pinned_at) == was_pinned)

# ---------- 4 归档 ----------
arch = nid(2)
w.lib.archive_note(arch, True)  # 直接改，不进栈（模拟旧状态）
w.lib.save()
with w.undoable("归档测试", note_ids=[arch]):
    w.lib.archive_note(arch, False)
    w.lib.save()
check("取消归档生效", w.lib.notes[arch].state == "active")
w.undo()
check("撤销取消归档回到 archived", w.lib.notes[arch].state == "archived")

# ---------- 5 删除到回收站 ----------
d = nid(3)
with w.undoable("删除测试", note_ids=[d]):
    w.lib.delete_note(d)
    w.lib.save()
check("删除进回收站", w.lib.notes[d].state == "trash")
w.undo()
check("撤销删除回到 active", w.lib.notes[d].state == "active")

# ---------- 6 彻底删除（连正文） ----------
p = nid(4)
with w.undoable("彻底删除测试", note_ids=[p]):
    w.lib.delete_note(p, hard=True)
path = ROOT / "notes" / f"{p}.md"
check("彻底删除删掉元数据", p not in w.lib.notes)
check("彻底删除删掉正文文件", not path.exists())
w.undo()
check("撤销彻底删除恢复元数据", p in w.lib.notes)
check("撤销彻底删除恢复正文文件", path.exists() and path.read_text(encoding="utf-8") is not None)

# ---------- 7 移动 ----------
m = nid(5)
old_topic = w.lib.notes[m].topic_id
others = [t.id for t in w.lib.topics.values() if t.state == "active" and t.id != old_topic]
with w.undoable("移动测试", note_ids=[m]):
    w.lib.move_note(m, others[0])
    w.lib.save()
check("移动生效", w.lib.notes[m].topic_id == others[0])
w.undo()
check("撤销移动回到原主题", w.lib.notes[m].topic_id == old_topic)

# ---------- 8 新建笔记 ----------
count_before = len(w.lib.notes)
with w.undoable("新建测试") as scope:
    n = w.lib.add_note(others[0], "note", "临时内容", None)
    scope.notes.add(n.id)
    w.lib.save()
check("新建笔记生效", len(w.lib.notes) == count_before + 1)
w.undo()
check("撤销新建笔记", len(w.lib.notes) == count_before and n.id not in w.lib.notes)

# ---------- 9 新建主题 ----------
tcount = len(w.lib.topics)
with w.undoable("新建主题测试") as scope:
    t = w.lib.add_topic("撤销用临时主题")
    scope.topics.add(t.id)
    w.lib.save()
check("新建主题生效", len(w.lib.topics) == tcount + 1)
w.undo()
check("撤销新建主题", len(w.lib.topics) == tcount and t.id not in w.lib.topics)

# ---------- 10 清空回收站 ----------
trash_ids = [x.id for x in w.lib.notes.values() if x.state == "trash"]
with w.undoable("清空回收站测试", note_ids=trash_ids):
    w.lib.empty_trash()
check("清空回收站变空", not [x for x in w.lib.notes.values() if x.state == "trash"])
w.undo()
check("撤销清空回收站全部回来", all(i in w.lib.notes for i in trash_ids))

# ---------- 11 空操作不占格子 ----------
depth_before = w.undo_stack.depth()
with w.undoable("什么都没做", note_ids=[nid(0)]):
    pass
check("没变化的操作不记录", w.undo_stack.depth() == depth_before)

# ---------- 12 重做栈被新操作清空 ----------
w.undo()
check("撤销后有重做", w.undo_stack.can_redo())
with w.undoable("新操作", note_ids=[nid(6)]):
    w.lib.toggle_pin(nid(6))
    w.lib.save()
check("新操作清空重做栈", not w.undo_stack.can_redo())

# ---------- 13 上限 ----------
for i in range(80):
    with w.undoable(f"批量{i}", note_ids=[nid(7)]):
        w.lib.toggle_pin(nid(7))
        w.lib.save()
check("撤销栈不超过上限", w.undo_stack.depth()[0] <= w.undo_stack.limit, str(w.undo_stack.depth()))

# ---------- 14 快捷键真的接上了 ----------
w.show()
for _ in range(12):
    app.processEvents()
mode_before = w.sort_mode
other = "title" if mode_before != "title" else "created"
w.set_sort(other)
check("切换排序后栈里有一条", w.undo_stack.can_undo())

# 14a 文本控件里按 Ctrl+Z 只撤销文字，不动操作栈
w._open_document()
for _ in range(10):
    app.processEvents()
edits = [e for e in w.editor.findChildren(QPlainTextEdit) if e.isVisible()]
if edits:
    edit = edits[0]
    edit.setFocus()
    app.processEvents()
    text_before = edit.toPlainText()
    depth_before = w.undo_stack.depth()
    QTest.keyClicks(edit, "KB")
    app.processEvents()
    QTest.keyClick(edit, Qt.Key_Z, Qt.ControlModifier)
    app.processEvents()
    check("编辑器里 Ctrl+Z 撤销文字", edit.toPlainText() == text_before)
    check("编辑器里 Ctrl+Z 不动操作栈", w.undo_stack.depth() == depth_before)
else:
    check("编辑器里 Ctrl+Z 撤销文字", False, "没找到可见编辑框")
    check("编辑器里 Ctrl+Z 不动操作栈", False, "没找到可见编辑框")

# 14b 列表焦点下按 Ctrl+Z 撤销操作
w._show_library()
for _ in range(10):
    app.processEvents()
w.note_list.setFocus()
app.processEvents()
QTest.keyClick(w.note_list, Qt.Key_Z, Qt.ControlModifier)
app.processEvents()
check("列表里 Ctrl+Z 撤销排序", w.sort_mode == mode_before)

# 14c Ctrl+Shift+Z 重做
QTest.keyClick(w.note_list, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
app.processEvents()
check("Ctrl+Shift+Z 重做排序", w.sort_mode == other)

print(f"\n{ok}/{ok + fail} PASS" if not fail else f"\n{ok}/{ok + fail}，{fail} 项失败")
w.close()
app.processEvents()
shutil.rmtree(ROOT, ignore_errors=True)
sys.exit(1 if fail else 0)
