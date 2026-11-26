"""构建可独立运行的程序目录，以及 HanLin 系列 Windows 安装包。

用法：
    python tools/make-portable.py            # 全部重打，并同步到桌面上的日常使用目录
    python tools/make-portable.py --skip-main  # 复用 dist/HanLin-Notes
    python tools/make-portable.py --no-sync    # 只打包，不覆盖日常使用目录

打完会自动把程序文件覆盖到「桌面/HanLin-Notes」（用户数据 Data/ 保持不动），
所以平时只要跑这一条命令，桌面上的那份就是最新的。
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICON = ROOT / "assets" / "hanlin-notes.ico"

EXCLUDES = [
    "tkinter",
    "matplotlib",
    "numpy",
    "pandas",
    "flask",
    "scipy",
    "IPython",
    "pytest",
]

# 裁掉用不到的 Qt 运行库：Qt 的 QML/Quick/PDF/虚拟键盘/WebSockets/DBus/Network 我们都没用，
# ANGLE（libEGL + libGLESv2）在桌面 OpenGL 可用的机器上也不会走。
# 这些文件删掉后主程序、导入导出、图库、PDF 导出都实测正常（Qt 仍然保留 icudt/opengl32sw，
# 前者缺了中文会起不来，后者是给没有显卡驱动（远程桌面/虚拟机）的机器兜底的）。
PRUNE = [
    "Qt5Qml_conda.dll",
    "Qt5QmlModels_conda.dll",
    "Qt5Quick_conda.dll",
    "Qt5Pdf_conda.dll",
    "Qt5VirtualKeyboard_conda.dll",
    "Qt5WebSockets_conda.dll",
    "Qt5DBus_conda.dll",
    "Qt5Network_conda.dll",
    "libGLESv2.dll",
    "libEGL.dll",
]


def version() -> str:
    ns: dict = {}
    exec((ROOT / "timodesk" / "__init__.py").read_text(encoding="utf-8"), ns)  # noqa: S102
    return ns.get("__version__", "0.0.0")


def pyinstaller(*args: str) -> None:
    env = dict(os.environ)
    user_site = Path(os.environ.get("APPDATA", "")) / "Python" / "Python312" / "site-packages"
    if user_site.exists():
        env["PYTHONPATH"] = str(user_site) + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
        )
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", *args]
    print("  $", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, env=env, check=True)


def build_main(dist: Path, work: Path, app_dir: Path) -> None:
    print("[1/2] 打包主程序…")
    if app_dir.exists():
        # PyInstaller replaces the named output directory.
        if app_dir.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT or any(
            item.name not in {"HanLin-Notes.exe", "_internal"} for item in app_dir.iterdir()
        ):
            raise SystemExit(f"输出目录含已有数据，请用 --dist 指定独立的构建目录：{app_dir}")
    args = [
        "--distpath", str(dist), "--workpath", str(work), "--specpath", str(work),
        "--windowed",
        "--name",
        "HanLin-Notes",
        "--icon",
        str(ICON),
        "--add-data",
        f"{ICON};assets",
    ]
    for m in EXCLUDES:
        args += ["--exclude-module", m]
    args.append(str(ROOT / "run.py"))
    pyinstaller(*args)
    if not (app_dir / "HanLin-Notes.exe").is_file():
        raise SystemExit("主程序没有打出来：" + str(app_dir))
    prune(app_dir)


def prune(app_dir: Path) -> None:
    """删掉确定用不到的 Qt 运行库，减掉约 22 MB。"""
    internal = app_dir / "_internal"
    saved = 0
    removed = []
    for name in PRUNE:
        p = internal / name
        if p.exists():
            saved += p.stat().st_size
            p.unlink()
            removed.append(name)
    if removed:
        print(f"      裁剪 {len(removed)} 个 Qt 库，省 {saved / 1024 / 1024:.1f} MB")


def find_iscc() -> Path:
    on_path = shutil.which("ISCC.exe")
    if on_path:
        return Path(on_path)
    for base in (Path(os.environ["LOCALAPPDATA"]) / "Programs",
                 Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")),
                 Path(os.environ["ProgramFiles"])):
        candidate = base / "Inno Setup 6" / "ISCC.exe"
        if candidate.is_file():
            return candidate
    raise SystemExit("找不到 ISCC.exe，请使用 Inno Setup 6.6 或更新版本。")


def build_setup(dist: Path, app_dir: Path, iscc: Path) -> None:
    print("[2/2] 编译 HanLin 安装向导…")
    package_version = version()
    subprocess.run(
        [str(iscc), f"/DPayloadDir={app_dir}", f"/DMyAppVersion={package_version}",
         f"/O{dist}", str(ROOT / "installer" / "HanLin-Notes.iss")],
        cwd=ROOT, check=True,
    )
    exe = dist / f"HanLin-Notes-Setup-{package_version}.exe"
    if not exe.is_file() or not exe.stat().st_size:
        raise SystemExit(f"安装器没有打出来：{exe}")
    mb = exe.stat().st_size / 1024 / 1024
    print(f"\n完成：{exe}  ({mb:.1f} MB)   版本 {version()}")


def desktop_dir() -> Path:
    """当前用户的桌面目录（可能被重定向到别的盘）。"""
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "Desktop")
                if value:
                    path = Path(os.path.expandvars(value))
                    if path.is_dir():
                        return path
        except OSError:
            pass
    for name in ("Desktop", "桌面"):
        path = Path.home() / name
        if path.is_dir():
            return path
    return Path.home() / "Desktop"


def sync_install(src: Path, dst: Path) -> tuple[int, str]:
    """把构建好的程序目录覆盖到日常使用的位置，保留用户数据（Data/）。

    返回（复制的文件数, 说明）。程序正在运行时 exe 会被占用，这里只提示、不中断。
    """
    copied = 0
    try:
        if not dst.is_dir():
            shutil.copytree(src, dst)
            for p in src.rglob("*"):
                copied += 1 if p.is_file() else 0
            return copied, f"已新建 {dst}"
        for path in src.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(src)
            if rel.parts and rel.parts[0] == "Data":
                continue  # 用户数据不动
            out = dst / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, out)
            copied += 1
    except PermissionError as exc:
        return copied, f"{dst} 写入失败（{exc.filename} 可能正在运行），请先退出程序"
    return copied, f"已同步到 {dst}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-main", action="store_true")
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--work", type=Path, default=ROOT / "build")
    parser.add_argument("--iscc", type=Path)
    parser.add_argument("--desktop", type=Path, help="覆盖日常使用目录（默认读注册表里的桌面）")
    parser.add_argument("--no-sync", action="store_true", help="不把新构建同步到日常使用目录")
    args = parser.parse_args()
    dist, work = args.dist.resolve(), args.work.resolve()
    app_dir = dist / "HanLin-Notes"
    iscc = args.iscc.resolve() if args.iscc else find_iscc()
    if not args.skip_main:
        work.mkdir(parents=True, exist_ok=True)
        build_main(dist, work, app_dir)
    build_setup(dist, app_dir, iscc)
    if not args.no_sync:
        target = (args.desktop or (desktop_dir() / "HanLin-Notes")).resolve()
        count, note = sync_install(app_dir, target)
        print(f"日常使用目录：{note}（{count} 个程序文件，Data/ 未改动）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
