# -*- coding: utf-8 -*-
"""按线上 wiki 推荐培养给精灵分定位桶（攻击手/辅助/坦克）。

为什么换掉旧逻辑：`train.py::_sprite_roles` 原来用「双攻前 40% / 耐久前 30% /
工具技能占比 ≥60%」三个阈值分桶且**没有兜底桶** → 344 个池条目里 114 个
（77 个物种）结构性不可采，约 19% 物种在训练数据里零出场（审计见
native/tools/audit_selfplay_pool.py）。wiki 的培养参考覆盖每一个形态，
用它分类既语义正确（wiki 是权威）又不会漏精灵。

数据：`native/tools/import_training_reference.py` 产出的
`backend/engine/ai/data/training_reference.json`，按**编号**组织（同编号多形态
各一条记录），对齐键 = 编号而非名字（本地部分外观形态名字被折叠，且同编号同外观
可能不同名，如 伊兰龙/伊兰亚龙）。

分类规则（对齐 wiki《性格选择原则》表的 高速压制/物攻输出/魔攻输出/坦克/双刀）：
  由该形态 pvp 模式的天赋权重（回退 world/default）算两个倾向：
    atk  = (物攻 + 魔攻 + 0.5×速度) / 总权重
    bulk = (生命 + 物防 + 魔防) / 总权重
  1. bulk ≥ 0.55 → 坦克（明显的墙）
  2. atk ≥ bulk  → 攻击手（含速度流/高速压制；速度记半权仍领先即算输向）
  3. 其余 → 看推荐技能里非攻击（状态/防御/变化）占比：≥ 0.5 → 辅助，否则攻击手
  另：非辅助但技能工具占比 ≥ 0.5 的，额外挂进辅助桶（保留旧的「可跨桶」语义）。

覆盖面契约：`classify_roles()` 返回的并集必须包含全部池条目——无 wiki 数据的
形态回落到旧的数值分位法，仍落空的按 双攻 vs 耐久 兜底，并记录来源便于审计。
"""
from __future__ import annotations

import json
from pathlib import Path

_REF_CACHE: dict | None = None
_SKILL_TYPE_CACHE: dict[str, str] = {}

ROOT = Path(__file__).resolve().parents[4]  # data/→ai/→engine/→backend/→仓库根
REF_PATH = ROOT / "backend" / "engine" / "ai" / "data" / "training_reference.json"

TALENT_ATK = ("物攻", "魔攻")
TALENT_BULK = ("生命", "物防", "魔防")
DEFAULT_FORMS = ("本来的样子", "")
BULK_TANK_SHARE = 0.55
ATK_ATTACKER_SHARE = 0.55
UTILITY_SUPPORT_SHARE = 0.5


def _skill_type(name: str) -> str:
    """技能类型（物攻/魔攻/防御/状态…），带进程级缓存。"""
    if name not in _SKILL_TYPE_CACHE:
        p = ROOT / "data" / "skills" / f"{name}.json"
        try:
            _SKILL_TYPE_CACHE[name] = json.loads(
                p.read_text(encoding="utf-8")).get("skill_type", "") if p.exists() else ""
        except (OSError, json.JSONDecodeError):
            _SKILL_TYPE_CACHE[name] = ""
    return _SKILL_TYPE_CACHE[name]


def load_reference(path: Path | None = None) -> dict:
    """加载培养参考（按编号组织）；文件缺失时返回空表（调用方自动回退分位法）。"""
    global _REF_CACHE
    if _REF_CACHE is not None:
        return _REF_CACHE
    p = Path(path) if path else REF_PATH
    _REF_CACHE = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return _REF_CACHE


def _mode_block(entry: dict, mode: str = "pvp") -> dict:
    for key in (mode, "world", "default"):
        blk = entry.get(key)
        if blk and blk.get("talent"):
            return blk
    return entry.get("default") or {}


