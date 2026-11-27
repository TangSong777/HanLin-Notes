"""Local note transfer keeps attachment identity and rolls back failed batches."""
import json
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from timodesk.exporter import export_markdown_tree, export_zip
from timodesk.importer import import_export
from timodesk.store import Library


def files(root):
    return {file.relative_to(root).as_posix(): file.read_bytes()
            for file in root.rglob("*") if file.is_file()}


with TemporaryDirectory() as temp:
    root = Path(temp)
    source = Library(root / "source").load()
    topic = source.add_topic("测试")
    for name in ("sample.png", "duplicate.png"):
        source.attachment_path(name).write_bytes(b"new image")
        source.add_note(topic.id, "note", content=f"![图](attachments/{name})", title="图片")
    source.save()
    original_source = files(source.root)
    archive, counts = export_zip(source, root / "archive.zip")
    assert counts["attachments"] == counts["documents"] == 1
    assert files(source.root) == original_source
    md = root / "markdown"
    md.mkdir()
    (md / "README.md").write_text("my previous readme", encoding="utf-8")
    export_markdown_tree(source, md)
    previous = set(files(md))
    export_markdown_tree(source, md)
    assert previous == set(files(md)) and len(list((md / "attachments").iterdir())) == 1
    assert (md / "README.md").read_text(encoding="utf-8") == "my previous readme"

    target = Library(root / "target").load()
    target_topic = target.add_topic("原主题")
    for name in ("duplicate.png", "sample.png"):
        target.attachment_path(name).write_bytes(b"old image")
    old = target.add_note(target_topic.id, "note", content="![原图](attachments/duplicate.png)", title="图片")
    target.save()
    result = import_export(archive, target, mode="merge")
    assert result.notes == result.attachments == 1
    added = next(note for note in target.notes.values() if note.id != old.id)
    assert b"old image" == target.attachment_path("duplicate.png").read_bytes()
    assert "attachments/duplicate-2.png" in target.content(added.id)
    assert b"new image" == target.attachment_path("duplicate-2.png").read_bytes()
    repeated = import_export(archive, target, mode="merge")
    assert repeated.notes == repeated.attachments == 0

    same_id = added.id
    target.set_content(same_id, "本地已编辑")
    target.save()
    conflict = import_export(archive, target, mode="merge")
    assert conflict.notes == 1 and target.content(same_id) == "本地已编辑"
    assert import_export(archive, target, mode="merge").notes == 0
    stable = files(target.root)
    with zipfile.ZipFile(archive) as source_zip:
        members = {name: source_zip.read(name) for name in source_zip.namelist()}
    damaged = root / "damaged.zip"
    attachment = next(name for name in members if name.startswith("attachments/"))
    members[attachment] = b"bad image"
    with zipfile.ZipFile(damaged, "w") as archive_file:
        for name, data in members.items():
            archive_file.writestr(name, data)
    try:
        import_export(damaged, target, mode="replace")
    except ValueError:
        pass
    else:
        raise AssertionError("corrupt attachment passed validation")
    assert files(target.root) == stable

    with zipfile.ZipFile(root / "unsafe.zip", "w") as unsafe:
        unsafe.writestr("data/documents.json", json.dumps({"documents": []}))
        unsafe.writestr("../escape.txt", "must not write")
    try:
        import_export(root / "unsafe.zip", target)
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe path was accepted")
    assert files(target.root) == stable and not (root / "escape.txt").exists()

    import os
    replace_file = os.replace

    def fail_index(src, dst):
        if Path(dst) == target.json_path and Path(src).parent.name == "new":
            raise PermissionError("simulated index write failure")
        return replace_file(src, dst)

    with patch("timodesk.importer.os.replace", fail_index):
        try:
            import_export(archive, target, mode="replace")
        except PermissionError:
            pass
        else:
            raise AssertionError("injected failure did not occur")
    assert files(target.root) == stable
    assert target.content(same_id) == "本地已编辑"

print("PASS 附件按内容去重、同名冲突、重复导出、损坏包与提交失败回滚")
