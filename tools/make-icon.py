"""生成寒淋笔记的应用图标（珊瑚红圆角外框 + 三条笔记行）。

用法（仓库根目录）：
    python tools/make-icon.py

输出：
    assets/hanlin-notes.png   256x256 预览图
    assets/hanlin-notes.ico   16/20/24/32/40/48/64/96/128/256 多尺寸
    build/icon-preview.png    三种尺寸并排的放大对照（仅用于人工检查）

图标语言跟随 HanLin 系列：暖白纸底、珊瑚红细外框、近黑几何笔画。
三条长度递减的横线表示「记录」，没有文字笔画，16px 下也不会糊成一团。
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import struct

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
PAPER = (248, 248, 246, 255)
FRAME = (207, 98, 102, 255)
INK = (31, 31, 31, 255)
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def render(size: int, scale: int = 8) -> Image.Image:
    """按 size 画一张图标；内部放大 scale 倍再缩小，得到平滑边缘。"""
    extent = size * scale
    image = Image.new("RGBA", (extent, extent), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    small = size <= 32
    inset = 0 if small else max(1, round(extent * 0.015))
    radius = round(extent * (0.21 if small else 0.22))
    border = max(2, round(extent * (0.075 if small else 0.065)))
    draw.rounded_rectangle(
        (inset, inset, extent - 1 - inset, extent - 1 - inset),
        radius=radius,
        fill=PAPER,
        outline=FRAME,
        width=border,
    )

    # 三条横线：左边对齐，长度 52% / 38% / 24%，粗细一致，整体垂直居中
    line_h = max(2, round(extent * (0.080 if small else 0.070)))
    left = round(extent * (0.26 if small else 0.24))
    widths = (0.50, 0.36, 0.22)
    gaps = (0.112, 0.112)
    total = 3 * (line_h / extent) + sum(gaps)
    y = (1 - total) / 2
    centers = []
    for index in range(3):
        centers.append(round(extent * y))
        y += line_h / extent + (gaps[index] if index < 2 else 0)
    for ratio, top in zip(widths, centers):
        right = left + round(extent * ratio)
        draw.rounded_rectangle(
            (left, top, right, top + line_h - 1),
            radius=line_h // 2,
            fill=INK,
        )
    return image.resize((size, size), Image.Resampling.LANCZOS)


def write_ico(path: Path, frames: list[Image.Image]) -> None:
    offset = 6 + 16 * len(frames)
    directory = bytearray(struct.pack("<HHH", 0, 1, len(frames)))
    payloads = []
    for frame in frames:
        with BytesIO() as out:
            frame.save(out, "PNG", optimize=True)
            payload = out.getvalue()
        width, height = frame.size
        directory.extend(
            struct.pack("<BBBBHHII", width % 256, height % 256, 0, 0, 1, 32, len(payload), offset)
        )
        payloads.append(payload)
        offset += len(payload)
    path.write_bytes(bytes(directory) + b"".join(payloads))


def preview(frames: list[Image.Image]) -> Image.Image:
    """16/24/48 放大 8 倍并排，方便肉眼检查小尺寸是否清楚。"""
    picks = [(f, 16) for f in frames if f.size[0] in (16, 24, 48)]
    zoom = 152
    canvas = Image.new("RGBA", (zoom * len(picks) + 24 * (len(picks) + 1), zoom + 48), (255, 255, 255, 255))
    x = 24
    for frame, _ in picks:
        canvas.paste(frame.resize((zoom, zoom), Image.Resampling.NEAREST), (x, 24))
        x += zoom + 24
    return canvas


def main() -> int:
    frames = [render(size) for size in SIZES]
    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)

    big = render(256)
    big.save(assets / "hanlin-notes.png")
    write_ico(assets / "hanlin-notes.ico", frames)

    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    preview(frames).save(build / "icon-preview.png")

    print("图标已生成：")
    print(f"  {assets / 'hanlin-notes.png'}  256x256")
    print(f"  {assets / 'hanlin-notes.ico'}  {len(SIZES)} 个尺寸")
    print(f"  {build / 'icon-preview.png'}  小尺寸对照")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
