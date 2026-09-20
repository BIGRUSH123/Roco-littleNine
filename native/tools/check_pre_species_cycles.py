# -*- coding: utf-8 -*-
"""native/tools/check_pre_species_cycles.py — 校验精灵 pre_species 链是否有环。

背景（2026-09-20 定位）：Sprite.apply_moe → _build_moe_chain 沿 pre_species
向下走到最低形态，循环体内每次都调用 battle.lookup_species_by_number →
SpriteDB.lookup_by_number → _read_one → path.read_text()，即「每跳一次读一个
磁盘 JSON」。数据一旦成环（A.pre=B 且 B.pre=A，或 pre 指向自身编号），该循环
无界 → 单回合永不结束（实测第 414 局单回合 1065 秒，栈固定在 _read_one）。

本工具静态扫描全部编号，报告：
  cycle      —— pre_species 回到已访问编号
  self_ref   —— pre_species 等于自身编号
  too_long   —— 链路长度超过 --cap（数据或代码语义可疑）
退出码 1 表示发现问题（可接 CI）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.common.sprite_db import SpriteDB


def walk(db: SpriteDB, start_number: str, cap: int) -> tuple[str, list[str], str]:
    """从编号 start_number 起沿 pre_species 走，返回 (verdict, chain, last)。"""
    chain: list[str] = []
    seen: set[str] = set()
    cur = start_number
    while cur:
        if cur in seen:
            return "cycle", chain, cur
        if len(seen) >= cap:
            return "too_long", chain, cur
        seen.add(cur)
        sp = db.lookup_by_number(cur)
        if sp is None:
            return "ok", chain, cur
        tail = f"（{sp.appearance}）" if sp.appearance else ""
        chain.append(f"{sp.number} {sp.name}{tail}")
        if sp.pre_species == sp.number:
            return "self_ref", chain, cur
        cur = sp.pre_species or ""
    return "ok", chain, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=40, help="链路长度上限（超过记为 too_long）")
    ap.add_argument("--show-ok", action="store_true", help="同时打印正常链路")
    ap.add_argument("--grep", default="", help="只打印名字/编号含该子串的链路")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[2]
    db = SpriteDB(root)
    numbers = sorted(db._by_number)
    verdict_counts: dict[str, int] = {}
    bad: list[tuple[str, str, list[str]]] = []
    chains: dict[str, list[str]] = {}

    for number in numbers:
        verdict, chain, last = walk(db, number, args.cap)
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        chains[number] = chain
        if verdict != "ok":
            bad.append((verdict, number, chain + [f"<-{last}"]))
            print(f"[{verdict}] 起点 {number}: " + " → ".join(chain) + f" → (回到 {last})")

    if args.grep:
        print(f"\n=== 含 '{args.grep}' 的链路 ===")
        for number, chain in chains.items():
            joined = " → ".join(chain)
            if args.grep in joined:
                print(f"  {number}: {joined or '(空)'}")

    if args.show_ok:
        print("\n=== 正常链路 ===")
        for number, chain in chains.items():
            print(f"  {number}: " + " → ".join(chain) + f"  (len={len(chain)})")

    print(f"\n编号总数 {len(numbers)}，判定统计 {verdict_counts}")
    multi = {n: c for n, c in chains.items() if len(c) > 1}
    print(f"可退化链路（len>1）{len(multi)} 条，最长 "
          f"{max((len(c) for c in chains.values()), default=0)}")
    if bad:
        print(f"\n!! 发现 {len(bad)} 条异常链路——必须先修数据/加环保护")
        return 1
    print("pre_species 链无环 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
