"""以临时示例笔记库生成 README 截图。"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from PyQt5.QtCore import QPointF, Qt
from PyQt5.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from timodesk.store import Library
from timodesk.ui.main_window import MainWindow
from timodesk.ui.theme import apply_theme
from timodesk.ui.title_bar import app_icon


def demo_map(path: Path) -> str:
    image = QImage(420, 300, QImage.Format_RGB32)
    image.fill(QColor("#efe0ca"))
    paint = QPainter(image)
    paint.setRenderHint(QPainter.Antialiasing)
    water = QPainterPath(QPointF(-17, 238))
    water.cubicTo(73, 175, 138, 249, 222, 157)
    water.cubicTo(310, 63, 342, 145, 445, 37)
    paint.setPen(QPen(QColor("#93aaa2"), 43, Qt.SolidLine, Qt.RoundCap))
    paint.drawPath(water)
    paint.setPen(QPen(QColor("#fffcf5"), 9, Qt.SolidLine, Qt.RoundCap))
    paint.drawLine(26, 90, 359, 241)
    paint.drawLine(55, 28, 222, 229)
    paint.setPen(Qt.NoPen)
    paint.setBrush(QColor("#bd635d"))
    paint.drawEllipse(QPointF(248, 191), 11, 11)
    paint.end()
    if not image.save(str(path)):
        raise OSError(f"无法保存示例图 {path}")
    return str(path)


def make_library(root: Path) -> Library:
    lib = Library(root).load()
    daily = lib.add_topic("生活备忘")
    writing = lib.add_topic("写作手记")
    reading = lib.add_topic("读书与摘录")
    travel = lib.add_topic("周末散步")
    work = lib.add_topic("项目笔记")
    reference = lib.add_topic("参考资料")
    reference.locked = True

    lead = lib.add_note(
        travel.id, "article",
        """## 沿河走到旧书店

出门时还在下雨。走到桥头，云已经散了，水面上的倒影慢慢亮起来。书店二楼换了书架，翻到一本旧游记，扉页上夹着一张旧轮渡票。

### 下次散步

- [x] 走过石桥
- [ ] 记下旧书店的营业时间
- [ ] 在河边找一个读书的位置

> 好天气适合走远一点，雨天也可以只走到桥下。
""",
        title="周末散步手记",
    )
    lib.toggle_pin(lead.id)
    lib.add_note(reading.id, "note", "第三章写到路上的陌生人。那一页想再读一遍，再把两段摘出来。", "读书摘录")
    gallery = lib.add_note(travel.id, "gallery", "左岸路线草图，过桥后沿河向南。", "河岸路线图")
    gallery.description = "从车站走到河岸"
    gallery.images = [lib.import_attachment(demo_map(root / "river-map.png"))]
    gallery.color = "#a3a3a3"
    link = lib.add_note(reference.id, "link", "周六开放时间与借阅说明，收藏作下次参考。", "公共图书馆")
    link.url = "https://example.org/library"
    when = lib.add_note(daily.id, "date", "带上书单和上次的笔记。", "下一次读书会")
    date = datetime.now().date() + timedelta(days=26)
    when.date = {"type": 0, "year": date.year, "month": date.month, "dayOfMonth": date.day, "isLunar": False}
    lib.add_note(work.id, "note", "先整理待核对的条目，再把还没确定的内容列出来。", "新一周的安排")
    account = lib.add_note(reference.id, "account", title="资料库演示账户")
    account.account = "reader@example.org"
    account.password = "example-only"
    account.remark = "示例站点的只读账号"
    lib.add_note(writing.id, "article", "收录那些想写下来、还没来得及展开的段落。", "十一月写作草稿")
    lib.add_note(daily.id, "note", "窗边的热茶、刚出炉的面包，和十分钟没响过的手机。", "早餐记录")
    lib.add_note(travel.id, "note", "从车站南口出发，带伞，沿旧铁路线走到河边。", "周末火车路线")
    lib.add_note(work.id, "note", "合并更新的正文，再统一导出这周记录。", "整理项目文件")
    lib.add_note(reading.id, "note", "补上两本想读的小说，先读薄的一本。", "待读书单")

    for topic, title, body in (
        (daily, "搬家前的清单", "取下窗台上的花盆，把钥匙留给房东。"),
        (writing, "春天的第一篇草稿", "从一封没有寄出的信开始。"),
        (travel, "去年海边路线", "这段路风很大，傍晚去正好。"),
    ):
        lib.archive_note(lib.add_note(topic.id, "note", body, title).id)

    past = datetime.now().astimezone() - timedelta(days=14)
    for day, note in enumerate(lib.notes.values()):
        stamp = (past - timedelta(days=day)).replace(hour=12, minute=0, second=0, microsecond=0).isoformat()
        note.created_at = note.updated_at = stamp
    lib.save()
    return lib


def save(window: MainWindow, name: str) -> None:
    QTest.qWait(250)
    path = ROOT / "docs" / f"screenshot-{name}.png"
    if not window.grab().save(str(path)):
        raise OSError(f"无法保存截图 {path}")
    print(path)


def main() -> None:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    app.setWindowIcon(app_icon())
    apply_theme(app, "light")

    with TemporaryDirectory(prefix="hanlin-notes-screenshots-") as temp:
        window = MainWindow(make_library(Path(temp)))
        window.resize(1280, 830)
        window.show()
        save(window, "main")
        window.set_theme("dark")
        save(window, "dark")
        window.set_theme("light")
        window._open_grid_note(window.note_list.item(0))
        save(window, "editor")
        window.select_nav("archived")
        save(window, "archived")
        window.close()


if __name__ == "__main__":
    main()
