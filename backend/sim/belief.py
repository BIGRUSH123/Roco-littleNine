"""backend/sim/belief.py — 对手下一步动作的**概率**估计（手设先验 + 可校准）。

为什么需要它（E0 实测，300 局 / 13390 个决策点）：对手实际换人率 23.5%，而
"它换"与"它不换"两种世界要求不同技能的决策点占 **23.4%**；押错方向平均损失
7.3% 最大生命（p90 29.9%，最高一条命）——所以"预判"不是玄学，是能量化的一手棋。
（度量工具：`native/tools/measure_prediction_stakes.py`。）

本模块只回答"对手下一步大概是哪一类"，把布尔预判升级成分布：
列 = `{attack, defense, status, switch, gather}`（**粗粒度是刻意的**——精确预测
具体技能不可能，而洛克王国世界的博弈维度就是技能类别 + 换人）。
概率按**引擎可算的事实**打分后归一化，权重集中在一个 dataclass 里，方便：
  - 手工调参；
  - 后面用数据校准（`load_calibrated()` + `features()` 输出可直接落盘做拟合）。

wiki 佐证（本地 `wiki/对战机制/04-换人与脱战.md:51-55`）：
  "对方无能量→大概率聚能或换人；对方被克制→可能换人；换人清增益→叠 Buff 慎换"。
另有硬事实：防御技能使用后进一回合冷却（`wiki/对战机制/02-应对机制.md:50-54`），
所以"它能不能防御"是可判定的。

复杂公式一律不写在这里：伤害/出手顺序/印记致死走 `tactics.py` 与引擎解析器。
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from . import tactics

SCENARIOS = ("attack", "defense", "status", "switch", "gather")


@dataclass(frozen=True)
class BeliefParams:
    """信念先验参数（全部是可解释的"信号 → logit 增量"）。"""

    # ── 换人分支 ──
    switch_base: float = -1.20        # 基线换人倾向（logit；实测有信号时平均换人率 23.5%）
    w_lethal: float = 1.60            # 我方能一击斩杀它（不逃就死）
    w_tick_doom: float = 2.00         # 它这回合结束会被 tick 死（换人正好躲掉）
    w_countered: float = 0.80         # 它被我方属性克制（wiki：被克制→可能换人）
    w_low_energy: float = 0.70        # 它能量不足以出手（wiki：无能量→大概率聚能或换人）
    w_hp_low: float = 0.90            # 它血线低（残血求稳）
    w_just_entered: float = -1.20     # 它刚换上过（换人有 tempo 成本，短期不再换）
    # ── 留场分支的相对权重（乘以各自信号强度）──
    stay_defense: float = 1.10        # 防御：怕我这回合的输出（我的最高伤害占它血量比例）
    stay_status: float = 0.90         # 状态/强化：我不太能威胁它时就地强化（wiki：叠 Buff 慎换）
    gather_energy: float = 0.60       # 聚能：能量不够打，又不想换人
    floor_stay: float = 0.05          # 保底：每类留场动作至少留一点概率


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


def _affordable_attacks(sprite) -> list:
    return [s for s in (getattr(sprite, 'skills', ()) or ())
            if s.is_attack and s.cooldown <= 0 and not s.sealed
            and s.energy_cost <= getattr(sprite, 'energy', 0)]


def _usable_defense(sprite) -> list:
    return [s for s in (getattr(sprite, 'skills', ()) or ())
            if s.is_defense and s.cooldown <= 0 and not s.sealed]


def _usable_status(sprite) -> list:
    """可用的强化/状态技。

    旧判据（`e.kind == 'stat'`，旧 kind 层）随该层于 2026-09-22 删除：IR 语料下
    它恒不成立，本函数**一直返回空列表**（`has_stat` 是常数 False）。保持行为不变
    故按「无结果」保留；要真正识别强化技请改用 `skill_ir.skill_profile`
    （`self_buff_stats` / `doubles_buffs`…），属需单独量测的决策改动。
    """
    return []


def _type_advantage(battle, attacker, defender, team: str) -> float:
    """我方最强攻击对它的属性倍率（>1 = 我克制它）。"""
    from .resolver import SkillResolver

    best = 0.0
    for skill in _affordable_attacks(attacker):
        try:
            best = max(best, SkillResolver._get_type_mult(skill, attacker, defender))
        except (AttributeError, TypeError):
            continue
    return best


def features(battle, my_team: str, opp_player, my_best_dmg: int) -> dict[str, float]:
    """换人分支的 logit 特征（可直接落盘做校准）。"""
    opp = getattr(opp_player, 'active', None)
    bench = [i for i in (getattr(opp_player, 'alive_sprites', None) or [])
             if i != getattr(opp_player, 'active_index', -1)]
    if opp is None or getattr(opp, 'current_hp', 0) <= 0:
        return {"no_active": 1.0}
    max_hp = max(1, opp.max_hp)
    hp_ratio = opp.current_hp / max_hp
    opp_team = tactics.opponent_team(my_team)
    attacks = _affordable_attacks(opp)
    my_sprite = _my_active(battle, my_team)
    counter_mult = (_type_advantage(battle, my_sprite, opp, my_team)
                    if my_sprite is not None else 1.0)
    return {
        "lethal": 1.0 if my_best_dmg >= opp.current_hp else 0.0,
        "tick_doom": 1.0 if tactics.predict_turn_end_damage(battle, opp, opp_team) >= opp.current_hp else 0.0,
        "countered": 1.0 if counter_mult > 1.0 else 0.0,
        "no_attack": 0.0 if attacks else 1.0,
        "hp_low": max(0.0, 1.0 - hp_ratio / 0.5) if hp_ratio < 0.5 else 0.0,
        "just_entered": 1.0 if battle.turn - getattr(opp, 'entry_turn', 0) <= 1 else 0.0,
        "has_bench": 1.0 if bench else 0.0,
        "my_threat": min(1.0, my_best_dmg / max_hp),
    }


def _my_active(battle, my_team: str):
    player = battle.get_player(my_team) if hasattr(battle, 'get_player') else None
    return getattr(player, 'active', None)


def switch_probability(battle, my_team: str, opp_player, my_best_dmg: int,
                       params: BeliefParams | None = None) -> float:
    """P(对手本回合换人)。没有替补可换 → 0。"""
    p = params or BeliefParams()
    f = features(battle, my_team, opp_player, my_best_dmg)
    if not f.get("has_bench") or f.get("no_active"):
        return 0.0
    logit = (
        p.switch_base
        + p.w_lethal * f["lethal"]
        + p.w_tick_doom * f["tick_doom"]
        + p.w_countered * f["countered"]
        + p.w_low_energy * f["no_attack"]
        + p.w_hp_low * f["hp_low"]
        + p.w_just_entered * f["just_entered"]
    )
    return _sigmoid(logit)


def belief(battle, my_team: str, opp_player, my_best_dmg: int,
           params: BeliefParams | None = None) -> dict[str, float]:
    """对手下一步动作的分布（五项归一化；不可用的分支概率为 0）。

    `attack` = 它用攻击技；`defense` = 用防御技（应对攻击）；`status` = 强化/状态技
    （应对防御）；`switch` = 换人；`gather` = 聚能/无事可做。
    """
    p = params or BeliefParams()
    opp = getattr(opp_player, 'active', None)
    if opp is None or getattr(opp, 'current_hp', 0) <= 0:
        return {k: (1.0 if k == "attack" else 0.0) for k in SCENARIOS}

    p_switch = switch_probability(battle, my_team, opp_player, my_best_dmg, p)
    rest = 1.0 - p_switch

    max_hp = max(1, opp.max_hp)
    threat = min(1.0, my_best_dmg / max_hp)          # 我这回合对它的压力
    attacks = _affordable_attacks(opp)
    has_def = bool(_usable_defense(opp))
    has_stat = bool(_usable_status(opp))
    weights = {
        "attack": 1.0,
        # 防御：怕我这回合的输出就举盾（防御技在冷却 → 结构上不可能，wiki 02）
        "defense": p.stay_defense * threat,
        # 状态/强化：我不太能威胁它时就地叠 Buff（wiki 04：换人清增益，慎换）
        "status": p.stay_status * (1.0 - threat),
        # 聚能：打不出攻击时是主要选择；能打时也可能攒大招（0.3 倍）
        "gather": p.gather_energy * (1.0 if not attacks else 0.3),
    }
    # 保底只给**结构上可用**的分支（否则"没带防御技"会变成"有 5% 概率防御"）
    weights["attack"] = max(weights["attack"], p.floor_stay) if attacks else 0.0
    weights["defense"] = max(weights["defense"], p.floor_stay) if has_def else 0.0
    weights["status"] = max(weights["status"], p.floor_stay) if has_stat else 0.0
    total = sum(weights.values()) or 1.0

    out = {k: 0.0 for k in SCENARIOS}
    out["switch"] = p_switch
    for key, w in weights.items():
        out[key] = rest * w / total
    return out


# ══════════════════════════════════════════════════════════════════
# 校准接口
# ══════════════════════════════════════════════════════════════════

def load_calibrated(path: str | Path) -> BeliefParams:
    """从 JSON 读校准后的参数（缺字段用默认值）；文件缺失/损坏时返回默认参数。"""
    p = Path(path)
    if not p.exists():
        return BeliefParams()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return BeliefParams()
    valid = {f.name for f in fields(BeliefParams)}
    return BeliefParams(**{k: float(v) for k, v in raw.items() if k in valid})


def save_params(params: BeliefParams, path: str | Path) -> None:
    """把参数写成 JSON（校准工具的产出格式）。"""
    Path(path).write_text(
        json.dumps(asdict(params), ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8")
