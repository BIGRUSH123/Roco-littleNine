# -*- coding: utf-8 -*-
"""native/tools/audit_selfplay_pool.py — 自博弈精灵池体检。

三类独立失败路径，任何一条出问题都会让「随机阵容」这一半训练数据失真：
  A. 池子数据本身：技能数不足（精灵上场只能聚能/换人）、技能 JSON 缺失
     （`_build_skill_list` 会静默跳过 → 该技能位消失）、名字无法解析。
  B. 角色分桶：`_sprite_roles` 按数值分攻击手/辅助/坦克；某桶过小或为空时
     `build_team` 只能回退全池，导致队伍角色结构退化。
  C. 实际对局分布：队伍人数、队内重复、精灵覆盖率、频次是否退化。

用法:
  python native/tools/audit_selfplay_pool.py [battles jsonl 通配符]
"""
from __future__ import annotations

import glob as _glob
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL
from backend.sim.factory import SimFactory


def audit_pool_data() -> dict:
    factory = SimFactory()
    skills_dir = ROOT / "data" / "skills"
    counts = Counter()
    short = []          # 技能位 < 4 的精灵
    missing_total = Counter()
    missing_examples = []
    unresolved = []
    for name, skills in SPRITE_RANDOM_POOL.items():
        counts[len(skills)] += 1
        if len(skills) < 4:
            short.append((name, len(skills)))
        for sk in skills:
            if not (skills_dir / f"{sk}.json").exists():
                missing_total[sk] += 1
                if len(missing_examples) < 8:
                    missing_examples.append((name, sk))
        if factory.sprite_db.get(name) is None:
            unresolved.append(name)

    print(f"=== A. 池子数据（{len(SPRITE_RANDOM_POOL)} 只）===")
    print("  技能位分布: " + "  ".join(f"{k}技能={v}只" for k, v in sorted(counts.items())))
    print(f"  技能位 < 4 的精灵: {len(short)} 只" + (f"  例: {short[:6]}" if short else ""))
    print(f"  缺失技能 JSON 的引用: {sum(missing_total.values())} 处，"
          f"涉及 {len(missing_total)} 个技能名")
    if missing_examples:
        print(f"    例: {missing_examples}")
    print(f"  名字无法解析（sprite_db 查不到）: {len(unresolved)} 只"
          + (f"  例: {unresolved[:6]}" if unresolved else ""))
    odd = sorted(((n, len(s)) for n, s in SPRITE_RANDOM_POOL.items() if len(s) > 50),
                 key=lambda kv: -kv[1])
    if odd:
        print(f"  技能池异常大的条目（>50）: {odd}  ← 可疑，正常 16-40")
    return {"short": short, "unresolved": unresolved, "missing": missing_total}


def audit_roles() -> None:
    from backend.engine.ai.train import _sprite_roles

    roles = _sprite_roles(SimFactory(), dict(SPRITE_RANDOM_POOL))
    print(f"\n=== B. 角色分桶 ===")
    for key in sorted(roles):
        if key == "info":
            continue
        names = roles[key]
        print(f"  {key:<12} {len(names):>4} 只")
    names_all = set(SPRITE_RANDOM_POOL)
    covered: set[str] = set()
    for key, v in roles.items():
        if key != "info":
            covered |= set(v)
    print(f"  分桶覆盖 {len(covered)} / {len(names_all)} 只；"
          f"未进任何桶 {len(names_all - covered)} 只")


def base_of(name: str) -> str:
    """去掉末尾「（外观）」得基础名——对局日志只有 Sprite.name（基础名），
    外观变体共用基础名，按全名统计会把「变体被采样」误判成「从未出现」。"""
    return name[:name.rindex("（")] if name.endswith("）") and "（" in name else name


