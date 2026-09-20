# -*- coding: utf-8 -*-
"""native/tools/duel_harness.py — 让**外部 agent**（我的子 agent / 人）按**固定流程**在真实引擎里对战。

程序自己不做决策，只固化流程的每一步，用「严格校验 + 自动记录」消灭手工搬运带来的错误：

  ① new    建局（队伍 / 首发 / 板凳顺序 / 种子）→ 写出第 1 回合的两份提示词
  ② prompt 写出（或重写）某一侧的提示词：局面 + **合法动作菜单** + 输出契约，全在一个文件里
  ③ answer 校验某一侧的 JSON 答案；通过则暂存（不通过**直接报错**，并给出真实可选菜单）
  ④ apply  两侧答案齐了才结算；结算后写下一回合提示词
  ⑤ status 一行式进度
  ⑥ record 按历史落盘自动生成 record.md（逐手记录 + 两侧所看提示词的 sha256）
  ⑦ show   给人看的当前局面
  ⑧ selftest 菜单 vs 引擎口径对拍（防提示词把可选项说少/说多）
  ⑨ notes  汇总选手自报的「缺什么信息」（去重），用来改下一轮提示词

**固定流程（子 agent 那一侧只需要两条命令）**

    env\\python.exe native/tools/duel_harness.py new --dir _duel/r1 --team-a meta:31 `
        --team-b file:native/tools/duel_teams/pro.json --lead-a 5 --lead-b 0
    # 循环：把 <dir>/dispatch_A.txt 里的指令原样发给子 agent A、dispatch_B.txt 发给 B
    #       子 agent 写 answer_<侧>.json（决定）与 note_<侧>.txt（一行：还缺什么信息）
    env\\python.exe native/tools/duel_harness.py apply --dir _duel/r1
    env\\python.exe native/tools/duel_harness.py record --dir _duel/r1

与「手敲 step」相比，这几处是上一局实测踩到的坑：

  · 指令**只能**取自提示词列出的合法动作；非法输入会报错并**保留回合**，
    不会静默降级成聚能（旧版把"技能名写错/被封印"变成一次聚能，静默改变了整局）。
  · 名字解析发生在引擎真正取招的那一刻（`choose_action`），避免「正位宝剑」这类
    轮换技能槽的索引漂移。
  · 提示词里**写明道具可用性及原因**：旧版 `_neutralize` 把血脉一并剥掉，
    A 的「进化之力」整局不可用（无「首领」血脉→无候选形态），记录却写成"agent 忘了用道具"。
  · 换人顺序由答案里的 `bench` 字段携带（默认沿用建局值），不必每回合重敲。
  · 每回合两侧**各自**看到的提示词文本会连同 sha256 一起落盘，隔离性可事后审计。

状态管理：只落盘 `session.json` + `history.jsonl`（存校验后的指令），每次从开局**重放**。
引擎在固定输入下逐位确定（`determinism_probe.py` 已验证），重放与逐回合存盘等价；
这也绕开了 `battle_to_dict` 在道具路径上的 `WhenBlock` 不可序列化问题。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_MAX_TURNS = 30
SESSION_VERSION = 3
# 提示词结构版本：改动给定信息集时递增，并随每回合落盘（记录里能看出哪几回合用了旧版）。
# v2（2026-09-21）：按选手自报缺口补上 伤害估算 / 物防魔防 / 板凳速度 / 无回能事实 / 终局算法。
PROMPT_REV = 2

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
    eff = [f"{getattr(e, 'name', '?')}×{int(getattr(e, 'stacks', 0) or 0)}"
           f"(剩{int(getattr(e, 'ttl', 0) or 0)}回合)"
           for e in getattr(act, "active_effects", [])
           if int(getattr(e, "stacks", 0) or 0) or int(getattr(e, "ttl", 0) or 0)]
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
                     f"  技能：{' / '.join(sk.name for sk in sp.skills)}")
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


# ══════════════════════════════════════════════════════════════════
# 提示词
# ══════════════════════════════════════════════════════════════════

_CONTRACT_TEMPLATE = """\
把**且仅把**一个 JSON 对象写进这个文件（写完就结束，不要写正文解释）：

    %ANSWER_PATH%

四种写法（`action` 必填）：
    {"action": "skill",  "name": "<技能名>"}                        出招
    {"action": "switch", "name": "<板凳里的精灵名>"}                 换人（换人先于技能结算）
    {"action": "gather"}                                            聚能（本回合不出招，回能量）
    {"action": "item",   "then": {后续动作}}                          用道具（不消耗行动，必须跟 then）

道具只有「进化之力」需要额外给 `"variant"`（形态号见上面「合法动作」里的道具那一行）；
其他道具**不要**写 variant。

