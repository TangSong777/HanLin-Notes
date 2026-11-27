"""从笔记包 ZIP（兼容外部笔记 App 的「可读数据导出」）导入到本地库。"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

from .store import Library, Note, Topic, TYPE_CODE, now_iso
from .transfer import ATTACHMENT_LINK, copy_attachment, digest_index, file_digest, rewrite_links, rewrite_note

IMG_RE = re.compile(r"!\[([^\]]*)\]\((?:\.\./)+attachments/([^)\s]+)\)")


@dataclass
class ImportReport:
    topics: int = 0
    notes: int = 0
    attachments: int = 0
    missing_attachments: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped: int = 0
    mode: str = "replace"
    renamed: int = 0
    deduped: int = 0

    def summary(self) -> str:
        if self.mode == "merge":
            s = f"合并完成：新增 {self.notes} 条笔记、{self.attachments} 个附件"
            if self.skipped:
                s += f"\n已存在而跳过 {self.skipped} 条"
            if self.renamed:
                s += f"\n{self.renamed} 个同名主题已自动加序号区分"
        else:
            s = f"导入完成：{self.topics} 个主题、{self.notes} 条笔记、{self.attachments} 个附件"
        if self.deduped:
            s += f"\n跳过 {self.deduped} 条内容重复的笔记"
        if self.missing_attachments:
            s += f"\n缺失附件 {len(self.missing_attachments)} 个"
        return s


def _dedupe_key(note: Note, content: str) -> tuple:
    """「是不是同一条笔记」的指纹，与「查找重复笔记」共用一套口径。"""
    from .store import note_fingerprint

    if note.type == "article":
        content = _strip_leading_title(content, note.title)
    return note_fingerprint(note, content), note.color, note.remind_at, bool(note.pinned_at)


def _fix_zip_name(info: zipfile.ZipInfo) -> str:
    """Windows 下用资源管理器压缩的 zip 可能用 GBK 存文件名。"""
    name = info.filename
    if not (info.flag_bits & 0x800):
        try:
            raw = name.encode("cp437")
            for enc in ("utf-8", "gbk"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
        except UnicodeEncodeError:
            pass
    return name


def extract_export(src: str | os.PathLike, dst: str | os.PathLike) -> Path:
    """把导出 ZIP 解压到 dst（已解压目录则直接返回）。"""
    src = Path(src)
    dst = Path(dst)
    if src.is_dir():
        return src
    dst.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src) as z:
        if sum(info.file_size for info in z.infolist()) > shutil.disk_usage(dst).free:
            raise OSError("暂存目录空间不足，无法完整解压笔记包")
        names = set()
        for info in z.infolist():
            name = _fix_zip_name(info).replace("\\", "/")
            if info.is_dir():
                name = name.removesuffix("/")
            rel = _safe_relative(name)
            key = name.casefold()
            if key in names or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError(f"笔记包含重复路径或链接：{name}")
            names.add(key)
            if info.is_dir():
                continue
            out = dst / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as fh, open(out, "xb") as w:
                shutil.copyfileobj(fh, w)
    return dst


def _safe_relative(name: str) -> Path:
    parts = name.split("/")
    for part in parts:
        if (not part or part in (".", "..") or part.endswith((" ", "."))
                or re.search(r'[\\:<>|?*"\x00-\x1f]', part)
                or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", part.split(".")[0])):
            raise ValueError(f"笔记包含无效路径：{name}")
    return Path(*parts)


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig")


def _norm_newlines(s: str) -> str:
    return s.replace("\r\n", "\n").replace("\r", "\n")


def _strip_leading_title(content: str, title: str | None) -> str:
    """文章正文里通常自带 '# 标题' 行，去掉它避免和标题栏重复。"""
    text = _norm_newlines(content or "")
    if title:
        lines = text.split("\n")
        head = 0
        while head < len(lines) and not lines[head].strip():
            head += 1
        if head < len(lines) and lines[head].strip() == f"# {title}".strip():
            lines = lines[head + 1 :]
            while lines and not lines[0].strip():
                lines.pop(0)
            return "\n".join(lines)
    return text


def _load_account_passwords(root: Path) -> dict[str, dict]:
    """accounts.csv: id,topic_id,title,account,password,remark,created_at,updated_at"""
    p = root / "content" / "accounts.csv"
    out: dict[str, dict] = {}
    if not p.exists():
        return out
    with open(p, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            out[row.get("id", "")] = row
    return out


def import_export(src: str | os.PathLike, lib: Library, progress=None, mode: str = "replace",
                  dedupe: bool = True) -> ImportReport:
    """Validate and prepare the complete result before touching the active library."""
    if mode not in ("merge", "replace"):
        raise ValueError("无效的导入模式")
    lib.root.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".hanlin-import-", dir=lib.root.parent))
    clean = True
    try:
        base_index = lib.json_path.read_bytes() if lib.json_path.is_file() else None
        staged = Library(work / "new").load()
        if mode == "merge":
            staged.topics, staged.notes = deepcopy((lib.topics, lib.notes))
            staged._content = lib._content.copy()
            if lib.notes_dir.exists():
                shutil.copytree(lib.notes_dir, staged.notes_dir, dirs_exist_ok=True)
        if lib.attachments_dir.exists():
            shutil.copytree(lib.attachments_dir, staged.attachments_dir, dirs_exist_ok=True)
        report = _prepare_import(src, staged, progress, mode, dedupe)
        current_index = lib.json_path.read_bytes() if lib.json_path.is_file() else None
        if current_index != base_index:
            raise ValueError("笔记库在导入期间发生改动，请重新导入。")
        if mode == "merge" and not report.notes and not report.attachments and staged.topics == lib.topics:
            return report
        lib.root.mkdir(parents=True, exist_ok=True)
        old = work / "original"
        old.mkdir()
        moved, applied = [], []
        try:
            for name in ("notes", "attachments", "library.json"):
                target = lib.root / name
                if target.exists():
                    os.replace(target, old / name)
                    moved.append(name)
                os.replace(staged.root / name, target)
                applied.append(name)
        except BaseException:
            try:
                for name in reversed(applied):
                    os.replace(lib.root / name, staged.root / name)
                for name in reversed(moved):
                    os.replace(old / name, lib.root / name)
            except OSError as error:
                clean = False
                raise OSError(f"导入回滚失败，原文件保留于 {old}") from error
            raise
        lib.topics, lib.notes, lib._content = staged.topics, staged.notes, staged._content
        lib._saved_signature = staged._saved_signature
        return report
    finally:
        if clean:
            shutil.rmtree(work, ignore_errors=True)


def _prepare_import(src, lib, progress, mode, dedupe) -> ImportReport:
    report = ImportReport(mode=mode)
    tmp = None
    try:
        src_path = Path(src)
        if src_path.is_dir():
            root = src_path
        else:
            tmp = tempfile.mkdtemp(prefix="package-", dir=lib.root.parent)
            root = extract_export(src_path, tmp)

        data_dir = root / "data"
        for path in (data_dir / "topics.json", data_dir / "documents.json",
                     root / "manifest.json", root / "content" / "accounts.csv", root / "attachments"):
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("笔记包含有指向包外的路径")
        if not (data_dir / "documents.json").is_file():
            raise ValueError("笔记包缺少 data/documents.json")
        topics_raw = json.loads(_read_text(data_dir / "topics.json")) if (data_dir / "topics.json").exists() else {}
        docs_raw = json.loads((data_dir / "documents.json").read_text(encoding="utf-8-sig"))
        if not isinstance(docs_raw.get("documents"), list):
            raise ValueError("笔记包的 documents 索引无效")
        manifest = json.loads(_read_text(root / "manifest.json")) if (root / "manifest.json").exists() else {}
        if manifest.get("status") in ("partial", "incomplete"):
            raise ValueError("笔记包标记为不完整")
        passwords = _load_account_passwords(root)

        merge = mode == "merge"
        # 内容指纹：替换导入只比对包内，合并导入还要跟库里已有的笔记比。
        # 回收站里的也算（否则「两条都在回收站」时会给其中一条再造一份）。
        seen = set()
        attachment_digests = digest_index(lib.attachments_dir)
        existing_aliases = attachment_digests.aliases
        if dedupe and merge:
            for existing in lib.notes.values():
                seen.add(_dedupe_key(rewrite_note(existing, existing_aliases),
                                     rewrite_links(lib.content(existing.id), existing_aliases)))
        base_order = max((t.order for t in lib.topics.values()), default=-1.0) + 1 if merge else 0.0
        if not merge:
            # 清空目标库
            lib.topics.clear()
            lib.notes.clear()
            lib._content.clear()
            if lib.notes_dir.exists():
                shutil.rmtree(lib.notes_dir)
        lib.notes_dir.mkdir(parents=True, exist_ok=True)
        lib.attachments_dir.mkdir(parents=True, exist_ok=True)

        # 1) 主题
        topic_list = topics_raw.get("topics", [])
        topic_records = {}
        for i, t in enumerate(topic_list):
            if t["id"] in topic_records:
                if topic_records[t["id"]] != t:
                    raise ValueError("笔记包含重复且内容不同的主题标识")
                continue
            topic_records[t["id"]] = t
            if merge and t["id"] in lib.topics:
                continue
            name = t.get("name") or "未命名主题"
            if merge:
                # 另一个手机导出的主题 id 不同、名字可能一样，加序号免得侧栏出现两个同名
                taken = {x.name for x in lib.topics.values() if x.state != "trash"}
                if name in taken:
                    base, n = name, 2
                    while f"{base}（{n}）" in taken:
                        n += 1
                    name = f"{base}（{n}）"
                    report.renamed += 1
            lib.topics[t["id"]] = Topic(
                id=t["id"],
                name=name,
                mode=t.get("mode") or "white",
                locked=bool(t.get("locked")),
                state=t.get("state") or "active",
                created_at=t.get("createdAt") or "",
                updated_at=t.get("updatedAt") or "",
                deleted_at=t.get("deletedAt"),
                archived_at=t.get("archivedAt"),
                order=float(i) + base_order,
            )
        report.topics = len(lib.topics)

        # 2) 附件：uri → 应用内相对路径
        uri_map: dict[str, str] = {}
        aliases: dict[str, str] = {}
        att_src = root / "attachments"
        entries = manifest.get("attachments", [])
        for entry in entries:
            if entry.get("sha256"):
                relative = _safe_relative(entry["path"])
                if len(relative.parts) != 2 or relative.parts[0] != "attachments":
                    raise ValueError("附件清单路径无效")
                file = root / relative
                if (file.is_symlink() or not file.is_file() or file.stat().st_size != entry.get("size")
                        or file_digest(file) != entry["sha256"]):
                    raise ValueError(f"附件校验失败：{file.name}")
        if att_src.exists():
            for f in sorted(att_src.iterdir()):
                if not f.is_file():
                    continue
                if f.is_symlink():
                    raise ValueError(f"笔记包含附件链接：{f.name}")
                name, copied = copy_attachment(f, lib.attachments_dir, attachment_digests)
                aliases[f.name] = name
                report.attachments += int(copied)
        for a in entries:
            if a.get("uri") and a.get("path"):
                name = Path(a["path"]).name
                uri_map[a["uri"]] = f"attachments/{aliases.get(name, name)}"

        def rewrite_md(text: str) -> str:
            """把 md 里的 ../../../attachments/x 改成 attachments/x。"""
            new = rewrite_links(text, aliases)
            for match in ATTACHMENT_LINK.finditer(new):
                name = match[2]
                if len(_safe_relative(name).parts) != 1:
                    raise ValueError("笔记包含无效的附件路径")
                if not lib.attachment_path(name).exists():
                    report.missing_attachments.append(name)
            return new

        # 3) 逐条笔记
        docs = docs_raw.get("documents", [])
        total = len(docs)
        note_ids = {nid.casefold(): nid for nid in lib.notes}
        for idx, d in enumerate(docs):
            raw = d.get("raw") or {}
            ntype = d.get("type") or "note"
            if ntype not in TYPE_CODE:
                report.warnings.append(f"未知类型 {ntype}，按笔记处理：{d.get('id')}")
                ntype = "note"
            title = raw.get("title") or (d.get("normalized") or {}).get("title")
            content = ""
            images: list[str] = []
            url = None
            description = ""
            account = password = remark = ""
            date_info = None

            rp = d.get("readablePath") or ""
            md_path = root / _safe_relative(rp.replace("\\", "/")) if rp else None
            if md_path and not md_path.resolve().is_relative_to(root.resolve()):
                raise ValueError("笔记包含有指向包外的正文路径")
            md_text = _read_text(md_path) if md_path and md_path.exists() else ""

            try:
                ext = json.loads(raw.get("extension") or "{}") if raw.get("extension") else {}
            except json.JSONDecodeError:
                ext = {}

            if ntype == "account":
                row = passwords.get(d.get("id", ""), {})
                account = ext.get("account") or (d.get("normalized") or {}).get("account") or ""
                password = row.get("password") or (d.get("normalized") or {}).get("password") or ""
                remark = ext.get("description") or row.get("remark") or ""
                content = _norm_newlines(raw.get("content") or "")
            elif ntype == "link":
                url = ext.get("url") or (d.get("normalized") or {}).get("url") or ""
                description = _norm_newlines(raw.get("content") or "")
                content = ""
            elif ntype == "gallery":
                imgs = ext.get("images") or (d.get("normalized") or {}).get("images") or []
                for u in imgs:
                    if u in uri_map:
                        images.append(uri_map[u])
                    elif u.startswith("timo://"):
                        report.missing_attachments.append(f"{u}（无映射）")
                    else:
                        images.append(f"attachments/{aliases.get(u[12:], u[12:])}" if u.startswith("attachments/") else u)
                description = _norm_newlines(raw.get("content") or "")
                # 可读 md 里的图片引用也一并收集（兜底）
                for m in IMG_RE.finditer(md_text):
                    rel = f"attachments/{aliases.get(m.group(2), m.group(2))}"
                    if rel not in images:
                        images.append(rel)
                content = ""
            elif ntype == "date":
                date_info = {
                    "type": ext.get("type", 0),
                    "year": ext.get("year"),
                    "month": ext.get("month"),
                    "dayOfMonth": ext.get("dayOfMonth"),
                    "isLunar": bool(ext.get("isLunar")),
                    "dayOfWeek": ext.get("dayOfWeek", -1),
                }
                description = _norm_newlines(raw.get("content") or "")
            elif ntype == "article":
                content = _strip_leading_title(raw.get("content", md_text) or "", title)
            else:  # note
                content = _norm_newlines(raw.get("content") or "")
                if not content and md_text:
                    content = _norm_newlines(md_text)
            content = rewrite_md(content)
            description = rewrite_md(description)

            nid = d.get("id") or lib.new_id()
            if len(_safe_relative(nid).parts) != 1:
                raise ValueError(f"无效的笔记标识：{nid}")
            note = Note(
                id=nid,
                topic_id=d.get("topicId") or "",
                type=ntype,
                state=d.get("state") or "active",
                title=title,
                color=d.get("color"),
                created_at=d.get("createdAt") or "",
                updated_at=d.get("updatedAt") or "",
                deleted_at=d.get("deletedAt"),
                archived_at=d.get("archivedAt"),
                pinned_at=d.get("pinnedAt"),
                remind_at=d.get("remindAt"),
                url=url,
                description=description,
                images=list(dict.fromkeys(images)),
                account=account,
                password=password,
                remark=remark,
                date=date_info,
                order=float(idx),
            )
            key = _dedupe_key(note, content)
            existing = lib.notes.get(note_ids.get(nid.casefold()))
            if existing and _dedupe_key(rewrite_note(existing, existing_aliases),
                                        rewrite_links(lib.content(existing.id), existing_aliases)) == key:
                report.skipped += 1
                if progress and (idx % 25 == 0 or idx == total - 1):
                    progress(idx + 1, total)
                continue
            if dedupe:
                if key in seen:
                    report.deduped += 1
                    if progress and (idx % 25 == 0 or idx == total - 1):
                        progress(idx + 1, total)
                    continue
                seen.add(key)
            if existing:
                note.id = lib.new_id()
                report.warnings.append("同一笔记标识已有不同内容，保留为单独的笔记。")
            # 主题不存在时挂到一个占位主题，避免丢数据
            if note.topic_id not in lib.topics:
                placeholder = "00000000-0000-0000-0000-000000000000"
                if placeholder not in lib.topics:
                    lib.topics[placeholder] = Topic(
                        id=placeholder, name="（未分类）", created_at=now_iso(), updated_at=now_iso(), order=9999
                    )
                note.topic_id = placeholder
            lib.notes[note.id] = note
            note_ids[note.id.casefold()] = note.id
            lib._content[note.id] = content
            p = lib.note_path(note.id)
            p.write_text(content, encoding="utf-8", newline="\n")
            report.notes += 1
            if progress and (idx % 25 == 0 or idx == total - 1):
                progress(idx + 1, total)

        if progress and not total:
            progress(0, 0)
        lib.save()
        return report
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