def _merged_block(entries: list[dict], mode: str = "pvp") -> dict:
    """同编号多条形态 → 权重求和合并（名字对不上时用编号级倾向）。"""
    acc: dict[str, dict[str, float]] = {}
    for e in entries:
        blk = _mode_block(e, mode)
        for cat in ("talent", "skill"):
            for nm, w in blk.get(cat) or []:
                acc.setdefault(cat, {})
                acc[cat][nm] = acc[cat].get(nm, 0.0) + float(w)
    return {cat: sorted(d.items(), key=lambda kv: -kv[1]) for cat, d in acc.items()}


def _shares(block: dict) -> tuple[float, float]:
    ranked = block.get("talent") or []
    total = sum(float(w) for _n, w in ranked) or 1.0
    w = {n: float(x) for n, x in ranked}
    atk = (sum(w.get(t, 0.0) for t in TALENT_ATK) + 0.5 * w.get("速度", 0.0)) / total
    bulk = sum(w.get(t, 0.0) for t in TALENT_BULK) / total
    return atk, bulk


def _utility_share(block: dict, top: int = 10) -> float | None:
    """推荐技能（按权重前 top 个）里非攻击技能的权重占比；无数据返回 None。"""
    ranked = (block.get("skill") or [])[:top]
    if not ranked:
        return None
    util = atk = 0.0
    for nm, w in ranked:
        if _skill_type(nm) in ("物攻", "魔攻"):
            atk += float(w)
        else:
            util += float(w)
    total = util + atk
    return (util / total) if total else None


def pick_entry(by_number: dict, number: str, name: str, form: str) -> tuple[dict | None, str]:
    """按 编号 + (名字, 形态/外观) 取参考记录；返回 (block 来源块, 匹配方式)。"""
    node = by_number.get(str(number).zfill(3))
    if not node:
        return None, "无编号数据"
    entries = node.get("entries") or []
    if not entries:
        return None, "无形态数据"
    for e in entries:                     # 精确：名字 + 形态/外观
        if e.get("name") == name and (e.get("form") or "") == (form or ""):
            return _mode_block(e), "精确匹配"
    same_name = [e for e in entries if e.get("name") == name]
    if len(same_name) == 1:
        return _mode_block(same_name[0]), "同名匹配"
    if same_name:
        # wiki 未列出该外观（本地外观更全/分支命名差异）：同名多形态合并，
        # 避免按文件顺序任取一条（如 鸭吉吉国王 的 5 个外观条目）。
        return _merged_block(same_name), "同名合并"
    return _merged_block(entries), "编号合并"


def classify_block(block: dict) -> tuple[str, float, float, float | None]:
    """(角色, atk 倾向, bulk 倾向, 工具占比) —— 规则见模块 docstring。"""
    atk, bulk = _shares(block)
    util = _utility_share(block)
    if bulk >= BULK_TANK_SHARE:
        return "tank", atk, bulk, util
    if atk >= bulk:
        return "attacker", atk, bulk, util
    if util is not None and util >= UTILITY_SUPPORT_SHARE:
        return "support", atk, bulk, util
    return "attacker", atk, bulk, util


def classify_roles(db, sprite_skills: dict[str, list[str]]) -> dict:
    """给全部池条目分桶；保证并集覆盖 sprite_skills 的每个键。

    返回 {'attackers': set, 'tanks': set, 'supports': set,
          'source': {name: 匹配方式}, 'detail': {name: (atk, bulk, util)}}
    """
    ref = load_reference()
    by_number = ref.get("by_number", {})
    attackers: set[str] = set()
    tanks: set[str] = set()
    supports: set[str] = set()
    source: dict[str, str] = {}
    detail: dict[str, tuple] = {}

    for name in sprite_skills:
        sp = db.get(name)
        if sp is None:
            continue
        block, how = pick_entry(by_number, sp.number, sp.name,
                                sp.appearance or sp.form)
        if block:
            role, atk, bulk, util = classify_block(block)
            source[name] = how
            detail[name] = (atk, bulk, util)
            (tanks if role == "tank" else supports if role == "support" else attackers).add(name)
            # 保留旧的「可跨桶」语义：工具技能占优的攻击手/坦克也进辅助桶
            if role != "support" and util is not None and util >= UTILITY_SUPPORT_SHARE:
                supports.add(name)
        else:
            source[name] = how
    return {"attackers": attackers, "tanks": tanks, "supports": supports,
            "source": source, "detail": detail}