可选字段：
    "reason": "一句话理由"         会写进对战记录，便于复盘
    "bench":  ["名1", "名2", ...]  重排你的换人上场顺序（省略 = 沿用上一回合）

另外再写**第二份文件**（一行文字，用于改进这套提示词）：

    %NOTE_PATH%

    内容 = 为了做出更好的决定，你觉得本文件（提示词）里还缺什么信息？
    只写缺什么（例如「缺双方的伤害估算表」「缺对手上一回合的能量变化」），
    没有缺的就写「无」。不要借这行写你的出招理由。

硬性要求：
  · 名字必须与「合法动作」里列出的**完全一致**。
  · 标着「不可用」的技能不要选：写了会**报错并让你重选**（引擎不会替你改成聚能）。
  · 两个文件都写完就结束。
"""

_NOTE_TEMPLATE = """\
## 上一次提交被拒绝（请重来一次）

你上次写的内容：
    %LAST%

被拒绝的原因：
    %REASON%

可以选的（与上面「合法动作」一致）：
%OPTIONS%
"""


_RULES_SECTION = """\
## 引擎口径（你必须知道的固定规则）

- 6v6 团队战：每方 4 点心力，**我方每力竭 1 只精灵 −1 点**，心力先归零的一方判负。
- 同一回合**双方同时出招**：先手值高的先动，同先手则速度高的先动。
- 技能分 攻击 / 防御 / 状态 三类，存在猜拳环：**防御克攻击、攻击克状态、状态克防御**。
  打出克制的一侧得到应对加成（减伤 / 威力倍率）；**被应对的一侧会被再打一次该技能的基础伤害**。
  技能上标的 `应对:X` 表示它只对 X 类攻击生效。
- **换人在技能结算之前发生**，所以换上来的精灵会吃到当回合的攻击。
- 能量：**开局 10、上限 10、不会自动回复**。唯一常规回能手段是 `gather`（聚能，+5），
  另有少数印记/效果回能。所以能量用完就打不出招。
- 技能表**每回合开始会"传动"**（位置轮换 + 部分槽位封印），本文件给的是**传动之后**的表，
  与引擎真正取招时的表一致；但**用道具后可能再传动一次**，那种情况本文件会写明"用后技能表"。没写就按原表。
- 打满上限未分胜负时按局面分裁决：`存活数×1.0 + 队伍血量比例×0.5 + 心力×0.25 + 在场能量比例×0.05`，
  差值不足 0.15 记平局。
