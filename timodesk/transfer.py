"""Shared handling of attachment identity in local imports and exports."""
from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path

ATTACHMENT_LINK = re.compile(r"(\]\()(?:\.\./)*attachments/([^)\s]+)(\))")


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return stream_digest(stream)


def stream_digest(stream) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


@dataclass
class AttachmentIndex:
    by_digest: dict[str, str] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    names: set[str] = field(default_factory=set)


def digest_index(folder: Path) -> AttachmentIndex:
    index = AttachmentIndex()
    if folder.is_dir():
        for path in sorted(folder.iterdir()):
            index.names.add(path.name.casefold())
            if path.is_file() and not path.is_symlink():
                index.aliases[path.name] = index.by_digest.setdefault(file_digest(path), path.name)
    return index


def copy_attachment(source: Path, target: Path, index: AttachmentIndex) -> tuple[str, bool]:
    digest = file_digest(source)
    existing = index.by_digest.get(digest)
    if existing is not None:
        return existing, False
    name, number = source.name, 2
    while name.casefold() in index.names or (target / name).exists():
        name = f"{source.stem}-{number}{source.suffix}"
        number += 1
    dest = target / name
    output = dest.open("xb")
    try:
        with output:
            with source.open("rb") as stream:
                shutil.copyfileobj(stream, output)
        if file_digest(dest) != digest:
            raise OSError(f"附件复制校验失败：{source.name}")
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    index.by_digest[digest] = name
    index.names.add(name.casefold())
    return name, True


def rewrite_links(text: str, aliases: dict[str, str], prefix: str = "") -> str:
    return ATTACHMENT_LINK.sub(
        lambda match: f"]({prefix}attachments/{aliases.get(match[2], match[2])})", text
    )


def rewrite_note(note, aliases: dict[str, str]):
    images = ["attachments/" + aliases.get(rel[12:], rel[12:]) if rel.startswith("attachments/") else rel
              for rel in note.images]
    return replace(
        note,
        images=list(dict.fromkeys(images)),
        description=rewrite_links(note.description or "", aliases),
    )
