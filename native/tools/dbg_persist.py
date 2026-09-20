"""dbg_persist — 追踪 py 的 skill.{name}.{stat} 持久键写入来源。

用法：env\\python.exe native/tools/dbg_persist.py <spec> <turn>
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine import replayer as R  # noqa: E402
from backend.engine.test_rust_gate import battle_from_spec  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402

# 包装 _apply_to_all_skills / _apply_to_matching_skills，打印 sprite 与技能名
orig_all = R._apply_to_all_skills


def wrap_all(sprite, m, replayer=None):
    out = orig_all(sprite, m, replayer)
    print(f"[all] sprite={sprite.name} stat={m.stat} d={m.value} scope={m.scope} "
          f"src={m.source} skills={[b.name for b in (sprite.skills or [])]} → {out}",
          flush=True)
    return out


R._apply_to_all_skills = wrap_all

orig_match = R._apply_to_matching_skills


def wrap_match(sprite, m, mark_energy_mod=0, replayer=None):
    out = orig_match(sprite, m, mark_energy_mod=mark_energy_mod, replayer=replayer)
    print(f"[match] sprite={sprite.name} stat={m.stat} d={m.value} scope={m.scope} "
          f"src={m.source} target={m.target} → {out}", flush=True)
    return out


R._apply_to_matching_skills = wrap_match

# 追踪所有 energy_cost / permanent 修改的 replayer 上下文
orig_apply_mod = R.JournalReplayer._apply_modifier


def wrap_apply_mod(self, m):
    if getattr(m, "stat", "") == "energy_cost":
        try:
            names = (f"self={self.self.name if self.self else None} "
                     f"opp={self.opp.name if self.opp else None} "
                     f"self_skill={getattr(getattr(self, '_self_skill', None), 'name', None)}")
        except Exception:  # noqa: BLE001
            names = "?"
        print(f"[mod] {names} target={m.target} d={m.value} scope={m.scope} src={m.source}",
              flush=True)
    return orig_apply_mod(self, m)


R.JournalReplayer._apply_modifier = wrap_apply_mod
# _DISPATCH 在类创建后显式填充 → 直接替换表项
for _k, _v in list(R.JournalReplayer._DISPATCH.items()):
    if getattr(_k, "__name__", "") == "ModifierInjection":
        R.JournalReplayer._DISPATCH[_k] = wrap_apply_mod

# 追踪 post 事件触发（含 observer source）
from backend.engine.battle import BattleVMEngine as VmEngine  # noqa: E402

orig_fire_post = VmEngine._fire_post_event
import backend.engine.battle as _eb  # noqa: E402


def wrap_fire_post(self, trigger, ctx, replayer):
    names = (f"trigger={trigger} self={getattr(replayer.self, 'name', None)} "
             f"opp={getattr(replayer.opp, 'name', None)} "
             f"self_skill={getattr(getattr(replayer, '_self_skill', None), 'name', None)}")
    before = dict(replayer.self._modifiers) if replayer.self else {}
    print(f"[fire-in]  {names}", flush=True)
    ev = orig_fire_post(self, trigger, ctx, replayer)
    if replayer.self:
        added = {k: v for k, v in replayer.self._modifiers.items() if k not in before}
        if added:
            print(f"[fire-out] {names} added={added}", flush=True)
    return ev


# 追踪 replay 调用中带 skill_off_0 的 journal
orig_replay = R.JournalReplayer.replay


def wrap_replay(self, journal):
    hits = [m for m in journal if getattr(m, "target", "") == "skill_off_0"]
    if hits:
        print(f"[replay] self={getattr(self.self, 'name', None)} "
              f"self_skill={getattr(getattr(self, '_self_skill', None), 'name', None)} "
              f"hits={[(m.stat, m.value, m.scope) for m in hits]}", flush=True)
    return orig_replay(self, journal)


R.JournalReplayer.replay = wrap_replay


_eb.BattleVMEngine._fire_post_event = wrap_fire_post

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
target = int(sys.argv[2])
random.seed(spec["seed"] + 1)
battle = battle_from_spec(spec)
a = RuleAgent("A", battle.player_a)
b = RuleAgent("B", battle.player_b)
battle.player_a.active_index = a.choose_lead(battle)
battle.player_b.active_index = b.choose_lead(battle)
battle._invalidate_ctx_team_cache()
while not battle.is_finished and battle.turn < target:
    battle.execute_turn(a, b)
print("=== done, modifiers ===")
for label, player in (("A", battle.player_a), ("B", battle.player_b)):
    for i, s in enumerate(player.team):
        keys = {k: v for k, v in s._modifiers.items() if k.startswith("skill.")}
        if keys:
            print(f"{label}[{i}] {s.name}: {keys} | skills={[b.name for b in s.skills]}")
