"""Deleting a card close to a column breakpoint must settle the scrollbars."""
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QListView

from timodesk.ui.theme import apply_theme
from timodesk.ui.widgets import AutoFadeScrollBar, NoteList


class GridProbe(NoteList):
    def __init__(self):
        self.layouts = []
        super().__init__(objectName="NoteGrid")

    def update_grid(self):
        super().update_grid()
        self.layouts.append(self.gridSize())


app = QApplication.instance() or QApplication([])
apply_theme(app, "light")
for width in range(1080, 1089):
    grid = GridProbe()
    grid.setVerticalScrollBar(AutoFadeScrollBar(Qt.Vertical, grid))
    grid.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    grid.setFrameShape(grid.NoFrame)
    grid.setViewMode(QListView.IconMode)
    grid.setMovement(QListView.Static)
    grid.setResizeMode(QListView.Adjust)
    grid.setVerticalScrollMode(QListView.ScrollPerPixel)
    grid.setUniformItemSizes(True)
    grid.setLayoutMode(QListView.Batched)
    grid.setBatchSize(80)
    grid.resize(width, 720)
    grid.addItems(f"示例 {i}" for i in range(17))
    grid.show()
    QTest.qWait(55)
    size = grid.gridSize()
    grid.layouts.clear()
    grid.takeItem(0)
    grid.update_grid()
    grid.doItemsLayout()
    QTest.qWait(165)
    assert grid.count() == 16
    assert grid.layouts and all(value == size for value in grid.layouts), (width, grid.layouts)
    assert len(grid.layouts) <= 3, (width, len(grid.layouts))
    grid.close()

print("PASS 删除卡片后列宽和滚动条稳定")
