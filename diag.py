"""诊断脚本：在真实数据副本上检查排序 / 归档 / 回收站 / 字体 的实际行为。

只读检查为主，少量写操作在临时库上进行。
"""
from __future__ import annotations

import shutil
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PyQt5.QtGui import QFont  # noqa: E402
from PyQt5.QtWidgets import QApplication, QPlainTextEdit, QTextBrowser  # noqa: E402

from timodesk.store import Library, compare_zh  # noqa: E402
from timodesk.ui.theme import C, app_qss, available_text_fonts, set_mode, set_text_font  # noqa: E402

OK, BAD = [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(f"{name}  {detail}")
    print(("  ok  " if cond else "  FAIL") + f"  {name}  {detail}")


def fresh(name: str) -> Path:
    dst = Path(name)
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree("testlib", dst)
    return dst


def main():
    app = QApplication([])
    set_mode("light")
    app.setStyleSheet(app_qss())

    lib = Library("testlib").load()
    notes = lib.notes
    print(f"\n库：{len(lib.topics)} 主题 / {len(notes)} 笔记")

    # ---------- 1. 数据层排序 ----------
    print("\n[1] 数据层排序")
    for mode, key, rev in (("created", "created_at", True), ("updated", "updated_at", True)):
        seq = lib.sorted_notes(state="active", order=mode)
        pins = [n for n in seq if n.pinned_at]
        check(f"{mode}: 置顶在最前", all(seq[i].pinned_at for i in range(len(pins))), f"{len(pins)} 条置顶")
        body = [n for n in seq if not n.pinned_at]
        vals = [n.created_at if mode == "created" else (n.updated_at or n.created_at) for n in body]
        check(f"{mode}: 时间单调", vals == sorted(vals, reverse=rev), f"{len(body)} 条")

    seq = lib.sorted_notes(state="active", order="title")
    titles = [n.display_title(lib.content(n.id)) for n in seq if not n.pinned_at]
    import functools as _ft
    check("title: 拼音序升序", titles == sorted(titles, key=_ft.cmp_to_key(compare_zh)), titles[:2])

    # 主题内排序
    tid = lid = None
    for t in lib.sorted_topics():
        if lib.topic_notes(t.id):
            tid = t.id
            break
    if tid:
        a = [n.id for n in lib.topic_notes(tid, order="updated")]
        b = [n.id for n in lib.sorted_notes(state="active", order="updated") if n.topic_id == tid]
        check("主题内排序与全库一致", a == b, f"{len(a)} 条")

    # ---------- 2. 界面排序 ----------
    print("\n[2] 界面排序")
    from timodesk.ui.main_window import MainWindow

    w = MainWindow(lib)
    w.resize(1280, 860)
    w.show()
    app.processEvents()

    def list_ids():
        return [w.note_list.item(i).data(0x0100) for i in range(w.note_list.count())]

    w.select_nav("all")
    app.processEvents()
    ids_updated = list_ids()
    check("全部视图有内容", len(ids_updated) > 0, f"{len(ids_updated)} 条")

    w.set_sort("title")
    app.processEvents()
    ids_title = list_ids()
    check("界面切到标题排序后顺序变化", ids_title != ids_updated, f"{len(ids_title)} 条")
    t_ui = [lib.notes[i].display_title(lib.content(i)) for i in ids_title if not lib.notes[i].pinned_at]
    check("界面标题排序单调", t_ui == sorted(t_ui, key=__import__("functools").cmp_to_key(compare_zh)))

    w.set_sort("created")
    app.processEvents()
    ids_created = list_ids()
    vals = [lib.notes[i].created_at for i in ids_created if not lib.notes[i].pinned_at]
    check("界面创建排序单调", vals == sorted(vals, reverse=True))

    w.set_sort("updated")
    app.processEvents()

    # 搜索结果排序
    w.search.setText("的")
    w._apply_search()
    app.processEvents()
    n_search = w.note_list.count()
    check("搜索有结果", n_search > 0, f"{n_search} 条")
    w.set_sort("title")
    app.processEvents()
    st = [lib.notes[i].display_title(lib.content(i)) for i in list_ids() if not lib.notes[i].pinned_at]
    ck = __import__("functools").cmp_to_key(compare_zh)
    check("搜索结果跟随标题排序", st == sorted(st, key=ck), f"{len(st)} 条")
    w._reset_search()
    w.set_sort("updated")
    app.processEvents()

    # 归档视图 / 回收站视图排序
    for kind in ("archived", "trash"):
        w.select_nav(kind)
        app.processEvents()
        got = list_ids()
        expect = [n.id for n in lib.sorted_notes(state=kind, order="updated")]
        check(f"{kind} 视图内容与数据一致", got == expect, f"{len(got)} vs {len(expect)}")

    # ---------- 3. 归档 ----------
    print("\n[3] 归档")
    root = fresh("diag_lib")
    tlib = Library(root).load()
    active = [n for n in tlib.sorted_notes(state="active")]
    victim = active[0]
    tlib.archive_note(victim.id)
    check("归档后状态=archived", tlib.notes[victim.id].state == "archived")
    check("归档后 archived_at 有值", bool(tlib.notes[victim.id].archived_at))
    check("归档后不在全部视图", victim.id not in [n.id for n in tlib.sorted_notes(state="active")])
    check("归档后出现在归档视图", victim.id in [n.id for n in tlib.sorted_notes(state="archived")])
    tlib.archive_note(victim.id, False)
    check("取消归档回到 active", tlib.notes[victim.id].state == "active")

    # 拖到归档主题
    arch_topic = next((t for t in tlib.topics.values() if t.state == "archived"), None)
    if arch_topic:
        moved = active[1]
        tlib.move_notes([moved.id], arch_topic.id)
        check("拖到归档主题后笔记也归档", tlib.notes[moved.id].state == "archived")
        act_topic = next((t for t in tlib.topics.values() if t.state == "active" and not t.deleted_at), None)
        tlib.move_notes([moved.id], act_topic.id)
        check("从归档主题拖回后恢复", tlib.notes[moved.id].state == "active")

    # 归档主题里新建笔记
    w2 = MainWindow(Library("diag_lib").load())
    w2.show()
    app.processEvents()
    if arch_topic:
        w2.view = ("topic", arch_topic.id)
        w2._reload_notes()
        app.processEvents()
        before = len(w2.lib.notes)
        w2.new_note("note")
        app.processEvents()
        created = [n for n in w2.lib.notes.values() if len(w2.lib.notes) > before and n.topic_id == arch_topic.id]
        newn = max(w2.lib.notes.values(), key=lambda n: n.created_at or "")
        check("归档主题内新建的笔记状态=archived", newn.topic_id == arch_topic.id and newn.state == "archived",
              f"state={newn.state}")
    w2.close()

    # ---------- 4. 回收站 ----------
    print("\n[4] 回收站")
    victim = active[2]
    tlib.delete_note(victim.id)
    check("删除后状态=trash", tlib.notes[victim.id].state == "trash")
    check("删除后不在全部视图", victim.id not in [n.id for n in tlib.sorted_notes(state="active")])
    check("删除后出现在回收站", victim.id in [n.id for n in tlib.sorted_notes(state="trash")])
    tlib.restore_note(victim.id)
    check("恢复后回到 active", tlib.notes[victim.id].state == "active")

    # 归档的笔记删了再恢复，应回到 archived
    arch_note = tlib.sorted_notes(state="archived")[0]
    tlib.delete_note(arch_note.id)
    tlib.restore_note(arch_note.id)
    check("归档笔记删除后恢复仍是 archived", tlib.notes[arch_note.id].state == "archived",
          f"state={tlib.notes[arch_note.id].state}")

    # 彻底删除
    gone = tlib.sorted_notes(state="trash")[0]
    md = root / "notes" / f"{gone.id}.md"
    had_md = md.exists()
    tlib.delete_note(gone.id, hard=True)
    check("彻底删除后从数据里消失", gone.id not in tlib.notes)
    check("彻底删除后 md 也删掉", not md.exists() if had_md else True)

    # 清空回收站
    n_trash = len(tlib.sorted_notes(state="trash"))
    tlib.empty_trash() if hasattr(tlib, "empty_trash") else None
    left = len(tlib.sorted_notes(state="trash"))
    check("清空回收站后为空", left == 0, f"{n_trash} → {left}")

    # 主题回收站
    doomed = next(t for t in tlib.topics.values() if t.state == "active" and not t.deleted_at
                  and tlib.topic_note_count(t.id) > 0)
    keep = tlib.topic_note_count(doomed.id)
    tlib.delete_topic(doomed.id)
    check("删主题后主题进回收站", tlib.topics[doomed.id].state == "trash")
    check("删主题后其笔记进回收站",
          all(tlib.notes[n.id].state == "trash" for n in tlib.notes.values() if n.topic_id == doomed.id),
          f"{keep} 条")
    stamp = tlib.topics[doomed.id].deleted_at
    followed = [n.id for n in tlib.notes.values() if n.topic_id == doomed.id and n.deleted_at == stamp]
    tlib.restore_topic(doomed.id)
    check("恢复主题后主题恢复", tlib.topics[doomed.id].state == "active")
    check("恢复主题后当初一起删的笔记恢复",
          all(tlib.notes[i].state != "trash" for i in followed),
          f"{len(followed)} 条")

    # ---------- 5. 字体 ----------
    print("\n[5] 字体")
    print("  可用字体:", available_text_fonts())
    for label, fam in available_text_fonts():
        got = set_text_font(fam)
        check(f"set_text_font({label}) 生效", got == fam, f"{got}")
        app.setStyleSheet(app_qss())
        w.set_theme(w.settings.get("theme_mode", "light"))
        w.apply_font(fam)
        app.processEvents()
        w.note_list.setCurrentRow(0)
        app.processEvents()
        hints = {}
        if w.note_list.count():
            hints["delegate"] = w.note_list.itemDelegate().font().family()
        hints["list"] = w.note_list.font().family()
        hints["editor"] = w.editor.edit.font().family()
        hints["preview"] = w.editor.preview.font().family()
        hints["topic_list"] = w.topic_list.font().family()
        vp = w.note_list.viewport()
        del vp
        wrong = {k: v for k, v in hints.items() if v != fam}
        check(f"{label}: 各控件字体一致", not wrong, f"{hints} 不一致={wrong}")

    # ---------- 6. 去重口径 ----------
    print("\n[6] 去重口径")
    groups = lib.duplicate_groups()
    bad = []
    for g in groups:
        keys = set()
        for n in g:
            keys.add((n.type, n.title or "", tuple(n.images or []), n.url or "", n.account or ""))
        if len(keys) > 1:
            bad.append((g[0].type, len(g), len(keys)))
    check("重复组内笔记完全同类同标题", not bad, f"{len(groups)} 组，其中 {len(bad)} 组只正文相同")

    print("\n" + "=" * 60)
    print(f"通过 {len(OK)} 项，失败 {len(BAD)} 项")
    for b in BAD:
        print("  FAIL:", b)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
