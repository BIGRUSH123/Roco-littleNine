"""数据面 lint：`stat_stage` 之外的 op / attr / target 也必须有读取点（traits 一并纳入）。

背景（2026-09-22）：`SkillValidatePass` 只跑技能（`data/skills`），**不跑特性**
（`data/traits`，243 个文件、292 处 observer）。于是特性里 attr 写错（如 `power_mod`、
`swift` 这类"没有读取点的名字"）无人拦，落地时变成静默空操作——本文件就是补这一刀：

1. **attr**：`power_mod` / `mult_mod` / `flag_set` 的 attr 必须出现在「引擎读过的键」集合里。
   集合来自引擎自己的表（校验白名单、`_PER_TURN_KEYS`、`_SPRITE_LEVEL_ATTRS`、`_RATIO_STATS`…）
   **加上**对 `backend/` 源码里 `_modifiers...("key")` 形式的机械扫描——读过的键才允许写。
2. **target**：必须落在 `VALID_TARGETS`（或 `skill_at_N`）。
3. **op**：名字必须在 `backend/vm/executor.py` 里出现过（拼错立即暴露）。

数据改动走这里，比"跑一局看看"可靠：这条 lint 抓到的第一例就是
`定向精炼` / `拨浪鼓` 的 `attr:"power_mod"`（全引擎没有任何读取点）。
"""
import json
import re
from pathlib import Path

from backend.engine.replayer import _RATIO_STATS
from backend.engine.trait_loader import TraitLoader
from backend.sim.battle import _PER_TURN_KEYS, _SKILL_PER_TURN_KEYS
from backend.vm.compiler.passes.skill_validate import (
    VALID_MULT_ATTRS,
    VALID_POWER_ATTRS,
    VALID_STAGE_STATS,
    VALID_TARGETS,
)

_PROJ = Path(__file__).resolve().parent.parent.parent
_DATA = _PROJ / "data"
_BACKEND = _PROJ / "backend"

#: 引擎里作为 `_modifiers` 键被读过的 attr（机械扫描 + 显式补充）。
_RE_MOD_KEY = re.compile(r'_modifiers(?:\.get\(|\[)\s*[\'"]([a-z_][a-z0-9_]*)[\'"]')
_RE_MOD_KEY2 = re.compile(r'(?:skill_mods|bs_mods|mods|target_mods)\.get\(\s*[\'"]([a-z_][a-z0-9_]*)[\'"]')

#: 扫描抓不到、但确有读取点（读取形式特殊：写进集合/字典按变量取、或由 flag 语义消费）。
_EXTRA_READ_ATTRS = frozenset({
    # flag 语义（`_modifiers[flag]` 真值判定，读取点形如 `_modifiers.get(flag)` 带变量）
    "swift", "drive", "sealed", "burst", "cooldown", "pre_charged", "charge_any_skill",
    "extra_action", "extra_turn_end", "turn_end_block", "mark_coexist", "freeze_immune",
    "blood_price", "heal_reverse", "ignore_mods", "ignore_resistance", "survive",
    "attach_abnormal", "_burst_extended", "_cinder_grass",
    # 技能级 / 精灵级参数（读取点走 `BattleSkill` 属性、`snapshot.py` 的显式取值）
    "power", "energy_cost", "priority", "combo", "combo_set", "combo_mult",
    "power_mult", "damage_mult", "damage_reduction", "energy_cost_mult", "life_drain",
    "use_count_bonus", "max_energy", "energy_gain_delta", "starfall_consume_ratio",
    "element", "hp", "sprite_self",  # element/hp：目标过滤与自残用途
})


def _read_attr_set() -> set[str]:
    found: set[str] = set()
    for p in _BACKEND.rglob("*.py"):
        try:
            src = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        found |= set(_RE_MOD_KEY.findall(src))
        found |= set(_RE_MOD_KEY2.findall(src))
    found |= set(VALID_POWER_ATTRS) | set(VALID_MULT_ATTRS) | set(VALID_STAGE_STATS)
    found |= set(_PER_TURN_KEYS) | set(_SKILL_PER_TURN_KEYS) | set(_RATIO_STATS)
    found |= set(TraitLoader._SPRITE_LEVEL_ATTRS)
    found |= _EXTRA_READ_ATTRS
    return found


