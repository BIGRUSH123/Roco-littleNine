# -*- coding: utf-8 -*-
"""找"疑似未定义名"（pyflakes F821）——本机没装 ruff/pyflakes 时的替代。

判据（保守，只报几乎确定的）：模块里出现的某个 `Name(Load)`，
- 不是内建名（`builtins` 里查得到的不报），
- 在**本模块任何位置**都没被绑定过（import / 赋值 / def / class / 形参 /
  `with ... as` / `for` 目标 / `except ... as` / 推导式目标 / `global`·`nonlocal` 声明），
就报出来。

真实案例（2026-09-23 审计）：`backend/engine/replayer.py:_apply_replay_choice` 里
用了没导入的 `vm_execute`，而调用点被 `try/except Exception: continue` 包着
（`_fire_post_event`），于是「一意孤行」的额外一次选择重放**静默失效**——
静态检查是唯一能在事前发现这类问题的办法。

用法: python native/tools/find_undefined_names.py [路径...]（默认 backend native/tools）
"""
from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

_SKIP_DIRS = {"__pycache__", ".git", "target", "build", "env", "node_modules"}
_BUILTINS = set(dir(builtins)) | {"__name__", "__file__", "__doc__", "__package__",
                                  "__spec__", "__loader__", "__builtins__", "__debug__"}


def _bound_names(tree: ast.AST) -> set[str]:
    """模块里所有"被绑定过"的名字（不分作用域，保守取并集）。"""
    names: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            names.add(n.id)
        elif isinstance(n, ast.arg):
            names.add(n.arg)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.alias):
            names.add((n.asname or n.name).split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            names.update(n.names)
        elif isinstance(n, ast.MatchAs) and n.name:      # match/case 捕获
            names.add(n.name)
        elif isinstance(n, ast.MatchStar) and n.name:
            names.add(n.name)
        elif isinstance(n, ast.MatchMapping) and n.rest:
            names.add(n.rest)
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                if a.name == "*":
                    names.add("*STAR*")
    return names


def scan(path: Path) -> list[tuple[int, str]]:
    try:
        # utf-8-sig：吃掉 BOM（本仓有几个文件带 BOM，Python 能跑但解析器要显式剥掉）
        src = path.read_text(encoding="utf-8-sig")
    except (UnicodeDecodeError, OSError) as e:
        return [(0, f"跳过（读不了）: {type(e).__name__}")]
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as e:
        return [(e.lineno or 0, f"语法错误: {e.msg}")]
    bound = _bound_names(tree)
    if "*STAR*" in bound:
        return []
    hits: list[tuple[int, str]] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            if n.id not in bound and n.id not in _BUILTINS:
                hits.append((n.lineno, n.id))
    return hits


def main() -> None:
    roots = [Path(p) for p in (sys.argv[1:] or ["backend", "native/tools"])]
    files = [p for r in roots for p in ([r] if r.is_file() else r.rglob("*.py"))]
    total = 0
    for p in sorted(files):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        hits = scan(p)
        if hits:
            total += len(hits)
            print(f"{p}")
            for line, name in hits:
                print(f"    {p}:{line}  未绑定名: {name}")
    print(f"\n合计 {total} 处")


if __name__ == "__main__":
    main()
