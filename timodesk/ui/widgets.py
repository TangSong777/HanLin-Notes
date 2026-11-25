"""自绘列表控件：主题条目与笔记卡片。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import QEasingCurve, QMimeData, QPoint, QRect, QRectF, QSize, Qt, QVariantAnimation, pyqtSignal
from PyQt5.QtGui import (
    QColor,
    QDrag,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt5.QtWidgets import QListView, QListWidget, QListWidgetItem, QScrollBar, QStyledItemDelegate, QStyle

from ..store import TYPE_LABELS
from .theme import C
from .visuals import glyph, motion_enabled


def human_time(iso: str | None) -> str:
    """把 ISO 时间转成「今天 14:03 / 昨天 / 3 天前 / 2024-05-01」。"""
    if not iso:
        return ""
    try:
        txt = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(txt).astimezone()
    except Exception:
        return iso[:10]
    now = datetime.now().astimezone()
    d = (now.date() - dt.date()).days
    if d == 0:
        return dt.strftime("今天 %H:%M")
    if d == 1:
        return dt.strftime("昨天 %H:%M")
    if d < 7:
        return f"{d} 天前"
    if dt.year == now.year:
        return dt.strftime("%m-%d")
    return dt.strftime("%Y-%m-%d")


def _wrap_lines(text: str, fm: QFontMetrics, width: int, max_lines: int) -> list[str]:
    """按像素宽度折行，返回至多 max_lines 行。"""
    text = " ".join(text.split())
    if not text:
        return []
    lines: list[str] = []
    rest = text
    while rest and len(lines) < max_lines:
        lo, hi = 0, len(rest)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if fm.horizontalAdvance(rest[:mid]) <= width:
                lo = mid
            else:
                hi = mid - 1
        if lo == 0:
            lo = 1
        lines.append(rest[:lo])
        rest = rest[lo:]
    if rest and lines:
        last = lines[-1]
        while last and fm.horizontalAdvance(last + "…") > width:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


class NoteCardDelegate(QStyledItemDelegate):
    """笔记列表卡片：类型 + 摘要 + 时间 + 颜色条 + 置顶。"""

    def __init__(self, lib, parent=None):
        super().__init__(parent)
        self.lib = lib
        self.show_topic = False
        self.term = ""  # 搜索词：命中处会在摘要里刷底色
        self._covers: dict[str, QPixmap | None] = {}

    def sizeHint(self, option, index) -> QSize:
        grid = self.parent()
        return grid.gridSize() if isinstance(grid, NoteList) and grid.gridSize().isValid() else QSize(274, 166)

    def _cover(self, note) -> QPixmap | None:
        """图库笔记取第一张图当封面（按笔记 id 缓存，滚动时不重复解码）。"""
        if note.type != "gallery" or not note.images:
            return None
        key = f"{note.id}:{note.images[0]}"
        if key in self._covers:
            return self._covers[key]
        pm = QPixmap()
        try:
            path = self.lib.attachment_abspath(note.images[0])
            if path and Path(path).exists():
                pm = QPixmap(str(path))
        except Exception:
            pm = QPixmap()
        if pm.isNull():
            self._covers[key] = None
            return None
        side = 54 * 2  # 2 倍图，缩放到 54 时更清楚
        pm = pm.scaled(side, side, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        out = QPixmap(54, 54)
        out.fill(Qt.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing, True)
        path_ = QPainterPath()
        path_.addRoundedRect(0, 0, 54, 54, 8, 8)
        p.setClipPath(path_)
        x = (54 - pm.width()) // 2
        y = (54 - pm.height()) // 2
        p.drawPixmap(x, y, pm)
        p.end()
        self._covers[key] = out
        return out

    def paint(self, painter: QPainter, option, index):
        note = self.lib.notes.get(index.data(Qt.UserRole))
        if note is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        grid = self.parent()
        if isinstance(grid, NoteList):
            painter.setOpacity(grid.card_opacity)

        card = QRectF(option.rect).adjusted(5, 3, -8, -11)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 10 if hovered else 5))
        painter.drawRoundedRect(card.translated(0, 2), 11, 11)
        painter.setBrush(QColor(C["card_hover" if hovered and not selected else "card"]))
        painter.setPen(QPen(QColor(C["accent" if selected else "card_border"]), 1.2 if selected else 1))
        painter.drawRoundedRect(card, 11, 11)

        left, top = round(card.left() + 15), round(card.top() + 12)
        right = round(card.right() - 15)
        kind = note.type
        if note.color:
            color = QColor(note.color)
            if color.isValid():
                painter.setPen(Qt.NoPen)
                painter.setBrush(color)
                painter.drawRoundedRect(QRectF(card.left() + 1, top + 31, 3, 34), 1.5, 1.5)
        marker = QRect(left, top, 26, 26)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(C["checked_bg" if selected else "card_soft"]))
        painter.drawRoundedRect(QRectF(marker), 6, 6)
        glyph(kind, "accent_text" if selected else "muted").paint(painter, marker.adjusted(5, 5, -5, -5))

        meta = QFont(option.font)
        meta.setPixelSize(11)
        painter.setFont(meta)
        painter.setPen(QColor(C["muted"]))
        painter.drawText(QRect(left + 33, top, right - left - 80, 26), Qt.AlignVCenter | Qt.AlignLeft,
                         TYPE_LABELS.get(kind, "笔记"))
        status_x = right - 16
        for show, name in ((selected, "select"), (bool(note.pinned_at), "pin"), (bool(note.remind_at), "clock")):
            if show:
                glyph(name, "accent_text" if name != "clock" else "muted").paint(painter, QRect(status_x, top + 5, 16, 16))
                status_x -= 19

        content = self.lib.content(note.id)
        title = note.display_title(content)
        cover = self._cover(note)
        text_width = right - left
        if cover is not None:
            painter.drawPixmap(QRect(right - 59, top + 31, 59, 59), cover)
            text_width -= 70

        heading = QFont(option.font)
        heading.setPixelSize(16)
        heading.setWeight(QFont.DemiBold)
        painter.setFont(heading)
        title_font = QFontMetrics(heading)
        title_color = C["accent_text" if selected else "ink"]
        painter.setPen(QColor(title_color))
        shown = title_font.elidedText(title, Qt.ElideRight, text_width)
        _draw_line(painter, QRect(left, top + 33, text_width, 24), shown, self.term, title_font, title_color)

        body = self._summary(note, content, title).strip()
        summary_font = QFont(option.font)
        summary_font.setPixelSize(12)
        painter.setFont(summary_font)
        summary_metrics = QFontMetrics(summary_font)
        painter.setPen(QColor(C["muted"]))
        for line, text in enumerate(_wrap_lines(body or "（空白）", summary_metrics, text_width, 2)):
            _draw_line(painter, QRect(left, top + 60 + 17 * line, text_width, 17),
                       text, self.term, summary_metrics)

        footer = round(card.bottom()) - 31
        painter.setPen(QPen(QColor(C["line_soft"]), 1))
        painter.drawLine(left, footer, right, footer)
        painter.setFont(meta)
        painter.setPen(QColor(C["muted"]))
        when = human_time(note.updated_at or note.created_at)
        if note.remind_at:
            when = "提醒 " + human_time(note.remind_at)
        metrics = QFontMetrics(meta)
        when_width = metrics.horizontalAdvance(when) + 4
        painter.drawText(QRect(right - when_width, footer + 4, when_width, 22), Qt.AlignRight | Qt.AlignVCenter, when)
        if self.show_topic:
            topic = self.lib.topics.get(note.topic_id)
            if topic:
                width = right - left - when_width - 7
                text = metrics.elidedText(topic.name, Qt.ElideRight, width)
                painter.drawText(QRect(left, footer + 4, width, 22), Qt.AlignLeft | Qt.AlignVCenter, text)
        painter.restore()

    @staticmethod
    def _summary(note, content: str, title: str) -> str:
        if note.type == "link":
            return "  ".join(part for part in (note.url, note.description) if part)
        if note.type == "account":
            return " · ".join(part for part in (note.account, note.remark) if part)
        if note.type == "date" and note.date:
            return _date_text(note.date)
        if note.type == "gallery":
            return f"{len(note.images)} 张图片" + (f" · {note.description}" if note.description else "")
        lines = content.strip().splitlines()
        if lines and lines[0].strip().lstrip("# ").strip() == title:
            lines = lines[1:]
        return " ".join(line.strip().lstrip("# ").strip() for line in lines)


def _spans(line: str, terms: list[str]) -> list[tuple[int, int]]:
    """一行里所有命中词的位置（重叠的部分合并）。"""
    low = line.lower()
    found: list[tuple[int, int]] = []
    for t in terms:
        t = t.lower()
        if not t:
            continue
        at = 0
        while True:
            i = low.find(t, at)
            if i < 0:
                break
            found.append((i, i + len(t)))
            at = i + len(t)
    if not found:
        return []
    found.sort()
    merged = [found[0]]
    for s0, e0 in found[1:]:
        if s0 <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e0))
        else:
            merged.append((s0, e0))
    return merged


def _draw_line(painter, rect, line, term, fm, normal_color=None) -> None:
    """绘制摘要及搜索高亮，支持空格分开的多个词。"""
    terms = [w for w in (term or "").split() if w]
    spans = _spans(line, terms)
    if not spans:
        painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, line)
        return
    color = QColor(normal_color or C["muted"])
    x = rect.left()
    pos = 0
    for s0, e0 in spans:
        if x > rect.right():
            break
        if s0 > pos:
            seg = line[pos:s0]
            w = fm.width(seg)
            painter.setPen(color)
            painter.drawText(QRect(x, rect.top(), w + 2, rect.height()), Qt.AlignLeft | Qt.AlignVCenter, seg)
            x += w
        seg = line[s0:e0]
        w = fm.width(seg)
        painter.fillRect(
            QRect(x - 1, rect.top() + 3, w + 2, rect.height() - 6), QColor(C["hl_bg"])
        )
        painter.setPen(QColor(C["accent_text"]))
        painter.drawText(QRect(x, rect.top(), w + 2, rect.height()), Qt.AlignLeft | Qt.AlignVCenter, seg)
        x += w
        pos = e0
    if pos < len(line) and x <= rect.right():
        seg = line[pos:]
        painter.setPen(color)
        painter.drawText(
            QRect(x, rect.top(), rect.width(), rect.height()),
            Qt.AlignLeft | Qt.AlignVCenter,
            fm.elidedText(seg, Qt.ElideRight, max(0, rect.right() - x)),
        )
    painter.setPen(color)


def _date_text(d: dict) -> str:
    from ..store import date_countdown

    t = d.get("type", 0)
    lunar = "农历" if d.get("isLunar") else "公历"
    if t == 0:
        base = f"{d.get('year')}年{d.get('month')}月{d.get('dayOfMonth')}日 · {lunar}"
    elif t == 1:
        base = f"每年 {d.get('month')}月{d.get('dayOfMonth')}日 · {lunar}"
    else:
        return ""
    delta = date_countdown(d)
    return f"{base} · {delta}" if delta else base


class TopicDelegate(QStyledItemDelegate):
    """主题条目：名称 + 计数 + 选中态。"""

    def __init__(self, lib, parent=None):
        super().__init__(parent)
        self.lib = lib
        self.counts: dict[str, int] = {}

    def sizeHint(self, option, index) -> QSize:
        return QSize(180, 36)

    def paint(self, painter: QPainter, option, index):
        topic = index.data(Qt.UserRole)
        if not topic:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(option.rect, QColor(C["paper_alt"]))
        r = option.rect.adjusted(1, 2, -2, -2)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        if selected:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(C["checked_bg"]))
            painter.drawRoundedRect(r, 8, 8)
        elif hovered:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(C["card_hover"]))
            painter.drawRoundedRect(r, 8, 8)
        cnt = self.counts.get(topic.id, 0)
        archived = getattr(topic, "state", "active") == "archived"
        name_font = QFont(option.font)
        name_font.setPixelSize(13)
        name_font.setWeight(QFont.DemiBold if selected else QFont.Normal)
        painter.setFont(name_font)
        name_color = C["faint"] if archived else C["ink"]
        painter.setPen(QColor(C["accent_text"] if selected else name_color))
        fm = QFontMetrics(name_font)
        locked = bool(getattr(topic, "locked", False))
        name = fm.elidedText(topic.name, Qt.ElideRight, r.width() - 67 - (15 if locked else 0))
        center = r.center().y()
        glyph("folder", "accent_text" if selected else "muted").paint(
            painter, QRect(r.left() + 9, center - 8, 16, 16)
        )
        painter.drawText(
            QRect(r.left() + 34, r.top(), r.width() - 57, r.height()),
            Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine,
            name,
        )
        if locked:
            glyph("lock", "faint").paint(painter, QRect(r.left() + 39 + fm.horizontalAdvance(name), center - 6, 12, 12))

        if cnt:
            meta = QFont(option.font)
            meta.setPixelSize(10)
            painter.setFont(meta)
            painter.setPen(QColor(C["accent_text"] if selected else C["faint"]))
            painter.drawText(QRect(r.right() - 41, r.top(), 30, r.height()), Qt.AlignRight | Qt.AlignVCenter, str(cnt))
        painter.restore()


class AutoFadeScrollBar(QScrollBar):
    """静止时变淡、滚动或悬停时恢复的滚动条，让列表看起来更干净。"""

    IDLE_OPACITY = 0.45

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        from PyQt5.QtCore import QEasingCurve, QPropertyAnimation, QTimer
        from PyQt5.QtWidgets import QGraphicsOpacityEffect

        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setOpacity(1.0)
        self.setGraphicsEffect(self._fx)
        self._anim = QPropertyAnimation(self._fx, b"opacity", self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(1400)
        self._timer.timeout.connect(lambda: self._fade_to(self.IDLE_OPACITY))
        self.valueChanged.connect(self._kick)
        self.sliderPressed.connect(self._kick)
        self._timer.start()

    def _kick(self, *args):
        self._fade_to(1.0)
        self._timer.start()

    def _fade_to(self, target: float):
        self._anim.stop()
        if not motion_enabled():
            self._fx.setOpacity(1.0)
            return
        self._anim.setStartValue(self._fx.opacity())
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, event):
        self._timer.stop()
        self._fade_to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._timer.start()
        super().leaveEvent(event)


class NoteList(QListWidget):
    """支持键盘、多选及跨主题拖动的自适应网格。"""

    MIME = "application/x-timo-note"
    deleteRequested = pyqtSignal(list)
    openRequested = pyqtSignal(QListWidgetItem)

    def __init__(self, parent=None, **kwargs):
        super().__init__(parent, **kwargs)
        self.open_on_click = True
        self._may_open = False
        self.card_opacity = 1.0
        self._reveal = QVariantAnimation(self)
        self._reveal.setDuration(120)
        self._reveal.setStartValue(0.68)
        self._reveal.setEndValue(1.0)
        self._reveal.setEasingCurve(QEasingCurve.OutCubic)
        self._reveal.valueChanged.connect(self._set_card_opacity)
        self.itemClicked.connect(self._activate_clicked)

    def update_grid(self):
        if self.viewMode() != QListView.IconMode:
            return
        width = max(1, self.viewport().width() - 14)
        columns = max(1, width // 264)
        size = QSize(width // columns, 166)
        if size != self.gridSize():
            self.setGridSize(size)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self.update_grid()

    def reveal_cards(self):
        self._reveal.stop()
        if self.isVisible() and motion_enabled():
            self._set_card_opacity(0.68)
            self._reveal.start()
        else:
            self._set_card_opacity(1.0)

    def _set_card_opacity(self, opacity):
        self.card_opacity = float(opacity)
        self.viewport().update()

    def _activate_clicked(self, item):
        if self._may_open and self.open_on_click:
            self.openRequested.emit(item)

    def mouseReleaseEvent(self, event):  # noqa: N802
        modifiers = Qt.ControlModifier | Qt.ShiftModifier | Qt.AltModifier
        self._may_open = event.button() == Qt.LeftButton and not event.modifiers() & modifiers
        try:
            super().mouseReleaseEvent(event)
        finally:
            self._may_open = False

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and self.open_on_click and self.currentItem():
            self.openRequested.emit(self.currentItem())
            return
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            ids = [i.data(Qt.UserRole) for i in self.selectedItems() if i.data(Qt.UserRole)]
            if ids:
                self.deleteRequested.emit(ids)
                return
        super().keyPressEvent(e)

    def startDrag(self, actions):  # noqa: N802
        items = self.selectedItems() or ([self.currentItem()] if self.currentItem() else [])
        ids = [i.data(Qt.UserRole) for i in items if i.data(Qt.UserRole)]
        if not ids:
            return
        it = items[0]
        mime = QMimeData()
        mime.setData(self.MIME, json.dumps(ids).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        rect = self.visualItemRect(it)
        pm = self.viewport().grab(rect.adjusted(6, 4, -6, -6))
        if not pm.isNull():
            drag.setPixmap(pm)
            drag.setHotSpot(QPoint(pm.width() // 2, pm.height() // 2))
        drag.exec_(Qt.MoveAction)


class TopicList(QListWidget):
    """主题列表：内部可拖动排序，也接受从笔记列表拖来的笔记。

    内部排序不走 Qt 默认的 InternalMove —— 那套会序列化 item 数据，
    Python 对象（Topic）存不进 mime，拖完 data(UserRole) 就丢了。
    这里自己 takeItem / insertItem，原对象原样搬运。
    """

    notesDropped = pyqtSignal(list, str)
    topicsReordered = pyqtSignal()

    def __init__(self, parent=None, **kwargs):
        super().__init__(parent, **kwargs)
        self.setDragDropMode(QListWidget.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self._drag_row = -1
        self._drop_before = -1
        self._drop_target = -1

    def startDrag(self, actions):  # noqa: N802
        self._drag_row = self.currentRow()
        try:
            super().startDrag(actions)
        finally:
            self._drag_row = -1
            self._clear_drop_mark()

    def _is_note(self, e) -> bool:
        return e.mimeData().hasFormat(NoteList.MIME)

    def _is_self_drag(self, e) -> bool:
        return e.source() is self and self._drag_row >= 0

    def dragEnterEvent(self, e):  # noqa: N802
        if self._is_note(e) or self._is_self_drag(e):
            e.acceptProposedAction()
            return
        super().dragEnterEvent(e)

    def dragMoveEvent(self, e):  # noqa: N802
        if self._is_note(e):
            row = self.indexAt(e.pos()).row()
            self._set_drop_mark(-1, row)
            e.acceptProposedAction()
            return
        if self._is_self_drag(e):
            before = self._insertion_row(e.pos())
            self._set_drop_mark(before if before not in (self._drag_row, self._drag_row + 1) else -1, -1)
            e.acceptProposedAction()
            return
        super().dragMoveEvent(e)

    def dragLeaveEvent(self, e):  # noqa: N802
        self._clear_drop_mark()
        super().dragLeaveEvent(e)

    def _set_drop_mark(self, before: int, target: int):
        if (before, target) != (self._drop_before, self._drop_target):
            self._drop_before, self._drop_target = before, target
            self.viewport().update()

    def _clear_drop_mark(self):
        self._set_drop_mark(-1, -1)

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        if self._drop_before < 0 and self._drop_target < 0:
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(C["accent_text"]), 2, Qt.SolidLine, Qt.RoundCap))
        if self._drop_target >= 0 and (item := self.item(self._drop_target)) is not None:
            painter.drawRoundedRect(QRectF(self.visualItemRect(item)).adjusted(3, 2, -4, -2), 7, 7)
        elif self._drop_before >= 0 and self.count():
            after = self._drop_before >= self.count()
            rect = self.visualItemRect(self.item(min(self._drop_before, self.count() - 1)))
            y = rect.bottom() - 1 if after else rect.top() + 1
            painter.drawLine(8, y, self.viewport().width() - 8, y)

    def _insertion_row(self, pos):
        index = self.indexAt(pos)
        if not index.isValid():
            return self.count()
        return index.row() + int(pos.y() > self.visualRect(index).center().y())

    def _move_row(self, e) -> None:
        src = self._drag_row
        row = self._insertion_row(e.pos())
        if src < 0 or row == src or row == src + 1:
            return
        item = self.takeItem(src)
        if item is None:
            return
        if row > src:
            row -= 1
        self.insertItem(row, item)
        self.setCurrentRow(row)
        self.topicsReordered.emit()

    def dropEvent(self, e):  # noqa: N802
        self._clear_drop_mark()
        if self._is_self_drag(e) and not self._is_note(e):
            e.acceptProposedAction()
            self._move_row(e)
            return
        if not self._is_note(e):
            super().dropEvent(e)
            return
        raw = bytes(e.mimeData().data(NoteList.MIME)).decode("utf-8", "ignore")
        it = self.itemAt(e.pos())
        e.acceptProposedAction()
        if not raw or it is None:
            return
        try:
            ids = json.loads(raw)
        except ValueError:
            ids = [raw]
        if isinstance(ids, str):
            ids = [ids]
        ids = [str(i) for i in ids if i]
        topic = it.data(Qt.UserRole)
        tid = getattr(topic, "id", None)
        if ids and tid:
            self.notesDropped.emit(ids, tid)