_READ_ATTRS = _read_attr_set()
_EXECUTOR_SRC = (_PROJ / "backend" / "vm" / "executor.py").read_text(encoding="utf-8")

_ATTR_OPS = ("power_mod", "mult_mod", "flag_set")


#: 特性侧 target 拼写（`replayer._target_sprite` 与 observer 路径解析；技能白名单不含它们）。
#: 由数据实际使用面收集，`test_all_targets_are_valid` 只在这里 + `VALID_TARGETS` 里放行，
#: 其余一律判为拼写错误。
_TRAIT_TARGETS = frozenset({
    "self", "own", "target", "entering", "ally_new", "enemy_new", "sprite_bench",
})


def _iter_ops(node, path="effects"):
    if isinstance(node, dict):
        if isinstance(node.get("op"), str):
            yield path, node
        for k, v in node.items():
            if k == "cond":            # 条件子树：里面的 "op" 是比较运算符，不是 VM op
                continue
            yield from _iter_ops(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _iter_ops(v, f"{path}[{i}]")


#: 不是 VM op：traits 的 observer 由 TraitToObserver 编译；比较运算符在条件里。
_NON_VM_OPS = frozenset({
    "observer", "eq", "gt", "gte", "lt", "lte", "neq", "not_in", "compare",
})


def _data_ops():
    for sub in ("skills", "traits"):
        for p in sorted((_DATA / sub).glob("*.json")):
            if p.name.startswith("_"):
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            doc = dict(d)
            doc["passive"] = d.get("passive", [])
            yield sub, p.name, _iter_ops(doc)


def test_all_op_attrs_have_readers():
    """traits + skills 全库：op 的 attr 必须真的被引擎读过（防静默空操作）。"""
    bad = []
    for sub, name, ops in _data_ops():
        for path, op in ops:
            if op["op"] not in _ATTR_OPS:
                continue
            attr = op.get("attr") or op.get("flag")
            if not isinstance(attr, str) or attr.startswith("="):
                continue
            if attr not in _READ_ATTRS:
                bad.append(f"{sub}/{name} {path} {op['op']} attr={attr!r}")
    assert not bad, "attr 没有读取点（写进去也不会生效）:\n  " + "\n  ".join(bad)


def test_all_targets_are_valid():
    bad = []
    for sub, name, ops in _data_ops():
        for path, op in ops:
            target = op.get("target")
            if not isinstance(target, str) or target.startswith("="):
                continue
            if (target in VALID_TARGETS or target in _TRAIT_TARGETS
                    or re.fullmatch(r"skill_at_\d+", target)):
                continue
            bad.append(f"{sub}/{name} {path} target={target!r}")
    assert not bad, "未知 target:\n  " + "\n  ".join(bad)


def test_all_ops_are_skipped_note():
    """op 名不在这里核对（说明，不是断言）。

    `backend/vm/executor.py` 的分发表是**程序化构建**的（按导入的 `op_*` 处理器组装），
    源码里没有 `"stat_stage": op_stat_stage` 这样的字面量，靠文本扫描无法可靠还原
    名字→处理器的映射；而技能侧的 op 名本来就会被 `SkillCompiler` 校验拦下。
    真正没人管的是 attr / target（`data/traits` 不走编译器），见本文件另两条测试。
    """


def test_no_zero_power_hit_descriptor():
    """无 `op` 的**命中描述**必须写 `power > 0`。

    这类 dict（形如 {"skill_type": "物攻", "power": N, "combo": M}）会被编译成
    `HitOp`，`power: 0` 一路走到 `calc_damage` 的 `power_term <= 0 → 0 伤`
    （实测：指指点点 写成 `power: 0` + `combo: 10`，实战 `-0HP`、连击数也不生效）。
    正确写法是**删掉这条描述**、改用顶层 `power` / `combo` 字段——隐式命中由
    `InjectHitPass` 注入（走 `power_self`，天然带上技能级威力修正）。
    """
    bad: list[str] = []
    for path in sorted((_DATA / "skills").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for eff in data.get("effects") or []:
            if not isinstance(eff, dict) or "op" in eff or "when" in eff:
                continue
            if int(eff.get("power") or 0) <= 0:
                bad.append(f"{path.stem}: {eff}")
    assert not bad, ("命中描述写了 power<=0（会打出 0 伤，应删掉并改用顶层 power/combo）:"
                     + "\n  " + "\n  ".join(bad))
