# -*- coding: utf-8 -*-
"""对局骨架的**引擎侧**：建局、局面渲染、合法动作菜单、答案解析与严格校验、会话重放。

不依赖 argparse，也不写提示词（提示词与命令在 `duel.cli`）。所有函数都假定
调用方已把仓库根放进 `sys.path`（`duel.cli` / `duel_harness.py` 负责）。
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.stdout.reconfigure(encoding="utf-8")



DEFAULT_MAX_TURNS = 30
SESSION_VERSION = 3
# 提示词结构版本：改动给定信息集时递增，并随每回合落盘（记录里能看出哪几回合用了旧版）。
# v2（2026-09-21）：按选手自报缺口补上 伤害估算 / 物防魔防 / 板凳速度 / 无回能事实 / 终局算法。
# v3（2026-09-21）：先手值相同时的抛硬币规则、强化步数与效果剩余回合、队伍表带技能数值、
#                   换人候补的挨打/输出/进场伤害估算。
PROMPT_REV = 3

_ACTION_ALIASES = {
    "skill": "skill", "技能": "skill", "招式": "skill", "攻击": "skill",
    "switch": "switch", "换人": "switch", "切换": "switch",
    "gather": "gather", "聚能": "gather", "蓄能": "gather",
    "item": "item", "道具": "item", "用道具": "item",
}
_NAME_KEYS = ("name", "skill", "skill_name", "target", "技能", "名字", "名称")
_VARIANT_KEYS = ("variant", "形态", "variety")
_THEN_KEYS = ("then", "follow_up", "后续", "then_action")
_BENCH_KEYS = ("bench", "替补顺序", "换人顺序")
_REASON_KEYS = ("reason", "理由")


class CommandError(Exception):
    """非法动作 / 非法指令。带侧别与"当时真实可选项"，用来直接生成重试提示词。"""

    def __init__(self, side: str, message: str, options: list[str] | None = None):
        super().__init__(f"[{side}] {message}")
        self.side = side
        self.message = message
        self.options = options or []


# ══════════════════════════════════════════════════════════════════
# 队伍与建局
# ══════════════════════════════════════════════════════════════════

def _item_by_name(name: str):
    from backend.sim.player import Item

    if name == "进化之力":
        return Item.leader()
    if name == "愿力":
        return Item.wish()
    print(f"  [警告] 未知道具 {name!r} → 按愿力处理", file=sys.stderr)
    return Item.wish()


def _load_team_spec(spec: str, rng: random.Random) -> tuple[list[dict], object, str]:
    """`meta:<i>` → meta 库第 i 支（含其道具与名字）；`file:<path>` → 读 JSON 规格。"""
    from backend.engine.ai.data.meta_teams import load_meta_teams, spec_from_team

    if spec.startswith("meta:"):
        idx = int(spec.split(":", 1)[1])
        team = load_meta_teams()[idx]
        specs, _ = spec_from_team(team, rng)
        return specs, _item_by_name(team.get("item") or "愿力"), team.get("name", spec)
    path = Path(spec.split(":", 1)[1])
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["sprites"], _item_by_name(data.get("item", "愿力")), data.get("name", str(path))


def _neutralize(specs: list[dict]) -> list[dict]:
    """把**天赋/性格**归零（两侧同口径：只比决策，不比培养）。

    血脉**不剥**：它是配装的一部分，且决定道具能不能用（首领血脉 ↔ 进化之力，
    元素血脉 ↔ 愿力）。旧版连血脉一起剥掉，结果 A 的进化之力整局不可用。
    """
    out = []
    for sp in specs:
        sp = dict(sp)
        sp["nature"] = None
        sp["iv"] = None
        out.append(sp)
    return out


def _build_battle(cfg: dict):
    from backend.sim.factory import SimFactory

    factory = SimFactory()
    rng = random.Random(cfg["seed"])
    specs_a, item_a, _ = _load_team_spec(cfg["team_a"], rng)
    specs_b, item_b, _ = _load_team_spec(cfg["team_b"], rng)
    if cfg.get("neutral", True):
        specs_a, specs_b = _neutralize(specs_a), _neutralize(specs_b)
    p1 = factory.build_player("A", specs_a, item=item_a)
    p2 = factory.build_player("B", specs_b, item=item_b)
    # 首发要在 build_battle 之前设：Battle 构造时会跑入场效果（印记进场伤害等）
    p1.active_index = cfg["lead_a"]
    p2.active_index = cfg["lead_b"]
    return factory, factory.build_battle(p1, p2)


# ══════════════════════════════════════════════════════════════════
# 渲染
# ══════════════════════════════════════════════════════════════════

def _kind(sk) -> str:
    return "攻击" if sk.is_attack else ("防御" if sk.is_defense else "状态")


def _skill_brief(sk) -> str:
    """队伍表里的技能简写：类别·能耗·威力·应对（板凳精灵也要能据此判断换人价值）。"""
    parts = [_kind(sk), f"{sk.energy_cost}费"]
    if sk.is_attack:
        parts.append(f"{sk.power}威")
    if sk.priority:
        parts.append(f"先手{sk.priority:+d}")
    if sk.counter and sk.counter != "无":
        parts.append(f"应{sk.counter}")
    return f"{sk.name}(" + "·".join(parts) + ")"


def _effect_brief(e) -> str | None:
    """效果层简写：强化/削弱要看**步数**，异常/印记看层数，都带剩余回合。"""
    from backend.vm.effect import AbnormalEffect, MarkEffect, StatBuffEffect

    ttl = int(getattr(e, "ttl", 0) or 0)
    tail = f"(剩{ttl}回合)" if ttl else ""
    name = getattr(e, "name", "?")
    if isinstance(e, StatBuffEffect):
        steps = int(getattr(e, "steps", 0) or 0)
        if not steps:
            return None
        unit = "点" if e.stat_key == "speed" else "步"
        return f"{name} {e.stat_key}{steps:+d}{unit}{tail}"
    stacks = int(getattr(e, "stacks", 0) or 0)
    if isinstance(e, (AbnormalEffect, MarkEffect)):
        if not stacks:
            return None
        return f"{name}×{stacks}层{tail}"
    return f"{name}{tail}" if ttl else None


def _skill_line(i: int, sk, energy: int) -> str:
    bits = [f"({i}) {sk.name}", _kind(sk), sk.element or "-", f"能耗{sk.energy_cost}"]
    if sk.is_attack:
        bits.append(f"威力{sk.power}")
    if sk.priority:
        bits.append(f"先手{sk.priority:+d}")
    if sk.counter and sk.counter != "无":
        bits.append(f"应对:{sk.counter}")
    if sk.cooldown > 0:
        bits.append(f"冷却{sk.cooldown}")
    if sk.sealed:
        bits.append("被封印")
    if sk.energy_cost > energy:
        bits.append("能量不足")
    return " ".join(bits)


def _bloodline_note(battle, sprite) -> str:
    """愿力会把槽 0 换成血脉技能——渲染里得让选手知道换出的是什么。"""
    from backend.sim.item_policy import bloodline_skill

    bl = getattr(sprite, "bloodline", "") or "-"
    bs = bloodline_skill(battle, sprite)
    if bs is not None:
        return f"      血脉 {bl}（愿力可把槽 0 换成「{bs.name}」）"
    return f"      血脉 {bl}"


def _side_block(battle, player, label: str) -> list[str]:
    act = player.active
    lines = [f"  【{label}】场上：{act.name}（{'/'.join(act.species.elements)}）"
             f" HP {act.current_hp}/{act.max_hp}  能量 {act.energy}"
             f"  速 {act.effective_stat('speed')}"
             f"  物攻 {act.effective_stat('atk')} 物防 {act.effective_stat('def')}"
             f" 魔攻 {act.effective_stat('sp_atk')} 魔防 {act.effective_stat('sp_def')}"
             f"  {'力竭' if act.is_fainted else ''}"]
    mods = {k: round(float(v), 3) for k, v in getattr(act, "_modifiers", {}).items() if v}
    if mods:
        lines.append(f"      强化/修正：{mods}")
    eff = [b for b in (_effect_brief(e) for e in getattr(act, "active_effects", [])) if b]
    if eff:
        lines.append(f"      状态：{'、'.join(eff)}")
    lines.append("      可用技能：")
    for i, sk in enumerate(act.skills):
        lines.append("        " + _skill_line(i, sk, act.energy))
    lines.append(_bloodline_note(battle, act))
    bench = [f"({i}) {sp.name} HP {sp.current_hp}/{sp.max_hp} 能量 {sp.energy}"
             f" 速 {sp.effective_stat('speed')}"
             f"{' 力竭' if sp.is_fainted else ''}"
             for i, sp in enumerate(player.team) if i != player.active_index]
    lines.append("      板凳：" + ("  ".join(bench) if bench else "无"))
    return lines


def _last_turn_events(battle) -> list[str]:
    if not battle.log:
        return []
    rec = battle.log[-1]
    evs: list[str] = []
    for side in ("action_a", "action_b"):
        ar = getattr(rec, side, None)
        if ar is not None:
            evs += list(getattr(ar, "events", []) or [])
    evs += list(getattr(rec, "faint_check_events", []) or [])
    evs += list(getattr(rec, "turn_end_events", []) or [])
    return evs


def _render(battle, persp: str = "A", with_history: str = "last") -> str:
    p_me, p_op = ((battle.player_a, battle.player_b) if persp == "A"
                  else (battle.player_b, battle.player_a))
    my_key, op_key = ("A", "B") if persp == "A" else ("B", "A")
    g = battle.globals
    marks = {t: "、".join(f"{m.name}×{int(getattr(m, 'stacks', 0) or 0)}"
                          f"(剩{int(getattr(m, 'ttl', 0) or 0)})"
                          for m in g.mark_effects.get(t, [])) or "无"
             for t in ("A", "B")}
    out = [f"── 局面（第 {battle.turn + 1} 回合开始时）── 天气 {g.weather or '无'}"
           f"（{'夜' if getattr(g, 'night', False) else '昼'}）"
           f"  心力 我 {p_me.lives} / 对方 {p_op.lives}",
           f"  印记 我侧：{marks[my_key]} ｜ 对方侧：{marks[op_key]}",
           f"  道具 我方 {getattr(p_me.item, 'name', '无')}"
           f"（已用 {getattr(p_me.item, 'uses', 0)}/{getattr(p_me.item, 'max_uses', 0)}）"
           f" ｜ 对方 {getattr(p_op.item, 'name', '无')}"
           f"（已用 {getattr(p_op.item, 'uses', 0)}/{getattr(p_op.item, 'max_uses', 0)}）"]
    out += _side_block(battle, p_me, "我方")
    out += _side_block(battle, p_op, "对方")
    if with_history == "full" and battle.log:
        out.append("  本局已发生的出招：")
        for rec in battle.log:
            out.append(f"    · {getattr(rec, '_header', '')}")
    elif battle.log:
        rec = battle.log[-1]
        out.append(f"  上一回合：{getattr(rec, '_header', '')}")
        out += [f"    · {e}" for e in _last_turn_events(battle)]
    return "\n".join(out)


def _team_sheet(player, label: str, name: str) -> str:
    item = getattr(player, "item", None)
    head = (f"【队伍 {label}】{name}  道具 {getattr(item, 'name', '无')}"
            f"（{getattr(item, 'max_uses', 0)} 次/局）")
    lines = [head]
    for i, sp in enumerate(player.team):
        lines.append(f"  ({i}) {sp.name:<12} {'/'.join(sp.species.elements):<6}"
                     f" HP {sp.max_hp:<4} 速度 {sp.effective_stat('speed'):<4}"
                     f" 血脉 {sp.bloodline or '-'}"
                     f"  技能：{' '.join(_skill_brief(sk) for sk in sp.skills)}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# 合法动作菜单
# ══════════════════════════════════════════════════════════════════

def _skill_blocked(sk, energy: int) -> str | None:
    if sk.sealed:
        return "被封印"
    if sk.cooldown > 0:
        return f"冷却中(剩 {sk.cooldown} 回合)"
    if sk.energy_cost > energy:
        return f"能量不足(需 {sk.energy_cost}，现有 {energy})"
    return None


def _item_block_reason(battle, side: str) -> str | None:
    """None = 现在能用；否则给出为什么不能用（提示词里要写清楚）。

    判定必须与引擎**同口径**：进化之力要看 `item_variants()`（不是只看血脉），
    愿力要看真有没有可换出的血脉技能（`item_usable()` 只查血脉，查不出空换）。
    这两个坑各踩过一次：菜单说"不可用"而引擎其实可用 = 静默抹掉一个可选项。
    """
    from backend.common.constants import ELEMENTAL_BLOODLINES
    from backend.sim.item_policy import bloodline_skill

    player = battle.get_player(side)
    item = player.item
    if item is None:
        return "本队没有道具"
    if item.is_exhausted:
        return f"已用完({item.uses}/{item.max_uses})"
    if item.cooldown_turns and item.uses > 0:
        left = item.cooldown_turns - (battle.turn - item.last_use_turn)
        if left > 0:
            return f"冷却中(还需 {left} 回合)"
    sprite = player.active
    if sprite is None:
        return "场上无精灵"
    if item.name == "进化之力":
        if sprite.bloodline != "首领":
            return f"{sprite.name} 的血脉是「{sprite.bloodline or '-'}」，进化之力需要「首领」血脉"
        if sprite.species.is_leader_stage():
            return f"{sprite.name} 已经是首领形态"
        if not battle.item_variants(side):
            return f"{sprite.name}（编号 {sprite.species.number}）没有可用的首领形态"
        return None
    if item.name == "愿力":
        if sprite.bloodline not in ELEMENTAL_BLOODLINES:
            return f"{sprite.name} 的血脉「{sprite.bloodline or '-'}」不是元素血脉"
        if bloodline_skill(battle, sprite) is None:
            return f"{sprite.name} 没有可换出的血脉技能（用了也是空转）"
        return None
    return "当前不可用"


def _static_post_item_skills(battle, side: str) -> list | None:
    """静态近似：只有愿力能静态推出"用后技能表"（槽 0 换血脉技能）。

    进化之力要改种族再传动，推不出来 —— 那种情况用 `_post_item_skills_fn` 真模拟一次。
    """
    from backend.sim.item_policy import bloodline_skill

    player = battle.get_player(side)
    item = player.item
    if item is None or item.name != "愿力" or player.active is None:
        return None
    bs = bloodline_skill(battle, player.active)
    if bs is None or not player.active.skills:
        return None
    return [bs] + list(player.active.skills[1:])


def _item_menu(battle, side: str, post_skills_fn=None) -> list[str]:
    """道具菜单。可用时把**用后技能表**一并写出来 —— 不然选手会按用前的表写 then，
    实测两种都会被引擎拒（愿力换槽 / 首领化再传动）。"""
    player = battle.get_player(side)
    item = player.item
    if item is None:
        return ["- 道具：本队没有道具"]
    reason = _item_block_reason(battle, side)
    if reason is not None:
        return [f"- 道具：{item.name} —— 本回合不可用（{reason}）"]
    if item.name == "进化之力":
        variants = battle.item_variants(side)
        opts = "，".join(f"{i} = {v.name}" for i, v in enumerate(variants))
        lines = [f"- 道具：item（{item.name}，可用）形态号：{opts}"
                 f" —— 写法 {{\"action\":\"item\",\"variant\":<形态号>,\"then\":{{后续动作}}}}"]
        post = (post_skills_fn(0) if post_skills_fn else None)
        if post:
            open_names = [sk.name for sk in post if not sk.sealed]
            sealed = [sk.name for sk in post if sk.sealed]
            lines.append(f"  · 用后（首领化）技能表变成：{' / '.join(open_names)}"
                         + (f"（{'、'.join(sealed)} 被封印）" if sealed else "")
                         + " —— then 必须按这张表写")
        return lines
    lines = [f"- 道具：item（{item.name}，可用）"
             f" —— 写法 {{\"action\":\"item\",\"then\":{{后续动作}}}}（不消耗行动，必须跟后续动作）"]
    post = (post_skills_fn(None) if post_skills_fn else None) or _static_post_item_skills(battle, side)
    if post:
        lines.append(f"  · 用后技能表变化：槽 0 由「{player.active.skills[0].name}」"
                     f"换成「{post[0].name}」（本回合有效）"
                     f" —— then 里的技能名要按**用后**的表写；用后技能表：")
        for i, sk in enumerate(post):
            lines.append("      " + _skill_line(i, sk, player.active.energy))
    return lines


def _legal_menu(battle, side: str, post_skills_fn=None) -> str:
    player = battle.get_player(side)
    act = player.active
    lines = ["可选技能（写 {\"action\":\"skill\",\"name\":\"<技能名>\"}）："]
    for i, sk in enumerate(act.skills):
        blocked = _skill_blocked(sk, act.energy)
        tag = f"   ← 不可用：{blocked}" if blocked else ""
        lines.append(f"- skill {sk.name}（{_kind(sk)}"
                     f"{'·' + sk.element if sk.element else ''}·能耗 {sk.energy_cost}"
                     f"{'·威力 ' + str(sk.power) if sk.is_attack else ''}"
                     f"{'·应对:' + sk.counter if sk.counter and sk.counter != '无' else ''}）{tag}")
    lines.append("换人（写 {\"action\":\"switch\",\"name\":\"<精灵名>\"}，换人先于技能结算）：")
    bench = [sp for i, sp in enumerate(player.team)
             if i != player.active_index and not sp.is_fainted]
    if bench:
        lines += [f"- switch {sp.name}（HP {sp.current_hp}/{sp.max_hp}，能量 {sp.energy}）"
                  for sp in bench]
    else:
        lines.append("- （没有可换的替补）")
    lines.append("聚能：- gather（本回合不出招，回复能量）")
    lines += _item_menu(battle, side, post_skills_fn)
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# 答案解析与严格校验（唯一的口径来源）
# ══════════════════════════════════════════════════════════════════

def _extract_json(text: str) -> dict:
    """容忍 ```json 围栏与前后正文，取第一个括号配平的 JSON 对象。"""
    raw = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", raw, re.S)
    if fenced:
        raw = fenced.group(1).strip()
    start = raw.find("{")
    if start < 0:
        raise ValueError("没找到 JSON 对象")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(raw[start:i + 1])
    raise ValueError("JSON 括号不配平")


def _pick(d: dict, keys: tuple[str, ...]):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _skill_names(player, skills=None) -> list[str]:
    return [sk.name for sk in (skills if skills is not None else player.active.skills)]


def _resolve_skill(player, arg, side: str, skills=None):
    from backend.sim.action import Action

    skills = skills if skills is not None else player.active.skills
    if isinstance(arg, bool):
        arg = None
    if isinstance(arg, int) or (isinstance(arg, str) and arg.strip().isdigit()):
        idx = int(arg)
        if not 0 <= idx < len(skills):
            raise CommandError(side, f"技能槽 {idx} 不存在", _skill_names(player, skills))
        sk = skills[idx]
    else:
        matches = [i for i, s in enumerate(skills) if s.name == str(arg).strip()]
        if not matches:
            raise CommandError(side, f"当前技能表里没有「{arg}」", _skill_names(player, skills))
        idx, sk = matches[0], skills[matches[0]]
    blocked = _skill_blocked(sk, player.active.energy)
    if blocked:
        raise CommandError(
            side, f"「{sk.name}」本回合不可用：{blocked}",
            [f"{s.name}（可用）" for s in skills
             if not _skill_blocked(s, player.active.energy)])
    return Action(kind="skill", skill_index=idx), sk.name


def _resolve_switch(player, arg, side: str):
    from backend.sim.action import Action

    bench = [sp for i, sp in enumerate(player.team) if i != player.active_index]
    usable = [sp for sp in bench if not sp.is_fainted]
    if isinstance(arg, int) or (isinstance(arg, str) and str(arg).strip().isdigit()):
        idx = int(arg)
        if not 0 <= idx < len(player.team):
            raise CommandError(side, f"队伍槽位 {idx} 不存在", [sp.name for sp in usable])
        target = player.team[idx]
        if idx == player.active_index:
            raise CommandError(side, f"{target.name} 已经在场上", [sp.name for sp in usable])
        if target.is_fainted:
            raise CommandError(side, f"{target.name} 已力竭，不能换上", [sp.name for sp in usable])
    else:
        name = str(arg).strip()
        target = next((sp for sp in player.team if sp.name == name), None)
        if target is None:
            raise CommandError(side, f"队伍里没有「{name}」", [sp.name for sp in usable])
        if target is player.active:
            raise CommandError(side, f"{name} 已经在场上", [sp.name for sp in usable])
        if target.is_fainted:
            raise CommandError(side, f"{name} 已力竭，不能换上", [sp.name for sp in usable])
    return Action(kind="switch", switch_index=player.team.index(target)), target.name


def _resolve_answer(battle, side: str, ans: dict, skills=None, depth: int = 0,
                    post_skills_fn=None):
    """答案 dict → (指令队列, 换人顺序(名字), reason)。

    非法输入一律抛 `CommandError`（**不降级**）：报错里带上"当时真实可选项"。
    `skills` 是校验技能名时用的技能表（用道具后要换成用后的表），`depth` 防 then 嵌套道具，
    `post_skills_fn` 用来真模拟一次用道具、拿到用后的表（首领化会再传动，静态推不出来）。
    """
    if not isinstance(ans, dict):
        raise CommandError(side, f"答案必须是 JSON 对象，收到 {type(ans).__name__}")
    raw_action = _pick(ans, ("action", "动作"))
    if raw_action is None:
        raise CommandError(side, "缺少 action 字段",
                           ["skill", "switch", "gather", "item"])
    action = _ACTION_ALIASES.get(str(raw_action).strip().lower())
    if action is None:
        raise CommandError(side, f"不认识的 action：{raw_action!r}",
                           ["skill", "switch", "gather", "item"])
    player = battle.get_player(side)
    name = _pick(ans, _NAME_KEYS)
    commands: list[str] = []

    if action == "gather":
        commands = ["gather"]
    elif action == "skill":
        if name is None:
            raise CommandError(side, "skill 动作缺少 name（技能名）", _skill_names(player, skills))
        _act, resolved = _resolve_skill(player, name, side, skills)
        commands = [f"skill {resolved}"]
    elif action == "switch":
        if name is None:
            raise CommandError(side, "switch 动作缺少 name（精灵名）",
                               [sp.name for sp in player.team if not sp.is_fainted])
        _act, resolved = _resolve_switch(player, name, side)
        commands = [f"switch {resolved}"]
    else:  # item
        if depth > 0:
            raise CommandError(side, "then 里不能再放道具（一回合只能用一次道具）")
        reason = _item_block_reason(battle, side)
        if reason is not None:
            raise CommandError(side, f"道具 {player.item.name} 现在不能用：{reason}", [])
        variant = _pick(ans, _VARIANT_KEYS)
        if player.item.name == "进化之力":
            variants = battle.item_variants(side)
            idx = int(variant) if isinstance(variant, (int, str)) and str(variant).strip().lstrip("-").isdigit() else None
            if idx is None or not 0 <= idx < len(variants):
                raise CommandError(side, "进化之力必须给 variant（形态号）",
                                   [f"{i} = {v.name}" for i, v in enumerate(variants)])
            commands = [f"item {idx}"]
        else:
            if variant is not None:
                print(f"  [警告 {side}] {player.item.name} 没有形态号，已忽略 variant={variant}",
                      file=sys.stderr)
            commands = ["item"]
        then = _pick(ans, _THEN_KEYS)
        if not isinstance(then, dict):
            raise CommandError(side, "item 动作必须带 then（道具不消耗行动，用过还要出招）",
                               ["then = {\"action\":\"skill\",\"name\":\"...\"} / {\"action\":\"gather\"}"])
        post = None
        if post_skills_fn is not None:
            post = post_skills_fn(idx if player.item.name == "进化之力" else None)
        if post is None:
            post = _static_post_item_skills(battle, side)
        try:
            sub, _bench, _why = _resolve_answer(battle, side, then, post, depth + 1)
        except CommandError as exc:
            hint = ""
            if post:
                open_names = [sk.name for sk in post if not sk.sealed]
                hint = (f"。用道具后的技能表：{' / '.join(open_names)}"
                        + (f"（{'、'.join(sk.name for sk in post if sk.sealed)} 被封印）"
                           if any(sk.sealed for sk in post) else ""))
            elif player.item.name == "进化之力":
                hint = "。进化之力会首领化并再传动一次技能表，请按用后的表写 then"
            raise CommandError(side, f"then 不合格：{exc.message}{hint}",
                               [sk.name for sk in post] if post else exc.options) from exc
        if len(sub) != 1 or sub[0].startswith("item"):
            raise CommandError(side, "then 里只能放一个非道具动作（skill / switch / gather）", [])
        commands += sub

    bench_arg = _pick(ans, _BENCH_KEYS)
    bench_names: list[str] = []
    if bench_arg is not None:
        if not isinstance(bench_arg, list) or not all(isinstance(x, str) for x in bench_arg):
            raise CommandError(side, "bench 必须是精灵名组成的数组", [sp.name for sp in player.team])
        for nm in bench_arg:
            sp = next((s for s in player.team if s.name == nm), None)
            if sp is None:
                raise CommandError(side, f"bench 里的「{nm}」不在队伍中",
                                   [s.name for s in player.team])
            if sp.is_fainted:
                raise CommandError(side, f"bench 里的「{nm}」已力竭", 
                                   [s.name for s in player.team if not s.is_fainted])
        bench_names = list(bench_arg)

    return commands, bench_names, str(_pick(ans, _REASON_KEYS) or "")


class _StrictAgent:
    """把校验后的指令喂进引擎；**在取招那一刻**再按名字解析一次（权威口径）。

    任何解析失败都抛 `CommandError`（不降级成聚能）：调用方会作废本次结算、
    保留回合，并用错误信息生成重试提示词。
    """

    def __init__(self, team: str, player, commands: list[str], bench: list[str]):
        self.team, self.player = team, player
        self.commands = list(commands)
        self.bench = list(bench)

    def _resolve(self, cmd: str, battle):
        from backend.sim.agent import _GATHER_ACTION
        from backend.sim.action import Action

        parts = cmd.split(maxsplit=1)
        kind = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        if kind == "gather":
            return _GATHER_ACTION
        if kind == "skill":
            act, _name = _resolve_skill(self.player, arg, self.team)
            return act
        if kind == "switch":
            act, _name = _resolve_switch(self.player, arg, self.team)
            return act
        if kind == "item":
            reason = _item_block_reason(battle, self.team)
            if reason is not None:
                raise CommandError(self.team, f"道具现在不能用：{reason}")
            return Action(kind="item", variant=int(arg)) if arg.strip().isdigit() \
                else Action(kind="item")
        raise CommandError(self.team, f"未知指令 {cmd!r}")

    def choose_action(self, battle):
        if not self.commands:
            raise CommandError(self.team, "指令队列为空（这是程序 bug，不是你的输入问题）")
        return self._resolve(self.commands.pop(0), battle)

    def choose_lead(self, battle) -> int:
        return self.player.active_index

    def choose_replacement(self, battle) -> int:
        order = [sp for nm in self.bench for sp in self.player.team if sp.name == nm]
        order += [sp for i, sp in enumerate(self.player.team)
                  if i != self.player.active_index and sp not in order]
        for sp in order:
            if sp is not self.player.active and not sp.is_fainted:
                return self.player.team.index(sp)
        raise CommandError(self.team, "没有可上场的替补")

    def on_game_end(self, winner):
        pass


# ══════════════════════════════════════════════════════════════════
# 会话：落盘 / 重放
# ══════════════════════════════════════════════════════════════════

def _session_path(dirpath: Path) -> Path:
    return dirpath / "session.json"


def _history_path(dirpath: Path) -> Path:
    return dirpath / "history.jsonl"


def _read_session(dirpath: Path) -> dict:
    path = _session_path(dirpath)
    if not path.exists():
        raise SystemExit(f"没有找到会话 {path}（先跑 new）")
    sess = json.loads(path.read_text(encoding="utf-8"))
    if sess.get("version") != SESSION_VERSION:
        raise SystemExit(f"会话版本 {sess.get('version')} ≠ {SESSION_VERSION}，请重新建局")
    return sess


def _read_history(dirpath: Path) -> list[dict]:
    path = _history_path(dirpath)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _cfg_from_session(sess: dict) -> dict:
    return {"team_a": sess["team_a"], "team_b": sess["team_b"], "seed": sess["seed"],
            "lead_a": sess["lead_a"], "lead_b": sess["lead_b"],
            "neutral": sess.get("neutral", True)}


def _replay(sess: dict, entries: list[dict]):
    """从开局重放到 entries 末尾。指令都是校验过的，重放必然一致（引擎逐位确定）。"""
    _factory, battle = _build_battle(_cfg_from_session(sess))
    for e in entries:
        ag_a = _StrictAgent("A", battle.player_a, e["commands_a"], e.get("bench_a") or [])
        ag_b = _StrictAgent("B", battle.player_b, e["commands_b"], e.get("bench_b") or [])
        try:
            battle.execute_turn(ag_a, ag_b)
        except CommandError as exc:
            raise SystemExit(f"历史重放失败（不应发生）：{exc}") from exc
    return battle


def _prompt_battle(sess: dict, entries: list[dict]):
    """**取招时点**的局面：重放 + 跑一遍回合开始管线（延时效果 / trait / 传动）。

    这一步不能省：`execute_turn` 内部先跑 `TurnPipeline.execute_turn_start`（每回合都跑，
    含传动与封印），之后才 `choose_action`。若按"上一回合结算后"的状态渲染/校验，
    技能表会差一次传动（实测：渲染给选手的是「械斗/啮合传递…」，真正取招时插槽已移位），
    道具用后还会再传动一次，`then` 就更对不上。返回 (battle, 回合开始事件)。
    """
    from backend.sim.pipeline import TurnPipeline

    battle = _replay(sess, entries)
    events = TurnPipeline.execute_turn_start(battle)
    return battle, events


def _post_item_skills_fn(sess: dict, entries: list[dict], side: str):
    """返回 `fn(variant) -> [(技能, 是否被封印), ...]`：在**一次性副本**上真的用一次道具，
    读出用后的技能表（首领化会改种族并再传动一次，静态推不出来，只能模拟）。

    副本用完即弃，绝不动正在跑的那一局。
    """
    def make(variant):
        battle, _events = _prompt_battle(sess, entries)
        try:
            player = battle.get_player(side)
            battle._resolve_item(side, variant)
            return list(player.active.skills)
        except Exception as exc:  # 引擎内部异常不该让整局卡住
            print(f"  [警告] 模拟用道具失败（{side}）：{exc}", file=sys.stderr)
            return []
    return make


def _default_bench(player, lead_index: int) -> list[str]:
    return [sp.name for i, sp in enumerate(player.team) if i != lead_index]


def _answer_path(dirpath: Path, side: str) -> Path:
    return dirpath / f"answer_{side}.json"


def _pending_path(dirpath: Path, side: str) -> Path:
    return dirpath / f"pending_{side}.json"


def _prompt_path(dirpath: Path, turn: int, side: str) -> Path:
    return dirpath / f"turn_{turn:03d}_{side}.md"


def _prompt_sha(dirpath: Path, turn: int, side: str) -> str:
    """该回合这一侧实际看到的提示词摘要（隔离审计用）。"""
    path = _prompt_path(dirpath, turn, side)
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()[:16]


def _dispatch_path(dirpath: Path, side: str) -> Path:
    return dirpath / f"dispatch_{side}.txt"


def _note_path(dirpath: Path, side: str) -> Path:
    return dirpath / f"note_{side}.txt"


def _read_note(dirpath: Path, side: str, inline: str | None = None) -> str:
    """选手自报的「缺什么信息」（一行）。用于下一轮改进提示词，不参与决策。"""
    if inline:
        return inline.strip()
    path = _note_path(dirpath, side)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip().splitlines()[0].strip() \
        if path.read_text(encoding="utf-8").strip() else ""


def _normalize_note(text: str) -> str:
    return re.sub(r"[\s。，、；：!！?？.,;:]+", "", text or "")


def collect_notes(dirpath: Path) -> list[tuple[str, int, str]]:
    """历史里去重后的自报缺口：[(侧, 首次出现的回合, 原文)]。"""
    seen: set[str] = set()
    out: list[tuple[str, int, str]] = []
    for e in _read_history(dirpath):
        for side in ("A", "B"):
            note = (e.get(f"note_{side.lower()}") or "").strip()
            key = _normalize_note(note)
            if not key or key == "无" or key in seen:
                continue
            seen.add(key)
            out.append((side, e["turn"], note))
    return out


def _label(sess: dict, side: str) -> str:
    return sess.get("labels", {}).get(side.lower(), side)


def _damage_section(battle, side: str) -> str:
    """伤害估算（用引擎自己的 `estimate_damage`）：双方本回合可用攻击的折算伤害。

    选手反复自报"缺伤害估算表，无法判断能否击杀"，这里直接给引擎口径的数字。
    注意：不含应对加成/防御减伤，也不含被应对时追加的那次基础伤害。
    """
    from backend.sim.item_policy import estimate_damage

    me = battle.get_player(side)
    other = "B" if side == "A" else "A"
    opp = battle.get_player(other)
    mine, theirs = me.active, opp.active
    lines = [f"## 伤害估算（引擎口径，本回合双方当前强化下；不含应对加成/防御减伤）", "",
             f"- 我方（{mine.name}）可用攻击 → 对手场上 {theirs.name}"
             f"（HP {theirs.current_hp}/{theirs.max_hp}）："]
    for sk in mine.skills:
        if not sk.is_attack:
            continue
        blocked = _skill_blocked(sk, mine.energy)
        try:
            dmg = estimate_damage(battle, mine, theirs, sk, side)
        except Exception as exc:  # 估算失败不该挡住整局
            lines.append(f"  · {sk.name} → （估算失败：{exc}）")
            continue
        kill = "**可击杀**" if dmg >= theirs.current_hp else ""
        tag = f"（本回合不可用：{blocked}）" if blocked else ""
        lines.append(f"  · {sk.name} → 约 {dmg} {kill}{tag}")
    lines.append(f"- 对手（{theirs.name}）可用攻击 → 我方场上 {mine.name}"
                 f"（HP {mine.current_hp}/{mine.max_hp}）：")
    best = 0
    shown = 0
    reasons: list[str] = []
    for sk in theirs.skills:
        if not sk.is_attack:
            continue
        blocked = _skill_blocked(sk, theirs.energy)
        if blocked:
            reasons.append(f"{sk.name}：{blocked}")
            continue
        try:
            dmg = estimate_damage(battle, theirs, mine, sk, other)
        except Exception:
            continue
        best = max(best, dmg)
        shown += 1
        kill = "**可能击杀**" if dmg >= mine.current_hp else ""
        lines.append(f"  · {sk.name} → 约 {dmg} {kill}")
    if not shown:
        lines.append("  · （对手本回合没有可用攻击"
                     + (f"：{'；'.join(reasons[:2])}" if reasons else "") + "）")
    elif best:
        lines.append(f"  · 对手本回合最高 ≈ {best}"
                     f"（我剩余 {mine.current_hp}，"
                     f"{'扛得住' if best < mine.current_hp else '扛不住一次'}）")
    # 换人候选的收益/代价（选手反复要"换人后能打多少、会挨多少"）
    from backend.sim.tactics import switch_in_damage

    bench = [sp for i, sp in enumerate(me.team)
             if i != me.active_index and not sp.is_fainted]
    if bench:
        lines.append(f"- 换人候补（换上后本回合：它打你约 / 你打它约 / 印记进场伤害）：")
        for sp in bench:
            take = 0
            for sk in theirs.skills:
                if not sk.is_attack or _skill_blocked(sk, theirs.energy):
                    continue
                try:
                    take = max(take, estimate_damage(battle, theirs, sp, sk, other))
                except Exception:
                    continue
            give = 0
            for sk in sp.skills:
                if not sk.is_attack or sk.energy_cost > sp.energy:
                    continue
                try:
                    give = max(give, estimate_damage(battle, sp, theirs, sk, side))
                except Exception:
                    continue
            entry = switch_in_damage(battle, side, sp)
            extra = f"，进场再吃 {entry}" if entry else ""
            lines.append(f"  · {sp.name}（HP {sp.current_hp}/{sp.max_hp}，能量 {sp.energy}）："
                         f"挨约 {take} / 打约 {give}{extra}")
    return "\n".join(lines)
