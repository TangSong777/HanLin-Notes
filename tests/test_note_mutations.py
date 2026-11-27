"""Delete and move notes without losing edits, scroll position or trash state."""
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from timodesk.store import Library
from timodesk.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])

with TemporaryDirectory() as root:
    lib = Library(root).load()
    source = lib.add_topic("项目")
    archive = lib.add_topic("归档")
    target = lib.add_topic("资料")
    lib.archive_topic(archive.id)
    notes = [lib.add_note(source.id, content=str(i)) for i in range(100)]
    window = MainWindow(lib)
    window.show()
    app.processEvents()
    bar = window.note_list.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    scroll = bar.value()
    assert scroll > 0
    note_id = window.note_list.item(35).data(Qt.UserRole)
    topic_note = lib.notes[note_id]

    window._pending_save = (note_id, "最新正文", "")
    with patch.object(lib, "set_content", side_effect=OSError("模拟无法写入正文")):
        assert not window._bulk_apply([note_id], "delete")
        assert topic_note.state == "active"
        assert window._pending_save == (note_id, "最新正文", "")
    assert window.save_now() and lib.content(note_id) == "最新正文"

    with patch.object(lib, "save", side_effect=OSError("模拟无法写入元数据")):
        assert not window._bulk_apply([note_id], "delete")
        assert topic_note.state == "active"
        assert not window._bulk_apply([note_id], "move", archive.id)
        assert topic_note.topic_id == source.id and topic_note.state == "active"

    with patch.object(lib, "save", wraps=lib.save) as save:
        assert window._bulk_apply([note_id, notes[5].id, note_id], "delete")
        assert save.call_count == 1
    assert bar.value() == scroll
    stamp = topic_note.deleted_at
    assert lib.delete_notes([note_id]) == 0 and topic_note.deleted_at == stamp
    assert lib.move_notes([note_id], target.id) == 0
    window.undo()
    assert lib.notes[note_id].state == "active" and lib.content(note_id) == "最新正文"
    window._on_notes_dropped([note_id], archive.id)
    assert lib.notes[note_id].state == "archived" and lib.notes[note_id].topic_id == archive.id
    window._on_notes_dropped([note_id], target.id)
    assert lib.notes[note_id].state == "active" and lib.notes[note_id].topic_id == target.id

    lib.archive_note(notes[6].id)
    assert lib.move_notes([notes[6].id], target.id) == 1
    assert lib.notes[notes[6].id].state == "archived"
    lib.delete_topic(source.id)
    assert lib.move_notes([note_id], source.id) == 0
    window.close()

print("PASS 删除及移动守住草稿、回收站时间、滚动位置与归档状态")
