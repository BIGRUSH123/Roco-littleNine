"""backend/sim/ev.py — 一手棋的期望值：信念 × 收益矩阵（专家决策层）。

背景：E0 实测（300 局 / 13390 决策点）对手换人率 23.5%，而"它换 / 它不换"要求不同
技能的决策点占 23.4%，押错方向平均损失 7.3% 最大生命——所以"预判"值得用概率处理，
而不是当前这种"要么假设它不换、要么假设它换"的二选一。

表示：本回合是一个**同时出招**的子博弈。
  - 行 = 我的候选动作（每个可用技能、每个可行替补、聚能；道具与首领化由
    `item_policy` 先处理掉，不参与矩阵）；
  - 列 = 对手行为假设（`belief.SCENARIOS`：攻击/防御/状态/换人/聚能）；
  - 单元格 = 本回合战术价值 `payoff(我的动作, 对手动作)`。

价值口径（占**各自**最大生命的比例，可跨行比较）：
    `value = 它掉血率 − 我掉血率 + kill_bonus·(它倒 − 我倒) + 能量/强化收益 − 应对/冷却/进场惩罚`

**应对三角**（wiki《02-应对机制》，严格石头剪刀布）用引擎自己的
`SkillResolver.resolve_counter()` 判定：防御技反制攻击、攻击技反制状态、状态技反制防御。
应对成功 = 应对方技能的减伤/`counter_succeeded` 威力倍率先落地，被应对方那一手被削弱。
**出手顺序完全照引擎**：应对成立 → A 方强制先出手（`battle.py:1103`，双方 `is_first=True`）；
否则 先手值 → 印记减速后的速度（`battle.py:1136`）；**换人先于技能**（`battle.py:1056`），
所以"我换人时吃伤害的是换上来那只"。

已知近似（都在 EVParams 里可调，且文档写明）：
  - 只算**本回合**：强化的未来收益用 `buff_credit` 折算、换人的未来对位优势不计 → 换人行的
    价值被低估（与 E0 同口径）；
  - 对手状态技/防御技的 debuff 通过 `status_tempo_penalty` 折算，不做逐效果建模；
  - 具体的减伤/威力倍率从技能 JSON 读（`damage_reduction` / `counter_succeeded` 的
    `power_mult`），没有的部分用参数兜底。
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path

from . import belief as belief_mod
from . import tactics
from .resolver import SkillResolver

SCENARIOS = belief_mod.SCENARIOS
ROOT = Path(__file__).resolve().parents[2]
_SKILLS_DIR = ROOT / "data" / "skills"


@dataclass(frozen=True)
class EVParams:
    """收益模型的折算参数（全部可解释、可校准）。"""

    kill_bonus: float = 0.50          # 打倒一只的价值（按"条命"折算成血量比例）
    buff_credit: float = 0.15         # 一次强化的未来收益（"白嫖强化"那条线的价值）
    gather_energy_gain: int = 5       # 聚能的能量收益（引擎聚能回复量）
    energy_weight: float = 0.02       # 每点能量折算的价值
    defense_cooldown_penalty: float = 0.10   # 防御技进冷却的后续代价（wiki：猜错被抓亏两回合）
    countered_penalty: float = 0.05   # 被应对成功时白吃 debuff 的折算
    status_tempo_penalty: float = 0.02  # 对方状态技落下后（我掉血率为 0 时）的隐性代价
    switch_incoming_weight: float = 1.0  # 换人时"换上来那只吃伤害"的权重（1.0 = 照实算）
    switch_position_weight: float = 0.30  # 换人的**对位收益**（换上来那只的输出优势）折算权重
    risk_lambda: float = 0.0          # 风险厌恶：score = EV − λ·(最好格 − 最坏格)


@dataclass(frozen=True)
class Candidate:
    """我的一个候选动作。"""

    kind: str        # 'skill' | 'switch' | 'gather'
    index: int       # 技能槽位 / 替补的 team 索引
    label: str = ""

    def __str__(self) -> str:  # 便于日志/测试断言
        return self.label or f"{self.kind}#{self.index}"


# ══════════════════════════════════════════════════════════════════
# 技能 JSON 里的应对/减伤参数
# ══════════════════════════════════════════════════════════════════

_SKILL_JSON: dict[str, dict] = {}


def skill_data(name: str) -> dict:
    """技能 JSON（带缓存）；缺文件返回空 dict。"""
    if name not in _SKILL_JSON:
        path = _SKILLS_DIR / f"{name}.json"
        try:
            _SKILL_JSON[name] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            _SKILL_JSON[name] = {}
    return _SKILL_JSON[name]


def defense_reduction(skill) -> float:
    """防御技的减伤比例（`effects` 里无 when 的 `damage_reduction` 最大值）。"""
    best = 0.0
    for eff in (skill_data(getattr(skill, 'name', '')).get("effects") or []):
        if eff.get("attr") == "damage_reduction" and not eff.get("when"):
            try:
                best = max(best, float(eff.get("value", 0) or 0))
            except (TypeError, ValueError):
                continue
    return min(0.95, best)


def counter_power_mult(skill) -> float:
    """应对成功时的威力倍率（如 偷袭 `counter_succeeded` → ×3）；没有则 1.0。"""
    best = 1.0
    for eff in (skill_data(getattr(skill, 'name', '')).get("effects") or []):
        if "counter_succeeded" not in json.dumps(eff.get("when") or {}, ensure_ascii=False):
            continue
        for sub in eff.get("then") or []:
            if sub.get("attr") == "power_mult":
                try:
                    best = max(best, float(sub.get("value", 1) or 1))
                except (TypeError, ValueError):
                    continue
    return best


# ══════════════════════════════════════════════════════════════════
# 对手行为假设（列）与代表技能
# ══════════════════════════════════════════════════════════════════

def _affordable(sprite, predicate) -> list:
    return [s for s in (getattr(sprite, 'skills', ()) or ())
            if s.cooldown <= 0 and not s.sealed and s.energy_cost <= getattr(sprite, 'energy', 0)
            and predicate(s)]


def brief_stats(sprite):  # 便于测试构造对手替身
    return sprite


def likely_switch_in(battle, me, opp_player, my_team: str):
    """对手最可能换上谁：对它最安全（我打它最不疼）又最有威胁的那只。"""
    opp_team = tactics.opponent_team(my_team)
    best, best_score = None, None
    for idx in (getattr(opp_player, 'alive_sprites', None) or []):
        if idx == getattr(opp_player, 'active_index', -1):
            continue
        sprite = opp_player.team[idx]
        incoming = max(
            (_damage(battle, me, sprite, s, my_team)
             for s in _affordable(me, lambda s: s.is_attack)), default=0)
        outgoing = max(
            (_damage(battle, sprite, me, s, opp_team)
             for s in _affordable(sprite, lambda s: s.is_attack)), default=0)
        score = outgoing / max(1, sprite.max_hp) - incoming / max(1, me.max_hp)
        if best_score is None or score > best_score:
            best, best_score = sprite, score
    return best


def scenario_context(battle, me, my_team: str, opp_player) -> dict[str, tuple]:
    """每个列 → (防守方精灵, 对手代表技能)。换人列的技能为 None。"""
    opp = getattr(opp_player, 'active', None)
    ctx: dict[str, tuple] = {}
    if opp is None:
        return ctx
    attacks = _affordable(opp, lambda s: s.is_attack)
    defenses = _affordable(opp, lambda s: s.is_defense)
    statuses = _affordable(opp, lambda s: not s.is_attack and not s.is_defense)
    opp_team = tactics.opponent_team(my_team)
    ctx["attack"] = (opp, max(attacks, key=lambda s: _damage(battle, opp, me, s, opp_team))
                     if attacks else None)
    ctx["defense"] = (opp, defenses[0] if defenses else None)
    ctx["status"] = (opp, statuses[0] if statuses else None)
    ctx["gather"] = (opp, None)
    switch_in = likely_switch_in(battle, me, opp_player, my_team)
    ctx["switch"] = (switch_in, None)
    return ctx


def _damage(battle, attacker, defender, skill, team: str, is_first: bool = False) -> int:
    """伤害估算；`is_first` 必须传真实出手顺序——印记倍率（mark_damage_mult）就吃它。"""
    if skill is None or defender is None or getattr(defender, 'current_hp', 0) <= 0:
        return 0
    from .battleskill import SkillUse

    dmg, _ = battle._resolver.calc_damage(
        attacker, defender, SkillUse(battle_skill=skill, is_first=is_first),
        battle.globals, attacker_team=team)
    return dmg


# ══════════════════════════════════════════════════════════════════
# 单元格价值
# ══════════════════════════════════════════════════════════════════

def _best_outgoing_damage(battle, attacker, defender, team: str) -> int:
    """该精灵对防守方的最强可负担攻击伤害（换人对位收益用）。"""
    return max((_damage(battle, attacker, defender, s, team)
                for s in _affordable(attacker, lambda s: s.is_attack)), default=0)


def acts_first(battle, me, my_team: str, my_skill, opp, opp_team: str, their_skill,
               my_switch: bool = False) -> bool:
    """我这一手会不会先结算（照引擎：换人最先；有应对 → A 方强制先手；否则先手值→速度）。"""
    if my_switch:
        return True
    counters = (my_skill is not None and their_skill is not None and (
        SkillResolver.resolve_counter(my_skill, their_skill)
        or SkillResolver.resolve_counter(their_skill, my_skill)))
    if counters:
        return my_team == 'A'          # battle.py:1103 应对成立时 A 先执行
    my_p = tactics.attack_priority(me, my_skill) if my_skill is not None else 0
    their_p = tactics.attack_priority(opp, their_skill) if their_skill is not None else 0
    if my_p != their_p:
        return my_p > their_p
    return (tactics.effective_speed(battle, me, my_team)
            >= tactics.effective_speed(battle, opp, opp_team))


def payoff_terms(battle, me, my_player, opp_player, my_cand: Candidate, scenario: str,
                 context: dict, params: EVParams, my_team: str) -> dict[str, float]:
    """我这一手在"对手走 `scenario`"时的**本回合价值分解**（可对拍引擎真实结算）。

    返回 {'dealt','taken','value',...}：`dealt`/`taken` 是预估的伤害（对拍用），
    `value` 是归一化后的战术价值（决策用）。
    """
    opp = getattr(opp_player, 'active', None)
    if opp is None or me is None:
        return {"dealt": 0.0, "taken": 0.0, "value": 0.0}
    opp_team = tactics.opponent_team(my_team)
    target, their_skill = context.get(scenario, (opp, None))
    if target is None or getattr(target, 'current_hp', 0) <= 0:
        target = opp

    my_skill = (me.skills[my_cand.index]
                if my_cand.kind == 'skill' and 0 <= my_cand.index < len(me.skills) else None)
    incoming_sprite = me
    if my_cand.kind == 'switch':
        incoming_sprite = (my_player.team[my_cand.index]
                           if 0 <= my_cand.index < len(my_player.team) else me)

    # ── 应对关系（双向）与出手顺序 ──
    i_counter = (my_skill is not None and their_skill is not None
                 and SkillResolver.resolve_counter(their_skill, my_skill))
    they_counter = (my_skill is not None and their_skill is not None
                    and SkillResolver.resolve_counter(my_skill, their_skill))
    counter_path = i_counter or they_counter
    first = acts_first(battle, me, my_team, my_skill, opp, opp_team, their_skill,
                       my_switch=(my_cand.kind == 'switch'))
    # 引擎：应对成立时**应对成功方先手**，另一侧按后手结算（battle.py
    # `_resolve_both_skills` 的应对分支；双方同时应对成功才退回先手度/速度）
    my_first_flag = True if i_counter else (False if they_counter else first)
    their_first_flag = True if they_counter else (False if i_counter else (not first))

    # ── 我造成的损失 ──
    dealt = 0
    if my_skill is not None and my_skill.is_attack:
        dealt = _damage(battle, me, target, my_skill, my_team, my_first_flag)
        dealt += tactics.starfall_bonus(battle, me, target, my_skill,
                                        tactics.opponent_team(my_team))
        if i_counter:
            # 我应对成功：自己这一手吃 `counter_succeeded` 威力倍率（只打一次；
            # 旧模型的「+ 基础」对应的是已修掉的重复伤害注入）
            dealt = int(dealt * counter_power_mult(my_skill))
        if they_counter:
            # 它应对成功（用防御技）：我的伤害被它的减伤削弱
            dealt = int(dealt * (1.0 - defense_reduction(their_skill)))
        if (not first and their_skill is not None and their_skill.is_attack
                and _damage(battle, opp, incoming_sprite, their_skill, opp_team,
                            their_first_flag) >= incoming_sprite.current_hp):
            dealt = 0                      # 我出手前就倒了 —— 幻影斩杀

    # ── 我承受的损失 ──
    taken = 0
    if their_skill is not None and their_skill.is_attack:
        taken = _damage(battle, opp, incoming_sprite, their_skill, opp_team, their_first_flag)
        if i_counter and my_skill is not None:
            # 我用防御技应对了它的攻击 → 减伤（风墙 50% 等）
            taken = int(taken * (1.0 - defense_reduction(my_skill)))
        if they_counter:
            # 它应对成功：它这一手吃 `counter_succeeded` 倍率（只打一次）
            taken = int(taken * counter_power_mult(their_skill))
        if first and dealt >= max(1, target.current_hp):
            taken = 0                      # 它先倒，这一手打不出来

    target_max = max(1, target.max_hp)
    mine_max = max(1, incoming_sprite.max_hp)
    value = (min(1.0, dealt / target_max) - min(1.0, taken / mine_max))
    if dealt >= target.current_hp > 0:
        value += params.kill_bonus
    if taken >= incoming_sprite.current_hp > 0:
        value -= params.kill_bonus

    # ── 收益 / 惩罚项 ──
    if my_cand.kind == 'gather':
        value += params.energy_weight * params.gather_energy_gain
    elif my_cand.kind == 'switch':
        value -= params.switch_incoming_weight * (
            tactics.switch_in_damage(battle, my_team, incoming_sprite) / mine_max)
        # 换人的价值大半在**下一回合**（对位优势）：本回合模型的补偿项，否则纯本回合
        # 期望会让它永远不肯换（旧规则的"残血换位"就是这条的启发式版本）。
        if params.switch_position_weight and target is opp:
            incoming_off = _best_outgoing_damage(battle, incoming_sprite, target,
                                                 tactics.opponent_team(my_team))
            current_off = _best_outgoing_damage(battle, me, target, my_team)
            value += params.switch_position_weight * (incoming_off - current_off) / target_max
    elif my_skill is not None and my_skill.is_defense:
        value -= params.defense_cooldown_penalty
    elif my_skill is not None and not my_skill.is_attack:
        value += params.buff_credit
    if they_counter:
        value -= params.countered_penalty
    if their_skill is not None and not their_skill.is_attack and taken == 0:
        value -= params.status_tempo_penalty
    return {"dealt": float(dealt), "taken": float(taken), "first": float(first),
            "i_counter": float(i_counter), "they_counter": float(they_counter),
            "value": value}


def payoff(battle, me, my_player, opp_player, my_cand: Candidate, scenario: str,
           context: dict, params: EVParams, my_team: str) -> float:
    """我这一手在"对手走 `scenario`"时的本回合价值（`payoff_terms` 的标量）。"""
    return payoff_terms(battle, me, my_player, opp_player, my_cand, scenario,
                        context, params, my_team)["value"]


# ══════════════════════════════════════════════════════════════════
# 矩阵 / 期望值 / 采样
# ══════════════════════════════════════════════════════════════════

def value_matrix(battle, me, my_player, opp_player, candidates: list[Candidate],
                 dist: dict[str, float], params: EVParams | None = None,
                 my_team: str = 'A') -> dict[Candidate, dict[str, float]]:
    """行 × 列的收益矩阵（只列概率 > 0 的列）。"""
    p = params or EVParams()
    context = scenario_context(battle, me, my_team, opp_player)
    return {
        cand: {sc: payoff(battle, me, my_player, opp_player, cand, sc, context, p, my_team)
               for sc, prob in dist.items() if prob > 1e-9 and sc in context}
        for cand in candidates
    }


def expected_values(matrix: dict[Candidate, dict[str, float]],
                    dist: dict[str, float],
                    params: EVParams | None = None) -> dict[Candidate, float]:
    """每行的期望值（可选风险惩罚：`EV − λ·(最好格 − 最坏格)`）。"""
    p = params or EVParams()
    out: dict[Candidate, float] = {}
    for cand, row in matrix.items():
        ev = sum(row.get(sc, 0.0) * dist.get(sc, 0.0) for sc in row)
        if p.risk_lambda and row:
            ev -= p.risk_lambda * (max(row.values()) - min(row.values()))
        out[cand] = ev
    return out


def softmax_pick(evs: dict[Candidate, float], temperature: float,
                 rng: random.Random | None = None, eps: float = 1e-9) -> Candidate:
    """按 softmax(EV/T) 采样（T=0 → argmax）。

    T>0 即**混合策略**：期望接近的动作会被混着出，让对手无法靠"读我"占便宜
    （扑克 CFR 的直觉；我们这里是最简单的一层——单回合软最大化）。
    """
    rng = rng or random
    if not evs:
        raise ValueError("softmax_pick 收到空候选集")
    ranked = sorted(evs.items(), key=lambda kv: (-kv[1], str(kv[0])))
    if temperature <= 1e-9:
        best = ranked[0][1]
        ties = [c for c, v in ranked if abs(v - best) <= eps]
        return ties[rng.randrange(len(ties))]        # 完全平局：随机（不可被预测）
    top = ranked[0][1]
    weights = [math.exp((v - top) / temperature) for _c, v in ranked]
    total = sum(weights)
    r = rng.random() * total
    acc = 0.0
    for (cand, _v), w in zip(ranked, weights):
        acc += w
        if r <= acc:
            return cand
    return ranked[-1][0]


def choose(battle, me, my_player, opp_player, candidates: list[Candidate],
           my_team: str, *, params: EVParams | None = None,
           belief_params: belief_mod.BeliefParams | None = None,
           temperature: float = 0.0, my_best_dmg: int = 0,
           rng: random.Random | None = None,
           belief_override: dict[str, float] | None = None,
           ) -> tuple[Candidate, dict[Candidate, float], dict[str, float]]:
    """完整决策：算信念 → 建矩阵 → 期望值 → （可选）混合采样。

    `belief_override` 直接给定对手动作分布（用于"我知道对手要做什么"的剥削者/
    诊断工具），给了就不再走 `belief.belief()`。

    返回 (选中的动作, 每行期望值, 对手动作分布) —— 期望值与分布都返回出去，
    方便日志/审计与"预测分歧"类度量。
    """
    p = params or EVParams()
    dist = (belief_override if belief_override is not None
            else belief_mod.belief(battle, my_team, opp_player, my_best_dmg, belief_params))
    matrix = value_matrix(battle, me, my_player, opp_player, candidates, dist, p, my_team)
    evs = expected_values(matrix, dist, p)
    picked = softmax_pick(evs, temperature, rng)
    return picked, evs, dist
