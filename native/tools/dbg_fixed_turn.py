"""dbg_fixed_turn — 固定动作逐回合复现（把 MCTS 仿真步的分歧从树里剥离）。

用法：env\\python.exe native/tools/dbg_fixed_turn.py <spec_no> "3,0;1,0" [--dump]

actions 形如 "3,0;1,0"（A动作,B动作；B<0 表示聚能）。默认力竭换人用
「首只存活」（对齐 rust FirstAliveRepl）。
"""

from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np  # noqa: E402
import roco_engine  # noqa: E402

from backend.engine.test_rust_gate import battle_from_spec, gate_digest  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402


class _FirstAliveProxy:
    """固定动作代理：choose_action 返回预定动作，换人取首只存活（对齐
    rust FirstAliveRepl）。"""

    def __init__(self, team: str, battle) -> None:
        self.team = team
        self.battle = battle
        self.action = None

    def choose_action(self, battle):
        return self.action

    def choose_replacement(self, battle) -> int:
        # 与 rust FirstAliveRepl 对齐：排除当前在场位；无可换返回 -1
        if self.team == "A":
            team, active = battle.player_a.team, battle.player_a.active_index
        else:
            team, active = battle.player_b.team, battle.player_b.active_index
        for i, s in enumerate(team):
            if not s.is_fainted and i != active:
                return i
        return -1


def _dump(d) -> str:
    return json.dumps(d, ensure_ascii=False, sort_keys=True)


def _install_dmg_traces() -> None:
    """打印 py 侧伤害链路输入（与 rust ROCO_DEBUG_DMG 打印对齐）。"""
    if os.environ.get("ROCO_DBG_CHOICE"):
        import random as _rnd

        _orig_choice = _rnd.choice

        def _choice(seq):
            r = _orig_choice(seq)
            print(f"[py choice] seq={list(seq)} -> {r}", flush=True)
            return r

        _rnd.choice = _choice

    import backend.vm.ops.hit as hit_mod

    orig_hit = hit_mod.calc_damage

    def hit_calc(power, atk_base, def_base, **kw):
        out = orig_hit(power, atk_base, def_base, **kw)
        print(
            f"[py op_hit] power={power} atk={atk_base} def={def_base} kw={kw} -> {out}",
            flush=True,
        )
        return out

    hit_mod.calc_damage = hit_calc

    import backend.engine.modifiers as mods_mod

    orig_adj = mods_mod.adjust_damage

    def adj(dmg, mods):
        out = orig_adj(dmg, mods)
        print(f"[py adjust] in={dmg.amount} mods={mods} -> {out.amount}", flush=True)
        return out

    mods_mod.adjust_damage = adj

    if os.environ.get("ROCO_DBG_TICK"):
        from backend.sim import resolver as _res

        _orig_te = _res.SkillResolver.turn_end

        def _te(sprites, globals_):
            for s in sprites.values():
                for e in s.active_effects:
                    print(
                        f"[py tick-scan] {s.name} eff={type(e).__name__}:{e.name}"
                        f" stacks={getattr(e, 'stacks', None)}"
                        f" pct={getattr(e, 'tick_damage_pct', None)} scope={e.scope}",
                        flush=True,
                    )
            out = _orig_te(sprites, globals_)
            for s in sprites.values():
                print(
                    f"[py tick-done] {s.name} hp={s.current_hp}"
                    f" last_dmg={dict(getattr(s, '_last_abnormal_dmg', {}))}",
                    flush=True,
                )
            return out

        _res.SkillResolver.turn_end = _te

    import backend.engine.replayer as repl_mod

    if os.environ.get("ROCO_DBG_TRANSFORM"):
        from backend.sim.sprite import Sprite as _Sp

        _orig_ce = _Sp.clear_effects

        def _ce(self, scope):
            before_mods = dict(self._mod_scopes)
            turn_effects = [
                (e.name, e.scope) for e in self.active_effects if e.scope == scope
            ]
            _orig_ce(self, scope)
            print(
                f"[py clear] {self.name} scope={scope} turn_effects={turn_effects}"
                f" mod_scopes={before_mods} after={dict(self._mod_scopes)}",
                flush=True,
            )
            return None

        _Sp.clear_effects = _ce

        _orig_rebuild = _Sp._rebuild_stat_cache

        _orig_inv = _Sp._invalidate_stat_cache

        def _inv(self):
            import traceback

            stack = [
                f"{f.name.split('.')[-1]}:{f.lineno}"
                for f in traceback.extract_stack()[:-1]
                if "backend" in (f.filename or "")
            ][-3:]
            print(
                f"[py inv-stat] {self.name} stack={stack}",
                flush=True,
            )
            return _orig_inv(self)

        _Sp._invalidate_stat_cache = _inv

        def _rebuild(self):
            out = _orig_rebuild(self)
            import traceback

            stack = [
                f"{f.name.split('.')[-1]}:{f.lineno}"
                for f in traceback.extract_stack()[:-1]
                if "backend" in (f.filename or "")
            ][-3:]
            print(
                f"[py stat-cache] {self.name} atk={self._cached_atk}"
                f" mods_atk={self._modifiers.get('atk', 0)} base={self.initial_stats.get('atk')}"
                f" stages={dict(self._cached_stages)} stack={stack}",
                flush=True,
            )
            return out

        _Sp._rebuild_stat_cache = _rebuild

    orig_apply = repl_mod.JournalReplayer._apply_damage

    def apply_damage(self, m):
        out = orig_apply(self, m)
        print(f"[py apply_damage] amount={m.amount} target={m.target} -> {out}", flush=True)
        return out

    repl_mod.JournalReplayer._apply_damage = apply_damage

    if os.environ.get("ROCO_DBG_TRANSFORM"):
        import backend.vm.ops.hit as _hit_guard  # noqa: F401  确认模块已加载

    if os.environ.get("ROCO_DBG_MOD"):
        import backend.engine.replayer as _rm

        orig_mod = _rm.JournalReplayer._apply_modifier

        def apply_mod(self, m):
            ss = getattr(self, "_self_skill", "MISSING")
            print(
                f"[py mod] stat={m.stat} target={m.target!r} scope={m.scope} mode={m.mode} "
                f"value={m.value} source={m.source!r} filter={m.skill_filter!r}"
                f" _self_skill={type(ss).__name__ if ss is not None else 'None'}"
                f" ss.skill={getattr(ss, 'skill', 'NA')!r}",
                flush=True,
            )
            return orig_mod(self, m)

        _rm.JournalReplayer._apply_modifier = apply_mod
        # 派发表在 import 期固化（_DISPATCH），必须同步替换
        from backend.vm.journal import ModifierInjection as _MI

        _rm.JournalReplayer._DISPATCH[_MI] = apply_mod

    if os.environ.get("ROCO_DBG_CNT"):
        import traceback

        from backend.sim.battle import Battle as _B

        orig_cnt = _B.inc_team_counter

        def inc_cnt(self, team, key, amount=1):
            stack = [
                f"{f.name}:{f.lineno}"
                for f in traceback.extract_stack()[:-1]
                if "backend" in (f.filename or "")
            ][-4:]
            print(f"[py inc_team_counter] {team} {key} +{amount} turn={self.turn} <- {stack}")
            return orig_cnt(self, team, key, amount)

        _B.inc_team_counter = inc_cnt