def audit_log(pattern: str) -> None:
    from collections import defaultdict

    files = [Path(p) for p in sorted(_glob.glob(str(ROOT / pattern)))]
    if not files:
        print(f"\n=== C. 对局分布 ===\n  没找到日志: {pattern}")
        return
    pool = set(SPRITE_RANDOM_POOL)
    pool_by_base: dict[str, list[str]] = defaultdict(list)
    for n in pool:
        pool_by_base[base_of(n)].append(n)

    sizes = Counter()
    base_repeat = 0
    true_dup = 0
    seen_base = Counter()
    n_teams = 0
    for path in files:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "teams" not in rec:
                    continue
                for side in ("A", "B"):
                    team = rec["teams"].get(side, [])
                    n_teams += 1
                    sizes[len(team)] += 1
                    for nm in team:
                        seen_base[nm] += 1
                    reps = [n for n in set(team) if team.count(n) > 1]
                    if reps:
                        base_repeat += 1
                        # 该基础名在池中只有 1 个条目 → 真重复（非法阵容）
                        if all(len(pool_by_base.get(n, [])) <= 1 for n in reps):
                            true_dup += 1

    pool_bases = set(pool_by_base)
    seen_pool_bases = set(seen_base) & pool_bases
    print(f"\n=== C. 对局分布（{n_teams} 支队伍）===")
    print("  队伍人数分布: " + "  ".join(f"{k}人={v}" for k, v in sorted(sizes.items())))
    print(f"  基础名重复的队伍 {base_repeat} ({base_repeat / max(1, n_teams):.2%})，"
          f"其中池中无第二个变体（真重复/非法）{true_dup} ({true_dup / max(1, n_teams):.3%})")
    print(f"  池中基础物种覆盖: {len(seen_pool_bases)}/{len(pool_bases)} "
          f"({len(seen_pool_bases) / max(1, len(pool_bases)):.1%})"
          f"  ← 日志只有基础名，变体无法区分")
    not_seen = sorted(pool_bases - seen_pool_bases)
    print(f"  从未出现的基础物种: {len(not_seen)} 只" + (f"  例: {not_seen[:10]}" if not_seen else ""))
    total = sum(seen_base.values())
    print(f"  每只基础物种平均上场 {total / max(1, len(pool_bases)):.1f} 次")
    print("  最常出现 10 只: " + "  ".join(f"{n}={c}" for n, c in seen_base.most_common(10)))

    # 结构性排除：不在任何角色桶的池条目，按「变体 / 独立物种」分类
    from backend.engine.ai.train import _sprite_roles

    roles = _sprite_roles(SimFactory(), dict(SPRITE_RANDOM_POOL))
    bucketed = set(roles["attackers"]) | set(roles["supports"]) | set(roles["tanks"])
    no_bucket = [n for n in pool if n not in bucketed]
    no_bucket_variant = [n for n in no_bucket if base_of(n) != n]
    no_bucket_base = [n for n in no_bucket if base_of(n) == n]
    print(f"\n  无角色桶的池条目 {len(no_bucket)}/{len(pool)}:"
          f" 外观变体 {len(no_bucket_variant)} 只 + 基础物种 {len(no_bucket_base)} 只")
    # 变体进不了桶没关系（同物种其他外观可被采到）；真正的问题是整个物种都不在桶里
    orphan_species = sorted({base_of(n) for n in no_bucket
                             if not (set(pool_by_base[base_of(n)]) & bucketed)})
    print(f"  !! 完全无法被随机阵容采到的物种（含其全部外观）: {len(orphan_species)} 只")
    print(f"     例: {orphan_species[:12]}")
    reachable = len(pool_by_base) - len(orphan_species)
    print(f"  随机阵容实际可采物种上限: {reachable}/{len(pool_by_base)}"
          f" ({reachable / max(1, len(pool_by_base)):.1%})")


def main() -> int:
    pattern = sys.argv[1] if len(sys.argv) > 1 else \
        "backend/engine/ai/log/exp23_bc/battles_run_*.jsonl"
    audit_pool_data()
    audit_roles()
    audit_log(pattern)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
