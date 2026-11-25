"""用临时示例库生成 README 的浅色、深色与归档截图。"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from timodesk.store import Library
from timodesk.ui.main_window import MainWindow
from timodesk.ui.theme import apply_theme
from timodesk.ui.title_bar import app_icon


def make_library(root: Path) -> Library:
    lib = Library(root).load()
    daily = lib.add_topic("日常")
    writing = lib.add_topic("写作")
    reading = lib.add_topic("读书")
    travel = lib.add_topic("旅行")
    work = lib.add_topic("工作")
    reference = lib.add_topic("参考资料")
    reference.locked = True

    lead = lib.add_note(
        daily.id,
        "article",
        """## 沿河走到旧书店

出门时还在下雨。走到桥头，云已经散了，水面上的倒影慢慢亮起来。

书店二楼换了书架。翻到一本旧游记，扉页上有一张上世纪的轮渡票。

### 下次散步

- [x] 走过石桥
- [ ] 记下旧书店的营业时间
- [ ] 在河边找一个读书的位置

> 好天气适合走远一点，雨天也可以只走到桥下。
""",
        title="周末散步手记",
    )
    lib.toggle_pin(lead.id)
    lib.add_note(writing.id, "note", "收录那些想写下来、还没来得及展开的段落。", title="十一月的写作草稿")
    link = lib.add_note(reference.id, "link", title="公共图书馆")
    link.url = "https://example.org/library"
    link.description = "整理开放时间和借阅信息。"
    lib.add_note(reading.id, "note", "第三章写到路上的陌生人。那一页想再读一遍。", title="读书摘录")
    lib.add_note(work.id, "note", "先列出三件最重要的事，下午留一段完整时间画图。", title="新一周安排")
    lib.add_note(travel.id, "note", "从车站往南走，午后去海边。", title="周末火车路线")
    lib.add_note(daily.id, "note", "面包、热茶和窗边的光。", title="早餐记录")
    lib.add_note(reading.id, "note", "补上两本想读的小说。", title="待读书单")

    old_titles = (
        (daily, "搬家前的清单", "最后一天记得取下窗台上的花盆，钥匙留给房东。"),
        (writing, "春天的第一篇草稿", "从一封没有寄出的信开始。"),
        (travel, "去年海边路线", "这段路风很大，傍晚去正好。"),
    )
    for topic, title, body in old_titles:
        note = lib.add_note(topic.id, "note", body, title=title)
        lib.archive_note(note.id)

    past = datetime.now().astimezone() - timedelta(days=14)
    for day, note in enumerate(lib.notes.values()):
        stamp = (past - timedelta(days=day)).replace(hour=12, minute=0, second=0, microsecond=0).isoformat()
        note.created_at = note.updated_at = stamp
    lib.save()
    return lib


def save(window: MainWindow, name: str) -> None:
    window.note_list.scrollToTop()
    QTest.qWait(300)
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
        window.select_nav("all")
        window.note_list.setCurrentRow(0)
        window.note_list.setFocus()
        save(window, "main")
        window.set_theme("dark")
        save(window, "dark")
        window.set_theme("light")
        window.select_nav("archived")
        window.note_list.setCurrentRow(0)
        window.note_list.setFocus()
        save(window, "archived")
        window.close()


if __name__ == "__main__":
    main()
