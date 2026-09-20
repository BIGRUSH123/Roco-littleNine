"""效果执行器对拍：Rust 解释器 vs Python oracle（vm.executor.execute）。

语料 = 全部真实技能 JSON 的 effects 数组 + 特性 JSON 的 then 列表。
在多个随机 Ctx 上执行，比较产出的 Mutation 流。

比对规范：Mutation 的标量字段逐位比较；嵌套 IR 载荷（then/effects/
cond/skill_where/listen/elif_）归一为占位符（嵌套行为由整局对拍门覆盖）。
"""

from __future__ import annotations

import dataclasses
import glob
import json
import sys

import pytest

sys.path.insert(0, ".")

try:
    import roco_engine
except ImportError:
    pytest.skip("roco_engine 未编译（maturin develop --release）", allow_module_level=True)

from backend.vm.executor import execute as py_execute
from backend.engine.test_native_resolve import _make_ctx, _ctx_json

PAYLOAD_KEYS = {"then", "effects", "cond", "elif_", "skill_where", "listen"}

# Rust serde snake_case tag → Python dataclass 类名
RUST_TO_PY = {
    "stat_change": "StatChange",
    "modifier_injection": "ModifierInjection",
    "damage": "Damage",
    "heal": "Heal",
    "energy_change": "EnergyChange",
    "mark_change": "MarkChange",
    "abnormal_change": "AbnormalChange",
    "weather_set": "WeatherSet",
    "dispel": "Dispel",
    "steal": "Steal",
    "tick": "Tick",
    "double": "Double",
    "effect_delta": "EffectDelta",
    "charge": "Charge",
    "escape": "Escape",
    "return": "Return",
    "lock": "Lock",
    "interrupt": "Interrupt",
    "exchange": "Exchange",
    "reset": "Reset",
    "redirect": "Redirect",
    "replay": "Replay",
    "borrow": "Borrow",
    "counter_register": "CounterRegister",
    "burst_grant": "BurstGrant",
    "team_counter_delta": "TeamCounterDelta",
    "lives_delta": "LivesDelta",
    "schedule_entry": "ScheduleEntry",
    "inherit_effects": "InheritEffectsMutation",
    "transform": "TransformMutation",
    "trait_interaction": "TraitInteractionMutation",
    "gain_skills": "GainSkillsMutation",
}


def _norm_val(v):
    """归一化为可比较的 ('tag', value) 形式（区分 int/float/bool/str）。"""
    if isinstance(v, bool):
        return ("bool", v)
    if isinstance(v, int):
        return ("int", v)
    if isinstance(v, float):
        return ("float", v)
    if isinstance(v, str):
        return ("str", v)
    if v is None:
        return ("null",)
    if isinstance(v, (set, frozenset)):
        return ("set", sorted(str(x) for x in v))
    if isinstance(v, (list, tuple)):
        return ("list", [_norm_val(x) for x in v])
    if isinstance(v, dict):
        return ("dict", {k: _norm_val(v[k]) for k in sorted(v, key=str)})
    if dataclasses.is_dataclass(v):
        return _norm_val(dataclasses.asdict(v))
    return ("other", str(v))


def _norm_mutation(m) -> tuple:
    name = type(m).__name__
    d = dataclasses.asdict(m)
    fields = {}
    for k, v in d.items():
        if k in PAYLOAD_KEYS:
            fields[k] = "<payload>" if v else "<empty>"
        else:
            fields[k] = _norm_val(v)
    return (name, fields)


def _norm_rust(json_str: str) -> tuple:
    d = json.loads(json_str)
    # Rust serde tag 形如 {"op": "stat_change", ...}（snake_case 类型名）
    op = RUST_TO_PY[d.pop("op")]
    # 字段名对齐 Python dataclass（Replay.from_ 在 Rust serde 里叫 "from"）
    if op == "Replay" and "from" in d:
        d["from_"] = d.pop("from")
    fields = {}
    for k, v in d.items():
        if k in PAYLOAD_KEYS:
            fields[k] = "<payload>" if v else "<empty>"
        else:
            fields[k] = _norm_val(v)
    return (op, fields)


def _corpus():
    """全部真实效果树：(标签, effects 数组)。"""
    cases = []
    for path in sorted(glob.glob("data/skills/*.json")):
        try:
            data = json.loads(open(path, encoding="utf-8").read())
        except Exception:
            continue
        effects = data.get("effects")
        if isinstance(effects, list) and effects:
            cases.append((path, effects))
    # 特性 JSON 里的 then 列表（observer/defer 等）
    for path in sorted(glob.glob("data/traits/*.json")):
        try:
            data = json.loads(open(path, encoding="utf-8").read())
        except Exception:
            continue

        def walk(o):
            if isinstance(o, dict):
                then = o.get("then")
                if isinstance(then, list) and then:
                    cases.append((path + ":then", then))
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)

        walk(data)
    return cases


CASES = _corpus()


def test_corpus_nonempty() -> None:
    assert len(CASES) > 300, f"效果语料过少: {len(CASES)}"


@pytest.mark.parametrize('c_idx', range(len(CASES)))
def test_effects_match(c_idx: int) -> None:
    label, effects = CASES[c_idx]
    effects_json = json.dumps(effects, ensure_ascii=False)
    for ctx_seed in (1, 2):
        ctx = _make_ctx(ctx_seed)
        try:
            expected = [_norm_mutation(m) for m in py_execute(ctx, json.loads(effects_json))]
        except Exception:
            continue  # Python 抛错（坏效果等）不在对拍范围
        actual = [_norm_rust(s) for s in roco_engine.py_execute(_ctx_json(ctx), effects_json)]
        assert actual == expected, f"{label} ctx_seed={ctx_seed}\nPY : {expected}\nRUST: {actual}"
