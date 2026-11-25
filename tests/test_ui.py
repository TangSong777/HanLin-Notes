"""UI 层一致性测试：在编辑器里改字段，确认写回数据层且能保存。"""
import shutil
import sys
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from timodesk.store import Library
from timodesk.ui.main_window import MainWindow
from timodesk.ui.theme import app_qss

ROOT = Path("_ui_tmp")
ok = 0
fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"PASS {name}")
    else:
        fail += 1
        print(f"FAIL {name} {detail}")


def main():
    if ROOT.exists():
        shutil.rmtree(ROOT)
    ROOT.mkdir()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(app_qss())

    lib = Library(ROOT / "lib").load()
    topic = lib.add_topic("UI 测试")
    lib.save()

    w = MainWindow(lib)
    w.resize(1200, 800)
    w.show()
    app.processEvents()

    def select(note):
        w.view = ("topic", topic.id)
        w._reload_notes(select_id=note.id)
        app.processEvents()
        w.editor.set_note(note)
        app.processEvents()

    # ---- 便签：正文写回 ----
    n = lib.add_note(topic.id, "note", "初始内容", None)
    select(n)
    w.editor._content_widget().setPlainText("改过的便签正文")
    app.processEvents()
    w._flush_save()
    check("便签正文写回", lib.content(n.id) == "改过的便签正文", lib.content(n.id))

    # ---- 文章：标题 + 正文 ----
    a = lib.add_note(topic.id, "article", "文章正文", None)
    select(a)
    w.editor.title_edit.setText("新文章标题")
    w.editor._content_widget().setPlainText("文章新正文")
    app.processEvents()
    w._flush_save()
    check("文章标题写回", lib.notes[a.id].title == "新文章标题", lib.notes[a.id].title)
    check("文章正文写回", lib.content(a.id) == "文章新正文", lib.content(a.id))

    # ---- 链接 ----
    lk = lib.add_note(topic.id, "link", "", "链接")
    select(lk)
    w.editor.url_edit.setText("https://timo.idik.net")
    w.editor.link_desc.setPlainText("链接说明")
    app.processEvents()
    w._flush_save()
    check("链接 URL 写回", lib.notes[lk.id].url == "https://timo.idik.net", lib.notes[lk.id].url)
    check("链接说明写回", lib.notes[lk.id].description == "链接说明", lib.notes[lk.id].description)

    # ---- 账号 ----
    ac = lib.add_note(topic.id, "account", "", "账号")
    select(ac)
    w.editor.title_edit.setText("某站")
    w.editor.account_edit.setText("me@example.com")
    w.editor.password_edit.setText("pw123")
    w.editor.remark_edit.setText("这是备注")
    app.processEvents()
    w._flush_save()
    check("账号写回", lib.notes[ac.id].account == "me@example.com", lib.notes[ac.id].account)
    check("密码写回", lib.notes[ac.id].password == "pw123", lib.notes[ac.id].password)
    check("备注写回", lib.notes[ac.id].remark == "这是备注", lib.notes[ac.id].remark)
    check("账号标题写回", lib.notes[ac.id].title == "某站", lib.notes[ac.id].title)

    # ---- 日子 ----
    dt = lib.add_note(topic.id, "date", "", "纪念日")
    select(dt)
    w.editor.date_year.setValue(2027)
    w.editor.date_month.setValue(6)
    w.editor.date_day.setValue(18)
    w.editor.date_type.setCurrentIndex(1)  # 每年重复
    w.editor.date_desc.setPlainText("每年这天")
    app.processEvents()
    w._flush_save()
    d = lib.notes[dt.id].date or {}
    check("日子年份", d.get("year") == 2027, d)
    check("日子月份", d.get("month") == 6, d)
    check("日子日", d.get("dayOfMonth") == 18, d)
    check("日子类型", d.get("type") == 1, d)
    check("日子说明", lib.notes[dt.id].description == "每年这天", lib.notes[dt.id].description)

    # ---- 图库：说明 ----
    g = lib.add_note(topic.id, "gallery", "", None)
    select(g)
    w.editor.gallery_desc.setPlainText("图库的一段说明")
    app.processEvents()
    w._flush_save()
    check("图库说明写回", lib.notes[g.id].description == "图库的一段说明", lib.notes[g.id].description)

    # ---- 置顶按钮 ----
    lib.toggle_pin(g.id)
    check("置顶生效", bool(lib.notes[g.id].pinned_at))

    # ---- 视图切换不崩 ----
    for kind in ("all", "archived", "trash"):
        w.view = (kind, None)
        w._reload_notes()
        app.processEvents()
    check("三种视图切换正常", True)

    # ---- 搜索 ----
    w.view = ("topic", topic.id)
    w._reload_notes()
    app.processEvents()
    w.search.setText("改过的便签")
    w._apply_search()
    app.processEvents()
    check("搜索命中 1 条", w.note_list.count() == 1, w.note_list.count())
    w.search.setText("")
    w._apply_search()
    app.processEvents()

    # ---- 打开文档、返回笔记库、批量点选 ----
    w._reload_notes(select_id=n.id)
    app.processEvents()
    item = w.note_list.currentItem()
    rect = w.note_list.visualItemRect(item)
    QTest.mouseClick(w.note_list.viewport(), Qt.LeftButton, pos=rect.center())
    app.processEvents()
    check("单击卡片打开文档", w.workspace.currentWidget() is w.document_page and w.editor.note.id == n.id)
    QTest.keyClick(w.editor.edit, Qt.Key_F, Qt.ControlModifier)
    check("正文中的 Ctrl+F 打开文内查找", w.editor.find_bar.isVisible())
    QTest.keyClick(w.editor.find_edit, Qt.Key_Escape)
    QTest.mouseClick(w.editor.mode_buttons["preview"], Qt.LeftButton)
    check("阅读模式显示预览", w.editor.preview.isVisible() and not w.editor.edit.isVisible())
    QTest.mouseClick(w.back_button, Qt.LeftButton)
    app.processEvents()
    check("返回保留卡片选择", w.workspace.currentWidget() is w.library_page and w.note_list.currentItem() is item)
    QTest.mouseClick(w.selection_button, Qt.LeftButton)
    for i in (0, 1):
        point = w.note_list.visualItemRect(w.note_list.item(i)).center()
        QTest.mouseClick(w.note_list.viewport(), Qt.LeftButton, pos=point)
    check("整理模式点选两条", len(w.note_list.selectedItems()) == 2 and w.batch_button.isVisible())

    # ---- 保存后重新加载 ----
    lib.save()
    lib2 = Library(ROOT / "lib").load()
    check("重载后正文仍在", lib2.content(n.id) == "改过的便签正文", lib2.content(n.id))
    check("重载后账号仍在", lib2.notes[ac.id].account == "me@example.com")

    w.close()
    print(f"\ntotal: {ok} / {ok + fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
