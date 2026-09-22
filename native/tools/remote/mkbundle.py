# -*- coding: utf-8 -*-
"""打包当前工作树给远端用（排除大件与本地专属产物）"""
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]   # .zcode/tmp/dsw/mkbundle.py -> 项目根
OUT = Path(__file__).resolve().parent / "roco_patch.zip"

SKIP_DIRS = {
    ".git", "env", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache",
    "checkpoints", "build", "_attachments", ".zcode", "frontend", "wiki", "raw",
    "target", ".tmp_probe", ".ruff_cache", ".idea", ".vscode",
}
SKIP_REL_PREFIX = (
    "backend/engine/ai/log/",
    "backend/engine/ai/data/",      # 远端已有（含 31MB training_reference.json）
)
SKIP_SUFFIX = (".pyc", ".pyd", ".so", ".log", ".pt", ".npz", ".jsonl")
SKIP_ROOT_GLOB_PREFIX = "_"      # 根目录的调试产物 _*.json / _*.txt 等


def keep(rel: str) -> bool:
    if any(rel.startswith(p) for p in SKIP_REL_PREFIX):
        return False
    if rel.endswith(SKIP_SUFFIX):
        return False
    if "/" not in rel and rel.startswith(SKIP_ROOT_GLOB_PREFIX):
        return False
    if rel.startswith("native/tools/_") or rel.startswith("backend/engine/ai/log"):
        return False
    return True


def main() -> None:
    os.chdir(ROOT)
    n = total = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                p = Path(dirpath) / fn
                rel = p.relative_to(ROOT).as_posix()
                if not keep(rel):
                    continue
                zf.write(p, rel)
                n += 1
                total += p.stat().st_size
    print(f"{OUT}  files={n}  原始 {total/1e6:.1f} MB  压缩后 {OUT.stat().st_size/1e6:.1f} MB")


if __name__ == "__main__":
    sys.exit(main())
