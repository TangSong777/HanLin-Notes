"""主题归档、侧栏筛选与旧笔记状态的回归。"""
from tempfile import TemporaryDirectory

from PyQt5.QtCore import QPoint, QTimer, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from timodesk.store import Library
from timodesk.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


def visible(window):
    return {window.topic_list.item(i).data(Qt.UserRole).id for i in range(window.topic_list.count())}


def choose_menu(label):
    def choose():
        menu = app.activePopupWidget()
        action = next((item for item in menu.actions() if item.text() == label), None)
        if action is None:
            menu.close()
            return
        menu.setActiveAction(action)
        QTest.keyClick(menu, Qt.Key_Return)

    QTimer.singleShot(30, choose)


with TemporaryDirectory() as root:
    lib = Library(root).load()
    topic = lib.add_topic("日常")
    legacy = lib.add_topic("已归档主题")
    lib.archive_topic(legacy.id)
    active = lib.add_note(topic.id)
    archived = lib.add_note(topic.id)
    lib.archive_note(archived.id)
    trashed = lib.add_note(topic.id)
    lib.delete_note(trashed.id)
    topic.locked = True  # 旧版保存的锁定状态不影响新的归档入口。
    lib.save()

    window = MainWindow(lib)
    window.show()
    app.processEvents()
    assert visible(window) == {topic.id}
    choose_menu("归档主题")
    window._topic_menu(window.topic_list.visualItemRect(window.topic_list.item(0)).center())
    assert visible(window) == set() and topic.state == "archived"
    assert active.state == "archived" and active.archived_at is None
    assert archived.state == "archived" and archived.archived_at
    assert trashed.state == "trash"

    choose_menu("只显示已归档主题")
    window._topic_sort_at(window.mapToGlobal(QPoint(80, 80)))
    assert visible(window) == {topic.id, legacy.id}
    assert window.settings.get("topic_filter") == "archived"

    item = next(
        window.topic_list.item(i)
        for i in range(window.topic_list.count())
        if window.topic_list.item(i).data(Qt.UserRole).id == topic.id
    )
    choose_menu("取消归档主题")
    window._topic_menu(window.topic_list.visualItemRect(item).center())
    assert visible(window) == {legacy.id}
    assert topic.state == "active" and active.state == "active"
    assert archived.state == "archived" and trashed.state == "trash"

    window.undo()
    assert visible(window) == {topic.id, legacy.id}
    assert lib.notes[active.id].state == "archived"
    window.redo()
    assert visible(window) == {legacy.id}
    assert lib.notes[active.id].state == "active"
    assert Library(root).load().notes[archived.id].state == "archived"
    reopened = MainWindow(Library(root).load())
    assert visible(reopened) == {legacy.id}
    window.close()
    reopened.close()
print("PASS 默认筛选、右键归档、归档筛选记忆、取消归档与撤销重做")
