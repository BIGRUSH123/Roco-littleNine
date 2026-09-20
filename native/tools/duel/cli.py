# -*- coding: utf-8 -*-
"""对局骨架的**命令行层**：会话、提示词、结算、记录与自检。实现细节在 `duel.core`。"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.stdout.reconfigure(encoding="utf-8")

from duel.core import *  # noqa: F401,F403
from duel.core import (  # noqa: F401  显式带上私有名：本模块的命令都要用
    _ACTION_ALIASES, _NAME_KEYS, _VARIANT_KEYS, _THEN_KEYS, _BENCH_KEYS, _REASON_KEYS, _item_by_name, _load_team_spec, _neutralize, _build_battle, _kind, _skill_brief, _effect_brief, _skill_line, _bloodline_note, _side_block, _last_turn_events, _render, _team_sheet, _skill_blocked, _item_block_reason, _static_post_item_skills, _item_menu, _legal_menu, _extract_json, _pick, _skill_names, _resolve_skill, _resolve_switch, _resolve_answer, _StrictAgent, _session_path, _history_path, _read_session, _read_history, _cfg_from_session, _replay, _prompt_battle, _post_item_skills_fn, _default_bench, _answer_path, _pending_path, _prompt_path, _prompt_sha, _dispatch_path, _note_path, _read_note, _normalize_note, _label, _damage_section
)


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
- 同一回合**双方同时出招**，出手顺序：先手值高的先动；先手值相同则比（印记减速后的）速度；
  **两者都相同则由引擎抛硬币（50/50，无法预判）**——所以镜像对位别默认自己先手。
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
