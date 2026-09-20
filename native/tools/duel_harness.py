# -*- coding: utf-8 -*-
"""native/tools/duel_harness.py — 让**外部 agent**（子 agent / 人）在真实引擎里逐回合对战。

本工具自己不做决策：每回合的动作由外部给出。它负责

  ① 建局（两侧队伍：`meta:<i>` = meta 库第 i 支；`file:<path>` = 自定义规格）
  ② 把局面渲染成双方可读的文本（速度线、可用技能表、冷却、血脉、印记、心力）
  ③ 把外部指令喂进引擎跑一回合，并把结果落盘

**状态管理用「动作重放」而不是序列化**：只记 `config.json` + `actions.jsonl`，
每次从开局重放到当前回合。引擎在固定输入下逐位确定（`native/tools/determinism_probe.py`
已验证），因此重放结果与逐回合存盘等价；这样也绕开了 `battle_to_dict` 在道具/复合效果
路径上的不可序列化对象（`WhenBlock`）。

用法：
    # 建局（--lead-a/--lead-b 是双方首发索引）
    env\\python.exe native/tools/duel_harness.py init --dir _duel \\
        --team-a meta:31 --team-b file:_duel/team_pro.json --lead-a 0 --lead-b 0
    # 渲染当前局面（persp=A 表示"我方"=A）
    env\\python.exe native/tools/duel_harness.py show --dir _duel --persp A
    # 走一回合：动作格式 `skill <i>` | `switch <i>` | `gather` | `item [形态] <后续动作>`
    env\\python.exe native/tools/duel_harness.py step --dir _duel \\
        --a "skill 0" --b "gather" --replacement-a 2,3,4,5 --replacement-b 1,2,3,4,5

方向约定：`--persp A` 时渲染里的"我方"就是 A 方（对应 `--a` 的指令）。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_MAX_TURNS = 30


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
    """把天赋/性格归零（两侧同口径：只比决策，不比培养）。"""
    out = []
    for sp in specs:
        sp = dict(sp)
        sp["nature"] = None
        sp["iv"] = None
        sp.pop("bloodline", None)
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


def cmd_init(args) -> None:
    dirpath = Path(args.dir)
    dirpath.mkdir(parents=True, exist_ok=True)
    cfg = {"team_a": args.team_a, "team_b": args.team_b, "seed": args.seed,
           "lead_a": args.lead_a, "lead_b": args.lead_b, "neutral": True}
    (dirpath / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    (dirpath / "actions.jsonl").write_text("", encoding="utf-8")

    _factory, battle = _build_battle(cfg)
    rng2 = random.Random(args.seed)
    _sa, ia, name_a = _load_team_spec(args.team_a, rng2)
    _sb, ib, name_b = _load_team_spec(args.team_b, rng2)
    print(f"=== 建局：A = {name_a}（道具 {getattr(ia, 'name', '无')}）"
          f"  vs  B = {name_b}（道具 {getattr(ib, 'name', '无')}）===")
    for team, player in (("A", battle.player_a), ("B", battle.player_b)):
        print(f"  [{team}] 首发 {player.active_index} {player.active.name}（速度线见 show）")
        for i, sp in enumerate(player.team):
            sl = "/".join(sk.name for sk in sp.skills)
            print(f"      {i} {sp.name:<14} HP{sp.max_hp:>4} "
                  f"速{sp.effective_stat('speed'):>4}  {'/'.join(sp.species.elements)}  {sl}")
    print(f"  （天赋/性格中立）→ 用 show 看局面、step 走回合；目录 {dirpath}")


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
             f"  物攻 {act.effective_stat('atk')} 魔攻 {act.effective_stat('sp_atk')}"
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
             f"{' 力竭' if sp.is_fainted else ''}"
             for i, sp in enumerate(player.team) if i != player.active_index]
    lines.append("      板凳：" + ("  ".join(bench) if bench else "无"))
    return lines


def _render(battle, persp: str = "A") -> str:
    p_me, p_op = ((battle.player_a, battle.player_b) if persp == "A"
                  else (battle.player_b, battle.player_a))
    my_key, op_key = ("A", "B") if persp == "A" else ("B", "A")
    g = battle.globals
    marks = {t: "、".join(f"{m.name}×{int(getattr(m, 'stacks', 0) or 0)}"
                          f"(剩{int(getattr(m, 'ttl', 0) or 0)})"
                          for m in g.mark_effects.get(t, [])) or "无"
             for t in ("A", "B")}
    out = [f"── 回合 {battle.turn} ── 天气 {g.weather or '无'}"
           f"（{'夜' if getattr(g, 'night', False) else '昼'}）"
           f"  心力 我 {p_me.lives} / 对方 {p_op.lives}",
           f"  印记 我侧：{marks[my_key]} ｜ 对方侧：{marks[op_key]}",
           f"  道具 我方 {getattr(p_me.item, 'name', '无')}"
           f"（已用 {getattr(p_me.item, 'uses', 0)}/{getattr(p_me.item, 'max_uses', 0)}）"
           f" ｜ 对方 {getattr(p_op.item, 'name', '无')}"]
    out += _side_block(battle, p_me, "我方")
    out += _side_block(battle, p_op, "对方")
    if battle.log:
        rec = battle.log[-1]
        out.append(f"  上一回合：{getattr(rec, '_header', '')}")
        evs: list[str] = []
        for side in ("action_a", "action_b"):
            ar = getattr(rec, side, None)
            if ar is not None:
                evs += list(getattr(ar, "events", []) or [])
        evs += list(getattr(rec, "faint_check_events", []) or [])
        evs += list(getattr(rec, "turn_end_events", []) or [])
        out += [f"    · {e}" for e in evs]
    return "\n".join(out)


# ══════════════════════════════════════════════════════════════════
# 动作解析与重放
# ══════════════════════════════════════════════════════════════════

class _ExternalAgent:
    """外部指令的执行器。

    指令**按名字**下达（`skill 齿轮扭矩` / `switch 泥吼牙` / `gather` / `item`），
    由它在 `choose_action` 被调用的那一刻（即引擎的"行动选择"阶段，TURN_START 之后）
    对**当前**技能表解析索引 —— 否则遇到「正位宝剑」这类每回合轮换技能槽的精灵，
    按索引下的指令会打到别的技能上。

    道具走引擎自己的道具循环（返回 `item` 动作 → 引擎 `_resolve_item` 后**再次**调用
    `choose_action`），所以指令队列里 `item` 后面要跟真正的动作。
    """

    def __init__(self, team: str, player, order: list[int], commands: list[str]):
        self.team, self.player, self.order = team, player, order
        self.commands = list(commands)
        self.notes: list[str] = []

    # ── 指令解析 ──
    def _resolve(self, cmd: str):
        from backend.sim.action import Action
        from backend.sim.agent import _GATHER_ACTION

        parts = cmd.split()
        if not parts:
            return _GATHER_ACTION
        kind = parts[0].lower()
        arg = " ".join(parts[1:]).strip()
        if kind == "gather":
            return _GATHER_ACTION
        if kind == "item":
            variant = int(arg) if arg.lstrip("-").isdigit() else None
            return Action(kind="item", variant=variant) if variant is not None else Action(kind="item")
        if kind == "switch":
            if arg.isdigit():
                return Action(kind="switch", switch_index=int(arg))
            for i, sp in enumerate(self.player.team):
                if sp.name == arg and i != self.player.active_index:
                    return Action(kind="switch", switch_index=i)
            self.notes.append(f"换人目标 {arg!r} 不在板凳里 → 改为聚能")
            return _GATHER_ACTION
        if kind == "skill":
            skills = self.player.active.skills
            if arg.isdigit():
                idx = int(arg)
                if 0 <= idx < len(skills):
                    return Action(kind="skill", skill_index=idx)
                self.notes.append(f"技能槽 {idx} 不存在 → 改为聚能")
                return _GATHER_ACTION
            for i, sk in enumerate(skills):
                if sk.name == arg:
                    if sk.sealed:
                        self.notes.append(f"{arg} 当前被封印 → 改为聚能")
                        return _GATHER_ACTION
                    return Action(kind="skill", skill_index=i)
            self.notes.append(f"当前技能表里没有 {arg!r} → 改为聚能（"
                              f"可用：{'/'.join(sk.name for sk in skills)}）")
            return _GATHER_ACTION
        self.notes.append(f"无法解析指令 {cmd!r} → 改为聚能")
        return _GATHER_ACTION

    # ── 引擎接口 ──
    def choose_action(self, battle):
        from backend.sim.agent import _GATHER_ACTION

        if not self.commands:
            return _GATHER_ACTION
        return self._resolve(self.commands.pop(0))

    def choose_lead(self, battle) -> int:
        return self.player.active_index

    def choose_replacement(self, battle) -> int:
        for i in self.order:
            if 0 <= i < len(self.player.team) and not self.player.team[i].is_fainted:
                return i
        return next(i for i, s in enumerate(self.player.team) if not s.is_fainted)

    def on_game_end(self, winner):
        pass


def _commands_from_arg(text: str) -> list[str]:
    """CLI 里的一条指令 → 队列：`item [形态] <后续动作>` 拆成 ["item ...", "后续"]。"""
    parts = text.strip().split()
    if not parts or parts[0].lower() != "item":
        return [text.strip()]
    if len(parts) == 1:
        return ["item", "gather"]
    if parts[1].lstrip("-").isdigit():
        rest = " ".join(parts[2:])
        return [f"item {parts[1]}"] + ([rest] if rest else ["gather"])
    return ["item"] + [" ".join(parts[1:])]


def _read_log(dirpath: Path) -> list[dict]:
    path = dirpath / "actions.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _replay(cfg: dict, entries: list[dict], verbose_last: bool = False):
    """从开局重放到 entries 末尾，返回 (factory, battle)；最后一条指令的解析备注会打印。"""
    factory, battle = _build_battle(cfg)
    for i, e in enumerate(entries):
        if battle.is_finished:
            break
        ag_a = _ExternalAgent("A", battle.player_a, e.get("repl_a") or [],
                              _commands_from_arg(e["a"]))
        ag_b = _ExternalAgent("B", battle.player_b, e.get("repl_b") or [],
                              _commands_from_arg(e["b"]))
        battle.execute_turn(ag_a, ag_b)
        if verbose_last and i == len(entries) - 1:
            for label, ag in (("A", ag_a), ("B", ag_b)):
                for note in ag.notes:
                    print(f"  [指令备注 {label}] {note}")
    return factory, battle


def cmd_show(args) -> None:
    dirpath = Path(args.dir)
    cfg = json.loads((dirpath / "config.json").read_text(encoding="utf-8"))
    entries = _read_log(dirpath)
    if args.turn >= 0:
        entries = entries[: args.turn]
    _factory, battle = _replay(cfg, entries)
    print(_render(battle, args.persp))
    if battle.is_finished:
        print(f"  **对局已结束：winner={battle.winner}（心力 A {battle.player_a.lives} / "
              f"B {battle.player_b.lives}）**")


def cmd_step(args) -> None:
    dirpath = Path(args.dir)
    cfg = json.loads((dirpath / "config.json").read_text(encoding="utf-8"))
    entries = _read_log(dirpath)
    if len(entries) >= args.max_turns:
        print(f"已达回合上限 {args.max_turns}，不再推进")
        return
    entry = {"a": args.a, "b": args.b,
             "repl_a": [int(x) for x in args.replacement_a.split(",") if x.strip()],
             "repl_b": [int(x) for x in args.replacement_b.split(",") if x.strip()]}
    entries.append(entry)
    _factory, battle = _replay(cfg, entries, verbose_last=True)
    with open(dirpath / "actions.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({**entry, "turn": battle.turn,
                            "finished": battle.is_finished, "winner": battle.winner},
                           ensure_ascii=False) + "\n")
    rec = battle.log[-1] if battle.log else None
    print(f"=== 回合 {battle.turn} 结算（A: {args.a} ｜ B: {args.b}）===")
    if rec is not None and hasattr(rec, "to_message"):
        print(rec.to_message())
    print(f"  finished={battle.is_finished} winner={battle.winner} "
          f"心力 A {battle.player_a.lives} / B {battle.player_b.lives}")
    if battle.is_finished:
        from backend.engine.ai.core.outcome import battle_outcome_a

        oc, reason = battle_outcome_a(battle, args.max_turns)
        print(f"  **终局：outcome_a={oc}（{reason}）→ "
              f"{'A 胜' if oc > 0 else ('B 胜' if oc < 0 else '平')}**")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.add_argument("--dir", default="_duel")
    p.add_argument("--team-a", default="meta:31")
    p.add_argument("--team-b", required=True)
    p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--lead-a", type=int, default=0)
    p.add_argument("--lead-b", type=int, default=0)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("show")
    p.add_argument("--dir", default="_duel")
    p.add_argument("--persp", default="A", choices=("A", "B"))
    p.add_argument("--turn", type=int, default=-1, help="只看前 N 回合（默认全部）")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("step")
    p.add_argument("--dir", default="_duel")
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    p.add_argument("--replacement-a", default="")
    p.add_argument("--replacement-b", default="")
    p.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    p.set_defaults(func=cmd_step)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