- 若回合中途你的场上精灵力竭，替补按你在 `bench` 里给的顺序上场（默认沿用建局顺序）。
"""


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
    if best:
        lines.append(f"  · 对手本回合最高 ≈ {best}"
                     f"（我剩余 {mine.current_hp}，"
                     f"{'扛得住' if best < mine.current_hp else '扛不住一次'}）")
    return "\n".join(lines)


def _build_prompt(sess: dict, entries: list[dict], battle, side: str, note: str | None,
                  start_events: list[str] | None = None, post_skills_fn=None) -> str:
    turn = len(entries) + 1
    other = "B" if side == "A" else "A"
    me = battle.get_player(side)
    opp = battle.get_player(other)
    label = _label(sess, side)
    parts = [f"# 你是一名 6v6 对战选手：{label}（{side} 方）· 第 {turn} 回合",
             "",
             "你只能读本文件。**不要**读仓库里的其他文件、不要搜索、不要运行代码。",
             f"你的决定只能写进：`{_answer_path(Path(sess['dir']), side).as_posix()}`",
             ""]
    brief = sess.get(f"brief_{side.lower()}")
    if brief:
        parts += ["## 你的参考资料（唯一允许额外阅读的文件）", "",
                  f"`{Path(brief).resolve().as_posix()}`", ""]
    parts += ["## 你的队伍", "", _team_sheet(me, side, _label(sess, side)), "",
              "## 对手队伍（这是他全部 6 只精灵）", "",
              _team_sheet(opp, other, _label(sess, other)), "",
              "## 当前局面", "",
              _render(battle, side, sess.get("history", "last"))]
    if start_events:
        parts += ["", "  本回合开始时的变化（已生效）："] + [f"    · {e}" for e in start_events]
    parts += ["", _damage_section(battle, side)] if sess.get("damage_table", True) else []
    parts += ["",
              _RULES_SECTION.replace("%MAX_TURNS%", str(sess.get("max_turns", DEFAULT_MAX_TURNS))),
              "## 合法动作（只从这里选）", "",
              _legal_menu(battle, side, post_skills_fn), "",
              "## 输出契约", "",
              _CONTRACT_TEMPLATE
              .replace("%ANSWER_PATH%", _answer_path(Path(sess["dir"]), side).as_posix())
              .replace("%NOTE_PATH%", _note_path(Path(sess["dir"]), side).as_posix())]
    if note:
        parts += ["", note]
    return "\n".join(parts)


def _write_prompt(dirpath: Path, sess: dict, entries: list[dict], side: str,
                  note: str | None = None) -> Path:
    battle, start_events = _prompt_battle(sess, entries)
    post_fn = _post_item_skills_fn(sess, entries, side)
    text = _build_prompt(sess, entries, battle, side, note, start_events, post_fn)
    path = _prompt_path(dirpath, len(entries) + 1, side)
    path.write_text(text, encoding="utf-8")
    _note_path(dirpath, side).unlink(missing_ok=True)  # 上一回合的自报缺口不跨回合复用
    _answer_path(dirpath, side).unlink(missing_ok=True)
    dispatch = (f"读 {path.as_posix()}（只读这一个文件），"
                f"按文件里的「输出契约」写**两个**文件："
                f"{_answer_path(dirpath, side).as_posix()}（你的决定）和 "
                f"{_note_path(dirpath, side).as_posix()}（一行：提示词里还缺什么信息，没有就写「无」）。"
                f"不要读仓库其他文件、不要运行代码、不要输出其他内容。")
    _dispatch_path(dirpath, side).write_text(dispatch, encoding="utf-8")
    return path


def _retry_note(dirpath: Path, turn: int, side: str, last: str, reason: str,
                options: list[str]) -> str:
    opts = "\n".join(f"    - {o}" for o in options) if options else "    - （见上）"
    return (_NOTE_TEMPLATE.replace("%LAST%", (last or "").strip()[:400])
            .replace("%REASON%", reason).replace("%OPTIONS%", opts))


# ══════════════════════════════════════════════════════════════════
# 命令
# ══════════════════════════════════════════════════════════════════

def cmd_new(args) -> None:
    dirpath = Path(args.dir)
    dirpath.mkdir(parents=True, exist_ok=True)
    sess = {"version": SESSION_VERSION, "dir": str(dirpath.resolve()),
            "team_a": args.team_a, "team_b": args.team_b, "seed": args.seed,
            "lead_a": args.lead_a, "lead_b": args.lead_b,
            "max_turns": args.max_turns, "neutral": not args.no_neutral,
            "history": args.history, "damage_table": not args.no_damage_table,
            "brief_a": args.brief_a, "brief_b": args.brief_b,
            "labels": {"a": args.label_a, "b": args.label_b}}
    _factory, battle = _build_battle(_cfg_from_session(sess))
    if not args.bench_a:
        sess["bench_a"] = _default_bench(battle.player_a, args.lead_a)
    else:
        sess["bench_a"] = [x.strip() for x in args.bench_a.split(",") if x.strip()]
    if not args.bench_b:
        sess["bench_b"] = _default_bench(battle.player_b, args.lead_b)
    else:
        sess["bench_b"] = [x.strip() for x in args.bench_b.split(",") if x.strip()]
    _session_path(dirpath).write_text(json.dumps(sess, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    _history_path(dirpath).write_text("", encoding="utf-8")
    for side in ("A", "B"):
        _answer_path(dirpath, side).unlink(missing_ok=True)
        _pending_path(dirpath, side).unlink(missing_ok=True)

    print(f"=== 建局：A = {_label(sess, 'A')}（道具 {getattr(battle.player_a.item, 'name', '无')}）"
          f"  vs  B = {_label(sess, 'B')}（道具 {getattr(battle.player_b.item, 'name', '无')}）===")
    for side, player in (("A", battle.player_a), ("B", battle.player_b)):
        print(f"  [{side}] 首发 {player.active_index} {player.active.name}"
              f"  板凳顺序 {sess[f'bench_{side.lower()}']}")
        for i, sp in enumerate(player.team):
            print(f"      {i} {sp.name:<14} HP {sp.max_hp:<4} 速 {sp.effective_stat('speed'):<4}"
                  f" 血脉 {sp.bloodline or '-':<4} {'/'.join(sp.species.elements):<6}"
                  f" {'/'.join(sk.name for sk in sp.skills)}")
    for side in ("A", "B"):
        reason = _item_block_reason(battle, side)
        print(f"  道具可用性 [{side}] {getattr(battle.get_player(side).item, 'name', '无')}："
              f"{'可用' if reason is None else '不可用 —— ' + reason}")
    print(f"  （天赋/性格中立，血脉保留；上限 {args.max_turns} 回合）")
    for side in ("A", "B"):
        path = _write_prompt(dirpath, sess, [], side)
        print(f"  [{side}] 第 1 回合提示词 → {path.as_posix()}")
    print(f"  子 agent 指令：{_dispatch_path(dirpath, 'A').as_posix()}"
          f" / {_dispatch_path(dirpath, 'B').as_posix()}")


def cmd_prompt(args) -> None:
    dirpath = Path(args.dir)
    sess = _read_session(dirpath)
    entries = _read_history(dirpath)
    sides = ("A", "B") if args.side == "both" else (args.side,)
    for side in sides:
        note = None
        if args.reason:
            last = args.last or ""
            opts = args.options.split("|") if args.options else []
            note = _retry_note(dirpath, len(entries) + 1, side, last, args.reason, opts)
        path = _write_prompt(dirpath, sess, entries, side, note)
        print(f"[{side}] → {path.as_posix()}")
        if args.print:
            print("-" * 72)
            print(path.read_text(encoding="utf-8"))
            print("-" * 72)


def cmd_answer(args) -> None:
    dirpath = Path(args.dir)
    sess = _read_session(dirpath)
    entries = _read_history(dirpath)
    battle, _events = _prompt_battle(sess, entries)
    text = args.json
    if text is None:
        src = Path(args.file) if args.file else _answer_path(dirpath, args.side)
        if not src.exists():
            raise SystemExit(f"没有找到答案文件 {src}")
        text = src.read_text(encoding="utf-8")
    try:
        ans = _extract_json(text)
        commands, bench, reason = _resolve_answer(
            battle, args.side, ans,
            post_skills_fn=_post_item_skills_fn(sess, entries, args.side))
    except (CommandError, ValueError) as exc:
        reason_txt = exc.message if isinstance(exc, CommandError) else str(exc)
        options = exc.options if isinstance(exc, CommandError) else []
        print(f"ERROR [{args.side}] {reason_txt}", file=sys.stderr)
        if options:
            print("  可选：" + " / ".join(options), file=sys.stderr)
        note = _retry_note(dirpath, len(entries) + 1, args.side, text, reason_txt, options)
        path = _write_prompt(dirpath, sess, entries, args.side, note)
        print(f"  重试提示词 → {path.as_posix()}", file=sys.stderr)
        raise SystemExit(2)
    payload = {"turn": len(entries) + 1, "answer": ans, "commands": commands,
               "bench": bench, "reason": reason, "raw": text,
               "note": _read_note(dirpath, args.side, args.note),
               "prompt_sha": _prompt_sha(dirpath, len(entries) + 1, args.side)}
    _pending_path(dirpath, args.side).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK [{args.side}] 第 {len(entries) + 1} 回合已受理："
          f"{' + '.join(commands)}" + (f"（{reason}）" if reason else ""))
    if payload["note"]:
        print(f"  自报缺口：{payload['note']}")
    other = "B" if args.side == "A" else "A"
    if _pending_path(dirpath, other).exists():
        print(f"  两侧齐了 → 现在可以 apply")
    else:
        print(f"  等 {other} 方的答案（或 apply 会提示缺哪边）")


def _run_turn(sess: dict, dirpath: Path, entries: list[dict], ans_a: dict, ans_b: dict):
    """重放 + 结算一回合；返回 (battle, entry)。任何指令问题都抛 CommandError。"""
    battle = _replay(sess, entries)
    ag_a = _StrictAgent("A", battle.player_a, ans_a["commands"], ans_a["bench"] or sess["bench_a"])
    ag_b = _StrictAgent("B", battle.player_b, ans_b["commands"], ans_b["bench"] or sess["bench_b"])
    battle.execute_turn(ag_a, ag_b)
    rec = battle.log[-1] if battle.log else None
    turn = len(entries) + 1
    entry = {"turn": turn, "prompt_rev": PROMPT_REV,
             "sha_a": ans_a.get("prompt_sha") or _prompt_sha(dirpath, turn, "A"),
             "sha_b": ans_b.get("prompt_sha") or _prompt_sha(dirpath, turn, "B"),
             "answer_a": ans_a["answer"], "answer_b": ans_b["answer"],
             "raw_a": ans_a["raw"], "raw_b": ans_b["raw"],
             "commands_a": ans_a["commands"], "commands_b": ans_b["commands"],
             "bench_a": ag_a.bench, "bench_b": ag_b.bench,
             "reason_a": ans_a.get("reason", ""), "reason_b": ans_b.get("reason", ""),
             "note_a": ans_a.get("note", ""), "note_b": ans_b.get("note", ""),
             "events": _last_turn_events(battle),
             "log": rec.to_message() if rec is not None and hasattr(rec, "to_message") else "",
             "header": getattr(rec, "_header", "") if rec is not None else "",
             "lives_a": battle.player_a.lives, "lives_b": battle.player_b.lives,
             "item_a": _item_usage(battle.player_a), "item_b": _item_usage(battle.player_b),
             "finished": bool(battle.is_finished), "winner": battle.winner}
    return battle, entry


def _item_usage(player) -> str:
    item = getattr(player, "item", None)
    if item is None:
        return "-"
    return f"{item.uses}/{item.max_uses}"


def _load_pending(dirpath: Path, side: str) -> dict:
    """读暂存答案（cmd_answer 已校验过，含 commands/prompt_sha）。"""
    path = _pending_path(dirpath, side)
    if not path.exists():
        raise SystemExit(f"[{side}] 还没有答案（先跑 answer --side {side}，或 apply --{side.lower()} <json>）")
    return json.loads(path.read_text(encoding="utf-8"))


def _checked_answers(dirpath: Path, sess: dict, entries: list[dict], battle,
                     inline: dict[str, str | None]) -> tuple[dict, list]:
    """两侧答案 → 校验过的 payload。返回 (parsed, failures)，failures 里是要重试的侧。"""
    parsed: dict[str, dict] = {}
    failures: list[tuple[str, str, str, list[str]]] = []
    turn = len(entries) + 1
    for side in ("A", "B"):
        raw_inline = inline.get(side)
        if raw_inline is None:
            parsed[side] = _load_pending(dirpath, side)
            if parsed[side].get("turn") != turn:
                raise SystemExit(f"[{side}] 暂存答案属于第 {parsed[side].get('turn')} 回合，"
                                 f"当前是第 {turn} 回合（先跑 prompt 重新出提示词）")
            continue
        try:
            ans = _extract_json(raw_inline)
            commands, bench, why = _resolve_answer(
                battle, side, ans,
                post_skills_fn=_post_item_skills_fn(sess, entries, side))
        except (CommandError, ValueError) as exc:
            msg = exc.message if isinstance(exc, CommandError) else str(exc)
            opts = exc.options if isinstance(exc, CommandError) else []
            failures.append((side, raw_inline, msg, opts))
            continue
        parsed[side] = {"turn": turn, "answer": ans, "commands": commands, "bench": bench,
                        "reason": why, "raw": raw_inline,
                        "note": _read_note(dirpath, side),
                        "prompt_sha": _prompt_sha(dirpath, turn, side)}
    return parsed, failures


def cmd_apply(args) -> None:
    dirpath = Path(args.dir)
    sess = _read_session(dirpath)
    entries = _read_history(dirpath)
    if entries and entries[-1].get("finished"):
        print(f"对局已结束：winner={entries[-1].get('winner')}（不必再 apply）")
        return
    turn = len(entries) + 1
    if turn > sess["max_turns"]:
        print(f"已达回合上限 {sess['max_turns']}，用 record 出记录")
        return

    battle, _events = _prompt_battle(sess, entries)
    parsed, failures = _checked_answers(dirpath, sess, entries, battle,
                                        {"A": args.a, "B": args.b})
    if failures:
        for side, raw, msg, opts in failures:
            print(f"ERROR [{side}] {msg}", file=sys.stderr)
            if opts:
                print("  可选：" + " / ".join(opts), file=sys.stderr)
            note = _retry_note(dirpath, turn, side, raw, msg, opts)
            path = _write_prompt(dirpath, sess, entries, side, note)
            print(f"  该回合未结算，重试提示词 → {path.as_posix()}", file=sys.stderr)
        raise SystemExit(2)

    try:
        battle, entry = _run_turn(sess, dirpath, entries, parsed["A"], parsed["B"])
    except CommandError as exc:
        raw = (parsed.get(exc.side) or {}).get("raw", "")
        note = _retry_note(dirpath, turn, exc.side, raw, exc.message, exc.options)
        path = _write_prompt(dirpath, sess, entries, exc.side, note)
        print(f"ERROR [{exc.side}] {exc.message}", file=sys.stderr)
        print(f"  该回合未结算，重试提示词 → {path.as_posix()}", file=sys.stderr)
        raise SystemExit(2) from exc

    with open(_history_path(dirpath), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    for side in ("A", "B"):
        _pending_path(dirpath, side).unlink(missing_ok=True)
        _answer_path(dirpath, side).unlink(missing_ok=True)

    print(f"=== 第 {entry['turn']} 回合结算：A {entry['commands_a']} ｜ B {entry['commands_b']} ===")
    if entry["log"]:
        print(entry["log"])
    print(f"  心力 A {entry['lives_a']} / B {entry['lives_b']}"
          f"  道具 A {entry['item_a']} / B {entry['item_b']}")
    if entry["finished"]:
        from backend.engine.ai.core.outcome import battle_outcome_a

        oc, why = battle_outcome_a(battle, sess["max_turns"])
        print(f"  **终局：winner={entry['winner']} outcome_a={oc}（{why}）→ "
              f"{'A 胜' if oc > 0 else ('B 胜' if oc < 0 else '平')}**")
        print(f"  出记录：record --dir {dirpath.as_posix()}")
    else:
        for side in ("A", "B"):
            path = _write_prompt(dirpath, sess, entries + [entry], side)
            print(f"  [{side}] 第 {entry['turn'] + 1} 回合提示词 → {path.as_posix()}")


def cmd_status(args) -> None:
    dirpath = Path(args.dir)
    sess = _read_session(dirpath)
    entries = _read_history(dirpath)
    battle = _replay(sess, entries)
    print(f"A {_label(sess, 'A')} vs B {_label(sess, 'B')}  已走 {len(entries)} 回合"
          f"（上限 {sess['max_turns']}，seed {sess['seed']}）")
    print(f"  当前局面：A {battle.player_a.active.name} HP "
          f"{battle.player_a.active.current_hp}/{battle.player_a.active.max_hp}"
          f" / B {battle.player_b.active.name} HP "
          f"{battle.player_b.active.current_hp}/{battle.player_b.active.max_hp}"
          f"   心力 A {battle.player_a.lives} / B {battle.player_b.lives}")
    for side in ("A", "B"):
        item = battle.get_player(side).item
        reason = _item_block_reason(battle, side)
        print(f"  道具 [{side}] {getattr(item, 'name', '无')} {_item_usage(battle.get_player(side))}"
              f"：{'可用' if reason is None else '不可用 —— ' + reason}")
    done = {side: _pending_path(dirpath, side).exists() for side in ("A", "B")}
    print(f"  本回合答案：A {'已受理' if done['A'] else '缺'} / B {'已受理' if done['B'] else '缺'}")
    if entries and entries[-1].get("finished"):
        print(f"  **已结束：winner={entries[-1]['winner']}**")
    else:
        for side in ("A", "B"):
            print(f"  [{side}] 提示词 {_prompt_path(dirpath, len(entries) + 1, side).as_posix()}"
                  f"  指令 {_dispatch_path(dirpath, side).as_posix()}")


def cmd_show(args) -> None:
    dirpath = Path(args.dir)
    sess = _read_session(dirpath)
    entries = _read_history(dirpath)
    if args.turn >= 0:
        entries = entries[: args.turn]
    battle = _replay(sess, entries)
    print(_render(battle, args.persp, sess.get("history", "last")))
    if battle.is_finished:
        print(f"  **对局已结束：winner={battle.winner}（心力 A {battle.player_a.lives} / "
              f"B {battle.player_b.lives}）**")


def cmd_notes(args) -> None:
    """汇总选手自报的「缺什么信息」（去重，只列每条的首次出现）。"""
    dirpath = Path(args.dir)
    _read_session(dirpath)
    notes = collect_notes(dirpath)
    if not notes:
        print("（还没有选手自报缺口）")
        return
    print(f"选手自报的信息缺口（去重后 {len(notes)} 条）——用于下一轮改进提示词，不影响本局：")
    for side, turn, note in notes:
        print(f"  · [{side}] T{turn}：{note}")


def cmd_record(args) -> None:
    dirpath = Path(args.dir)
    sess = _read_session(dirpath)
    entries = _read_history(dirpath)
    battle = _replay(sess, entries)
    out = Path(args.out) if args.out else dirpath / "record.md"
    lines = [f"# 对战记录：{_label(sess, 'A')}（A） vs {_label(sess, 'B')}（B）", "",
             f"- 协议：duel_harness v{SESSION_VERSION}（固定流程：提示词 → 严格校验 → 结算 → 记录）",
             f"- 建局：seed {sess['seed']}，首发 A#{sess['lead_a']} / B#{sess['lead_b']}，"
             f"上限 {sess['max_turns']} 回合，天赋/性格中立（血脉保留）",
             f"- 队伍：A = `{sess['team_a']}`；B = `{sess['team_b']}`",
             f"- 换人顺序：A {sess['bench_a']}；B {sess['bench_b']}",
             f"- 已走 {len(entries)} 回合，道具使用 A {_item_usage(battle.player_a)}"
             f" / B {_item_usage(battle.player_b)}", "",
             "## 隔离审计（每回合两侧各自看到的提示词）", "",
             "下表 sha256[:16] 是**提交答案那一刻**该侧提示词文件的摘要：",
             "两侧提示词只差「我方/对方」的视角，各自只包含自己的队伍、对手队伍、局面、合法动作与契约；",
             "`-` 表示该回合不是走 answer 通道提交的（脚本化驱动）。",
             "`rev` 是提示词结构版本 —— 中途升级过就以它区分（同一局里两侧同版本）。", "",
             "| 回合 | rev | A 提示词 sha256[:16] | B 提示词 sha256[:16] |", "|---|---|---|---|"]
    for e in entries:
        lines.append(f"| {e['turn']} | v{e.get('prompt_rev', 1)} | "
                     f"`{e['sha_a'] or '-'}` | `{e['sha_b'] or '-'}` |")
    lines += ["", "## 逐手记录", "",
              "| 回合 | A 出招 | B 出招 | 结果 |", "|---|---|---|---|"]
    for e in entries:
        ev = "；".join(e["events"]).replace("|", "/").replace("\n", " ")
        a = " / ".join(e["commands_a"]) or "-"
        b = " / ".join(e["commands_b"]) or "-"
        lines.append(f"| {e['turn']} | {a} | {b} | {ev or '-'} |")
    lines += ["", "## 终局", ""]
    if battle.is_finished:
        from backend.engine.ai.core.outcome import battle_outcome_a

        oc, why = battle_outcome_a(battle, sess["max_turns"])
        lines.append(f"- winner={battle.winner}，outcome_a={oc}（{why}）")
    else:
        lines.append(f"- 打到第 {len(entries)} 回合未结束（上限 {sess['max_turns']}）")
    lines.append(f"- 心力 A {battle.player_a.lives} / B {battle.player_b.lives}")
    lines.append("- 双方理由（answer 里的 reason 字段）：")
    for e in entries:
        if e.get("reason_a") or e.get("reason_b"):
            lines.append(f"  - T{e['turn']} A：{e.get('reason_a') or '-'} ｜ B：{e.get('reason_b') or '-'}")
    notes = collect_notes(dirpath)
    lines += ["", "## 选手自报的信息缺口（去重；用于下一轮改提示词，不影响本局）", ""]
    if notes:
        lines += [f"- [{side}] T{turn}：{note}" for side, turn, note in notes]
    else:
        lines.append("- （无）")
    lines += ["", "## 附录：每回合引擎原文", ""]
    for e in entries:
        lines += [f"### 第 {e['turn']} 回合（A {e['commands_a']} ｜ B {e['commands_b']}）", "",
                  "```", (e.get("log") or "；".join(e["events"])).strip(), "```", "",
                  f"A 原始答案：`{e.get('raw_a', '')}`", "",
                  f"B 原始答案：`{e.get('raw_b', '')}`", ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"记录 → {out.as_posix()}（{len(entries)} 回合）")


def cmd_selftest(args) -> None:
    """菜单 vs 引擎对拍：菜单说「可用」的必须真能用，说「不可用」的必须真不能用。

    这条不变量专门防"提示词把玩家的可选项说少了/说多了"——那会让整局的记录失真
    （旧版把 A 的进化之力整局写成不可用，于是"没用道具"被误读成 agent 的失误）。
    """
    from backend.common.constants import ELEMENTAL_BLOODLINES
    from backend.sim.item_policy import bloodline_skill

    dirpath = Path(args.dir)
    sess = _read_session(dirpath)
    entries = _read_history(dirpath)
    battle, _events = _prompt_battle(sess, entries)
    problems: list[str] = []
    for side in ("A", "B"):
        player = battle.get_player(side)
        sprite = player.active
        item = player.item
        reason = _item_block_reason(battle, side)
        if item is None:
            engine_ok = False
        elif item.name == "进化之力":
            engine_ok = bool(battle.item_variants(side))  # 内部已含 can_use
        elif item.name == "愿力":
            engine_ok = (item.can_use(battle.turn)
                         and sprite.bloodline in ELEMENTAL_BLOODLINES
                         and bloodline_skill(battle, sprite) is not None)
        else:
            engine_ok = False
        if (reason is None) != bool(engine_ok):
            problems.append(f"[{side}] 道具口径不一致：菜单={reason!r}，引擎可用={engine_ok}")
        for sk in sprite.skills:
            blocked = _skill_blocked(sk, sprite.energy)
            if (blocked is None) != (not sk.sealed and sk.cooldown <= 0
                                     and sk.energy_cost <= sprite.energy):
                problems.append(f"[{side}] 技能「{sk.name}」口径不一致：菜单={blocked!r}")
        # 菜单里列出的动作必须真的通过校验（反向：校验器不能比菜单更严）
        for sk in sprite.skills:
            if _skill_blocked(sk, sprite.energy):
                continue
            try:
                _resolve_answer(battle, side, {"action": "skill", "name": sk.name})
            except CommandError as exc:
                problems.append(f"[{side}] 菜单列了「{sk.name}」但校验拒绝：{exc.message}")
        for sp in player.team:
            if sp is player.active or sp.is_fainted:
                continue
            try:
                _resolve_answer(battle, side, {"action": "switch", "name": sp.name})
            except CommandError as exc:
                problems.append(f"[{side}] 菜单列了换人「{sp.name}」但校验拒绝：{exc.message}")
        try:
            _resolve_answer(battle, side, {"action": "gather"})
        except CommandError as exc:
            problems.append(f"[{side}] gather 被拒：{exc.message}")
        if reason is None:
            probe = ({"action": "item", "variant": 0, "then": {"action": "gather"}}
                     if item.name == "进化之力"
                     else {"action": "item", "then": {"action": "gather"}})
            try:
                _resolve_answer(battle, side, probe,
                                post_skills_fn=_post_item_skills_fn(sess, entries, side))
            except CommandError as exc:
                problems.append(f"[{side}] 菜单说道具可用但校验拒绝：{exc.message}")
        # 用后技能表必须能在提示词里说清（首领化会再传动，说错=选手必被拒）
        if reason is None:
            post = _post_item_skills_fn(sess, entries, side)(
                0 if item.name == "进化之力" else None)
            if item.name == "进化之力" and not post:
                problems.append(f"[{side}] 进化之力可用但模拟不出用后技能表")
    if problems:
        print(f"自检失败（{len(problems)} 项）：")
        for p in problems:
            print(f"  · {p}")
        raise SystemExit(1)
    print(f"自检通过：第 {len(entries) + 1} 回合的菜单与引擎口径一致"
          f"（两侧道具可用性：A {'可用' if _item_block_reason(battle, 'A') is None else '不可用'}"
          f" / B {'可用' if _item_block_reason(battle, 'B') is None else '不可用'}）")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="固定流程的双 agent 对战骨架")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", help="建局并写出第 1 回合提示词")
    p.add_argument("--dir", default="_duel")
    p.add_argument("--team-a", default="meta:31")
    p.add_argument("--team-b", required=True)
    p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--lead-a", type=int, default=0)
    p.add_argument("--lead-b", type=int, default=0)
    p.add_argument("--bench-a", default="", help="逗号分隔的换人顺序（默认=队伍顺序去掉首发）")
    p.add_argument("--bench-b", default="")
    p.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    p.add_argument("--history", default="last", choices=("last", "full", "none"),
                   help="提示词里给多少历史（默认只给上一回合）")
    p.add_argument("--no-damage-table", action="store_true",
                   help="不给伤害估算表（默认给；A/B 实验用）")
    p.add_argument("--brief-a", default=None, help="A 方唯一允许额外阅读的文件")
    p.add_argument("--brief-b", default=None)
    p.add_argument("--label-a", default="队伍A")
    p.add_argument("--label-b", default="队伍B")
    p.add_argument("--no-neutral", action="store_true", help="保留天赋/性格（默认中立）")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("prompt", help="写出（或重写）某一侧的提示词")
    p.add_argument("--dir", default="_duel")
    p.add_argument("--side", default="both", choices=("A", "B", "both"))
    p.add_argument("--print", action="store_true", help="把提示词打到屏幕上")
    p.add_argument("--last", default=None, help="重试时：上次提交的内容")
    p.add_argument("--reason", default=None, help="重试时：被拒绝的原因")
    p.add_argument("--options", default=None, help="重试时：可选清单（用 | 分隔）")
    p.set_defaults(func=cmd_prompt)

    p = sub.add_parser("answer", help="校验并暂存一侧的答案")
    p.add_argument("--dir", default="_duel")
    p.add_argument("--side", required=True, choices=("A", "B"))
    p.add_argument("--json", default=None, help="直接给 JSON 文本")
    p.add_argument("--file", default=None, help="从文件读（默认 answer_<侧>.json）")
    p.add_argument("--note", default=None, help="选手自报的缺口（默认读 note_<侧>.txt）")
    p.set_defaults(func=cmd_answer)

    p = sub.add_parser("apply", help="两侧齐了 → 结算本回合")
    p.add_argument("--dir", default="_duel")
    p.add_argument("--a", default=None, help="A 方答案 JSON（覆盖暂存）")
    p.add_argument("--b", default=None, help="B 方答案 JSON（覆盖暂存）")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("status", help="一行式进度")
    p.add_argument("--dir", default="_duel")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("record", help="生成 record.md")
    p.add_argument("--dir", default="_duel")
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("selftest", help="菜单 vs 引擎口径对拍（防提示词说少/说多可选项）")
    p.add_argument("--dir", default="_duel")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("notes", help="汇总选手自报的「缺什么信息」（去重）")
    p.add_argument("--dir", default="_duel")
    p.set_defaults(func=cmd_notes)

    p = sub.add_parser("show", help="给人看的当前局面")
    p.add_argument("--dir", default="_duel")
    p.add_argument("--persp", default="A", choices=("A", "B"))
    p.add_argument("--turn", type=int, default=-1, help="只看前 N 回合（默认全部）")
    p.set_defaults(func=cmd_show)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
