"""动效工具：时长、混色、hover 进度、退化为瞬时。"""
import sys

sys.path.insert(0, ".")
from PyQt5.QtCore import QEvent, QPointF, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import QApplication, QListWidget, QWidget

from timodesk.ui import motion

app = QApplication(sys.argv)
ok = total = 0


def check(name, cond):
    global ok, total
    total += 1
    ok += 1 if cond else 0
    print(("  PASS " if cond else "  FAIL ") + name)


# ---- 1. 基础 ----
check("时长从小到大", motion.FAST < motion.BASE < motion.SLOW)
check("enabled 返回布尔", isinstance(motion.enabled(), bool))

c = motion.mix("#000000", "#ffffff", 0.0)
check("混色 t=0 返回起点", (c.red(), c.green(), c.blue()) == (0, 0, 0))
c = motion.mix("#000000", "#ffffff", 1.0)
check("混色 t=1 返回终点", (c.red(), c.green(), c.blue()) == (255, 255, 255))
c = motion.mix("#000000", "#ffffff", 0.5)
check("混色 t=0.5 取中间", (c.red(), c.green(), c.blue()) == (128, 128, 128))
c = motion.mix("#000000", "#ffffff", 2.0)
check("混色 t 超界被夹住", (c.red(), c.green(), c.blue()) == (255, 255, 255))

# ---- 2. 关掉动画时退化 ----
original = motion.enabled
motion.enabled = lambda: False
check("关掉动画后时长为 0", motion.duration(200) == 0)
w = QWidget()
motion.fade_in(w, 190)
check("关掉动画后不装 effect", w.graphicsEffect() is None)
view = QListWidget()
view.addItems(["一", "二", "三"])
tracker = motion.HoverTracker(view)
tracker._set_row(1)
check("关掉动画时 hover 直接到位", tracker.progress_for(1) == 1.0)
tracker._set_row(-1)
check("关掉动画时离开立即归零", tracker.progress_for(1) == 0.0)
motion.enabled = original

# ---- 3. hover 进度 ----
view2 = QListWidget()
view2.resize(240, 200)
for i in range(4):
    view2.addItem(f"第 {i} 行")
view2.show()
tracker2 = motion.attach_hover(view2)
check("attach_hover 挂在视图上", getattr(view2, "hover", None) is tracker2)
check("重复 attach 返回同一个", motion.attach_hover(view2) is tracker2)
check("初始没有 hover 行", tracker2.row == -1 and tracker2.progress == 0.0)

rect = view2.visualItemRect(view2.item(2))
ev = QMouseEvent(QEvent.MouseMove, QPointF(rect.center()), Qt.NoButton, Qt.NoButton, Qt.NoModifier)
QApplication.sendEvent(view2.viewport(), ev)
check("鼠标进入记录了行号", tracker2.row == 2)
check("新行从 0 开始", tracker2.progress <= 0.05 or tracker2.progress == 1.0)

ev2 = QMouseEvent(QEvent.Leave, QPointF(0, 0), Qt.NoButton, Qt.NoButton, Qt.NoModifier)
QApplication.sendEvent(view2.viewport(), ev2)
check("移出后行号清空", tracker2.row == -1)
check("非当前行进度为 0", tracker2.progress_for(0) == 0.0)

view2.close()

print(f"\n{ok}/{total} PASS")
sys.exit(0 if ok == total else 1)
