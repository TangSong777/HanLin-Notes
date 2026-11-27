"""跑 tests/ 下全部 test_*.py。

用法（在仓库根目录）：
    python tests/run_all.py
    python tests/run_all.py --portable <独立的便携构建目录>

每个脚本都是独立进程，工作目录固定为仓库根，并把仓库根放进 PYTHONPATH，
这样测试里 `import timodesk` 才能找到包。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portable", type=Path, help="包含 HanLin-Notes.exe 且无现有 Data 的目录")
    args = parser.parse_args()
    files = sorted(ROOT.glob("tests/test_*.py"))
    if args.portable is None:
        files = [file for file in files if file.name != "test_portable.py"]
    if not files:
        print("没找到测试脚本")
        return 1
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    bad: list[str] = []
    for f in files:
        command = [sys.executable, str(f)]
        if f.name == "test_portable.py":
            command.append(str(args.portable.resolve()))
        try:
            r = subprocess.run(
                command,
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
            )
            rc = r.returncode
            out = (r.stderr if rc != 0 else "") or r.stdout or ""
        except subprocess.TimeoutExpired:
            rc, out = 99, "超时"
        tail = [line.strip() for line in out.strip().splitlines() if line.strip()]
        last = tail[-1] if tail else "（无输出）"
        # 只有「非零失败数」才算失败：「0 FAIL」「PASS」都不算
        failed_mark = re.search(r"[1-9]\d*\s*(FAIL|failed|项失败)", last)
        ok = rc == 0 and not failed_mark
        print(f"{'OK ' if ok else 'BAD'} {f.name:<26} {last}")
        if not ok:
            bad.append(f.name)
    print(f"\n{len(files) - len(bad)}/{len(files)} 通过" + (f"；失败：{', '.join(bad)}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