def _build_py_battle(spec: dict):
    random.seed(spec["seed"] + 1)
    np.random.seed((spec["seed"] + 1) % (2**32 - 1))
    battle = battle_from_spec(spec)
    a = RuleAgent("A", battle.player_a)
    b = RuleAgent("B", battle.player_b)
    battle.player_a.active_index = a.choose_lead(battle)
    battle.player_b.active_index = b.choose_lead(battle)
    battle._invalidate_ctx_team_cache()
    battle._mcts_sim = True
    return battle


def _compare(spec: dict, sims, *, dump: bool = False, expected=None) -> bool:
    """py/rust 固定动作逐轮仿真比对。

    sims: [[(a_idx, b_idx), ...], ...]（每轮仿真的动作序列；轮间回滚 state
    但不回滚 RNG，与 MCTS save/restore 一致）。
    expected: MCTS 记录的 digest_trace（每步 {"b","mti","np","d"}），用于
    校验重放路径与仿真路径是否同轨迹。
    """
    from backend.engine.ai.core.mcts import action_index_to_action
    from backend.engine.test_rust_gate import _first_diff

    if os.environ.get("ROCO_DBG_DMG"):
        _install_dmg_traces()

    battle = _build_py_battle(spec)
    proxy_a = _FirstAliveProxy("A", battle)
    proxy_b = _FirstAliveProxy("B", battle)
    py_digests: list[list[dict]] = []
    for sim in sims:
        # 每轮仿真重新取快照（对齐 mcts.py 主循环）。不可复用同一份快照：
        # restore_mutable_state 直接别名 saved 里的内层 dict（如 team_counters
        # 的计数表），仿真期间的写入会污染快照本身。
        saved = battle.save_mutable_state()
        sim_d: list[dict] = []
        for a_idx, b_idx in sim:
            act_a = action_index_to_action(battle.player_a, a_idx)
            act_b = None if b_idx < 0 else action_index_to_action(battle.player_b, b_idx)
            if os.environ.get("ROCO_DBG_ACT"):
                print(
                    f"  [replay] sim={len(py_digests)} turn={battle.turn} "
                    f"a_idx={a_idx} -> {act_a!r} | b_idx={b_idx} -> {act_b!r}"
                )
            proxy_a.action = act_a
            proxy_b.action = act_b
            battle.execute_turn_headless(
                proxy_a, proxy_b, fixed_action_a=act_a, fixed_action_b=act_b
            )
            sim_d.append(gate_digest(battle))
        battle.restore_mutable_state(saved)
        py_digests.append(sim_d)

    ru = json.loads(
        roco_engine.py_fixed_turns(
            json.dumps(spec, ensure_ascii=False),
            json.dumps([[[a, b] for a, b in sim] for sim in sims]),
        )
    )
    ru_digests: list[list[dict]] = ru["turns"]

    if os.environ.get("ROCO_DBG_EVENTS"):
        # 事件版重放（非 headless）：打印 py 侧每回合的完整事件串，
        # 用于确认分歧回合里到底发生了什么机制。
        battle2 = _build_py_battle(spec)
        battle2._mcts_sim = False
        pa = _FirstAliveProxy("A", battle2)
        pb = _FirstAliveProxy("B", battle2)
        for i, sim in enumerate(sims):
            saved2 = battle2.save_mutable_state()
            for a_idx, b_idx in sim:
                act_a = action_index_to_action(battle2.player_a, a_idx)
                act_b = None if b_idx < 0 else action_index_to_action(battle2.player_b, b_idx)
                pa.action = act_a
                pb.action = act_b
                rec = battle2.execute_turn(pa, pb, fixed_action_a=act_a, fixed_action_b=act_b)
                evs = list(getattr(rec, "turn_start_events", []) or [])
                evs += list(getattr(getattr(rec, "action_a", None), "events", []) or [])
                evs += list(getattr(getattr(rec, "action_b", None), "events", []) or [])
                evs += list(getattr(rec, "turn_end_events", []) or [])
                print(f"[py events] sim={i} turn={getattr(rec, 'turn', '?')} first={getattr(rec, 'first_team', '?')}", flush=True)
                for ev in evs:
                    print("    ", ev, flush=True)
            battle2.restore_mutable_state(saved2)

    if expected is not None:
        bad = None
        for i, (ps, es) in enumerate(zip(py_digests, expected)):
            for j, (p, e) in enumerate(zip(ps, es)):
                if p != e["d"]:
                    bad = (i, j, p, e["d"])
                    break
            if bad:
                break
        if bad:
            from backend.engine.test_rust_gate import _first_diff as _fd

            print(f"⚠ 重放与 MCTS 记录不符：仿真 {bad[0]} 步 {bad[1]}"
                  "（重放路径与仿真路径不同轨迹，多半是换人策略差异，结论不可直接采信）")
            print("    ", _fd(bad[3], bad[2]))
        else:
            print("✓ 重放路径与 MCTS 记录一致")

    ok = True
    for i in range(max(len(py_digests), len(ru_digests))):
        ps = py_digests[i] if i < len(py_digests) else None
        rs = ru_digests[i] if i < len(ru_digests) else None
        if ps == rs:
            print(f"sim {i}: 一致（{len(ps or [])} 步）")
            continue
        ok = False
        print(f"sim {i}: DIFF")
        if ps is None or rs is None:
            print(f"      仅一侧有记录 py={'有' if ps else '缺'} rust={'有' if rs else '缺'}")
            continue
        if len(ps) != len(rs):
            print(f"      步数 py={len(ps)} rust={len(rs)}")
        for j, (p, r) in enumerate(zip(ps, rs)):
            if p == r:
                continue
            print(f"    step {j}: {_first_diff(p, r)}")
            if dump:
                print("      py  :", _dump(p)[:2500])
                print("      rust:", _dump(r)[:2500])
            break
    print("── 一致 ──" if ok else "── 分歧 ──")
    return ok


def main() -> None:
    spec_no = int(sys.argv[1])
    actions_spec = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else "0,0"
    dump = "--dump" in sys.argv

    pairs = [tuple(int(x) for x in part.split(",")) for part in actions_spec.split(";")]
    spec = json.loads(
        (ROOT / "native" / "gate_specs" / f"spec_{spec_no:04d}.json").read_text("utf-8")
    )
    _compare(spec, [pairs], dump=dump)


if __name__ == "__main__":
    main()
