"""笔记历史版本：留档、限流、恢复、清理。"""
import shutil
import sys

sys.path.insert(0, ".")
from PyQt5.QtWidgets import QApplication

from timodesk.store import Library
from timodesk.ui.main_window import MainWindow
from timodesk.ui.theme import app_qss

app = QApplication(sys.argv)
app.setStyleSheet(app_qss())

ok = total = 0


def check(name, cond):
    global ok, total
    total += 1
    ok += 1 if cond else 0
    print(("  PASS " if cond else "  FAIL ") + name)


root = "worklib_hist"
shutil.rmtree(root, ignore_errors=True)
lib = Library(root).load()
lib.history.merge = 0  # 测试里关掉合并窗口，方便数版本
t = lib.add_topic("历史")
n = lib.add_note(t.id, "note", "第一版")
nid = n.id

# ---- 1. 留档 ----
lib.set_content(nid, "第二版")
check("改一次正文留一版", lib.history.count(nid) == 1)
v = lib.history.versions(nid)
check("留的是改之前的正文", lib.history.read(nid, v[0].stamp)["content"] == "第一版")

lib.set_content(nid, "第三版")
check("再改一次多一版", lib.history.count(nid) == 2)
latest = lib.history.read(nid, lib.history.versions(nid)[0].stamp)
check("最新的历史是上一版内容", latest["content"] == "第二版")

# ---- 2. 去重：内容没变不留档 ----
before = lib.history.count(nid)
lib.set_content(nid, "第三版")
check("内容没变不留档", lib.history.count(nid) == before)

# ---- 3. 合并窗口 ----
lib.history.merge = 3600
lib.set_content(nid, "第四版")
lib.set_content(nid, "第五版")
# 合并窗口内不新增版本，只把最新那版改成「刚才那一份旧内容」
check("合并窗口内不新增版本", lib.history.count(nid) == 2)
merged = lib.history.read(nid, lib.history.versions(nid)[0].stamp)
check("合并后留下最新的一次旧内容", merged["content"] == "第四版")
lib.history.merge = 0

# ---- 4. 空笔记不留档 ----
e = lib.add_note(t.id, "note", "")
lib.history.capture(e, "")
check("空白不留档", lib.history.count(e.id) == 0)

# ---- 5. 字段也一起存 ----
a = lib.add_note(t.id, "article", "正文")
a.title = "标题甲"
lib.save()
lib.set_content(a.id, "正文改过")
saved = lib.history.read(a.id, lib.history.versions(a.id)[0].stamp)
check("历史里带标题", saved["title"] == "标题甲")

# ---- 6. 版本上限 ----
lib.history.keep = 3
for i in range(6):
    lib.set_content(a.id, f"正文第{i}次")
check("超出上限只留 3 版", lib.history.count(a.id) == 3)
lib.history.keep = 50

# ---- 7. 坏文件不影响读取 ----
d = lib.history.note_dir(nid)
(d / "99991231-235959-000.json").write_text("{坏掉的 json", encoding="utf-8")
check("坏 json 被跳过", all(v.stamp != "99991231-235959-000" for v in lib.history.versions(nid)))

# ---- 8. 界面里恢复 ----
lib.save()
win = MainWindow(lib)
win.select_nav("all")
win.editor.set_note(lib.notes[nid])
stamps = lib.history.versions(nid)
old_content = lib.history.read(nid, stamps[0].stamp)["content"]
win.lib.set_content(nid, old_content)  # 模拟对话框恢复的动作
win.lib.notes[nid].title = "恢复后的标题"
win.lib.save()
check("恢复后正文变回旧版", lib.content(nid) == old_content)

# ---- 9. 彻底删除时清历史 ----
has_before = lib.history.count(nid) > 0
lib.delete_note(nid, hard=True)
check("彻底删除前有历史", has_before)
check("彻底删除后历史目录清空", lib.history.count(nid) == 0)
check("彻底删除后笔记没了", nid not in lib.notes)

# ---- 10. 对话框能构造 ----
from timodesk.ui.dialogs import HistoryDialog

keep_id = a.id
dlg = HistoryDialog(win, lib, keep_id)
check("有历史时列表非空", dlg.list.count() > 0)
check("默认选中第一版", dlg.list.currentRow() == 0)
dlg.deleteLater()

empty_note = lib.add_note(t.id, "note", "没有历史")
dlg2 = HistoryDialog(win, lib, empty_note.id)
check("空历史显示提示", dlg2.empty.isVisible() or dlg2.list.count() == 0)
dlg2.deleteLater()

print(f"\n{ok}/{total} PASS")
sys.exit(0 if ok == total else 1)
