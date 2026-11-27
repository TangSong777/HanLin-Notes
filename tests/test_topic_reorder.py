"""主题手动排序的交互、动效与筛选状态回归。"""
from tempfile import TemporaryDirectory

from PyQt5.QtCore import QEvent, QPoint, QPointF, QTimer, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from timodesk.store import Library
from timodesk.ui import widgets
from timodesk.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


def move(viewport, point):
    app.sendEvent(viewport, QMouseEvent(QEvent.MouseMove, QPointF(point), Qt.NoButton, Qt.LeftButton, Qt.NoModifier))


def open_manual_sort(window):
    def select_action():
        menu = app.activePopupWidget()
        action = next((item for item in menu.actions() if item.text() == "自定义排序"), None)
        if action is None:
            menu.close()
            return
        menu.setActiveAction(action)
        QTest.keyClick(menu, Qt.Key_Return)

    QTimer.singleShot(30, select_action)
    window._topic_sort_at(window.mapToGlobal(QPoint(80, 80)))


with TemporaryDirectory() as root:
    lib = Library(root).load()
    first = lib.add_topic("第一")
    hidden = lib.add_topic("归档")
    middle = lib.add_topic("第二")
    last = lib.add_topic("第三")
    lib.archive_topic(hidden.id)
    window = MainWindow(lib)
    window.show()
    app.processEvents()
    listing = window.topic_list
    viewport = listing.viewport()
    start, end = (listing.visualItemRect(listing.item(i)).center() for i in (0, 2))
    assert not listing.reordering and not listing.dragEnabled()

    open_manual_sort(window)
    assert listing.reordering and window.btn_finish_topic_sort.isVisible()
    widgets.motion_enabled = lambda: True
    QTest.mousePress(viewport, Qt.LeftButton, pos=start)
    move(viewport, end)
    assert listing._source_row == 0 and listing._target_row == 2
    assert listing._shift_end == {1: -36, 2: -36}
    QTest.qWait(65)
    assert -36 < listing._offset(1) < 0
    assert not viewport.grab().isNull()
    QTest.keyClick(listing, Qt.Key_Escape)
    QTest.mouseRelease(viewport, Qt.LeftButton, pos=end)
    assert [t.id for t in lib.sorted_topics()] == [first.id, middle.id, last.id]

    widgets.motion_enabled = lambda: False
    QTest.mousePress(viewport, Qt.LeftButton, pos=start)
    move(viewport, end)
    assert listing._offset(1) == -36
    QTest.mouseRelease(viewport, Qt.LeftButton, pos=end)
    assert [t.id for t in lib.sorted_topics()] == [middle.id, last.id, first.id]
    assert [t.id for t in sorted(lib.topics.values(), key=lambda t: t.order)] == [
        middle.id, hidden.id, last.id, first.id,
    ]
    assert [t.id for t in Library(root).load().sorted_topics()] == [middle.id, last.id, first.id]
    window.undo()
    assert [t.id for t in lib.sorted_topics()] == [first.id, middle.id, last.id]
    window.redo()
    assert [t.id for t in lib.sorted_topics()] == [middle.id, last.id, first.id]
    window.btn_finish_topic_sort.click()
    assert not listing.reordering and not window.btn_finish_topic_sort.isVisible()

    for number in range(35):
        lib.add_topic(f"测试主题 {number}")
    window.refresh_topics()
    app.processEvents()
    window._set_manual_topic_sort(True)
    start = listing.visualItemRect(listing.item(0)).center()
    edge = QPoint(start.x(), viewport.height() - 2)
    QTest.mousePress(viewport, Qt.LeftButton, pos=start)
    move(viewport, edge)
    QTest.qWait(100)
    assert listing.verticalScrollBar().value() > 0
    QTest.mouseRelease(viewport, Qt.LeftButton, pos=edge)
    assert listing.verticalScrollBar().value() > 0
    window.close()

print("PASS 主题拖动让位、取消、落位、边缘滚动、隐藏主题顺序与撤销重做")
