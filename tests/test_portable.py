"""启动便携目录里的 exe，确认窗口出现、Data 目录写盘，然后结束进程。"""
import os
import subprocess
import sys
import time
from ctypes import WINFUNCTYPE, byref, windll, wintypes
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("用法：python tests/test_portable.py <独立的便携构建目录>")
TARGET = Path(sys.argv[1]).resolve()
EXE = TARGET / "HanLin-Notes.exe"
if not EXE.is_file() or (TARGET / "Data").exists():
    raise SystemExit("需要一个含 HanLin-Notes.exe 且没有 Data 的新构建目录")

user32 = windll.user32
env = os.environ.copy()
env.pop("HANLIN_DATA_DIR", None)
env.pop("TIMO_DATA_DIR", None)
proc = subprocess.Popen([str(EXE)], cwd=TARGET, env=env)
found = False


def cb(hwnd, _):
    global found
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, byref(pid))
    if pid.value != proc.pid:
        return True
    if not user32.IsWindowVisible(hwnd):
        return True
    buf = (wintypes.WCHAR * 256)()
    user32.GetWindowTextW(hwnd, buf, len(buf))
    if buf.value in {"寒淋笔记", "欢迎使用寒淋笔记"}:
        found = True
    return True


callback = WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(cb)
try:
    for _ in range(30):
        user32.EnumWindows(callback, 0)
        if found and (TARGET / "Data").is_dir():
            print("PASS 寒淋笔记窗口已出现，便携 Data 已创建")
            break
        if proc.poll() is not None:
            raise SystemExit(f"便携程序提前退出：{proc.returncode}")
        time.sleep(1)
    else:
        raise SystemExit("便携程序没有同时显示窗口并创建 Data")
finally:
    if proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=5)
