"""cond DSL 对拍：Rust 求值 vs Python oracle。

语料从 data/skills + data/traits 的真实 JSON 提取（所有含 "cond" 键的
dict + 已知条件名字符串），在多个随机 Ctx 上对拍三态结果
（true/false/error，error 对应 Python 异常 → 跳过观察者）。
"""

from __future__ import annotations

import glob
import json
import sys

import pytest

sys.path.insert(0, ".")

try:
    import roco_engine
except ImportError:
    pytest.skip("roco_engine 未编译（maturin develop --release）", allow_module_level=True)

from backend.vm.cond import eval_one as py_eval_one, infer_triggers as py_infer_triggers
from backend.engine.test_native_resolve import _make_ctx, _ctx_json


def _walk_conds(o, conds: set):
    if isinstance(o, dict):
        c = o.get("cond")
        if isinstance(c, str):
            # 规范条件 dict：{"cond": "hp_below", "ratio": 0.5, ...}
            conds.add(json.dumps(o, sort_keys=True, ensure_ascii=False))
        elif isinstance(c, dict) and isinstance(c.get("cond"), str):
            # observer 效果内嵌条件：{"op": "observer", "cond": {...}}
            conds.add(json.dumps(c, sort_keys=True, ensure_ascii=False))
        for v in o.values():
            _walk_conds(v, conds)
    elif isinstance(o, list):
        for v in o:
            _walk_conds(v, conds)


def _collect_conds():
    conds: set = set()
    for path in glob.glob("data/skills/*.json") + glob.glob("data/traits/*.json"):
        try:
            data = json.loads(open(path, encoding="utf-8").read())
        except Exception:
            continue
        _walk_conds(data, conds)
    return sorted(conds)


CONDS = _collect_conds()

# 已知条件名（作为裸字符串条件出现的形态）
KNOWN_COND_STRS = [
    "turn_start", "always", "turn_end", "counter_succeeded", "self_was_countered",
    "charged", "is_charging", "burst", "first_action", "on_ko", "opp_switched",
    "is_first", "is_second", "devotion_triggered", "damage_restraint", "不存在的条件",
]


def test_cond_corpus_nonempty() -> None:
    assert len(CONDS) > 20, f"条件语料过少: {len(CONDS)}"


@pytest.mark.parametrize('c_idx', range(len(CONDS)))
def test_conds_match(c_idx: int) -> None:
    cond = json.loads(CONDS[c_idx])
    for ctx_seed in (1, 2, 3):
        ctx = _make_ctx(ctx_seed)
        try:
            expected = "true" if py_eval_one(ctx, cond) else "false"
        except Exception:
            expected = "error"
        actual = roco_engine.py_eval_cond(_ctx_json(ctx), CONDS[c_idx])
        assert actual == expected, f"cond={CONDS[c_idx]} ctx_seed={ctx_seed}"


@pytest.mark.parametrize('s', KNOWN_COND_STRS)
def test_string_conds_match(s: str) -> None:
    for ctx_seed in (1, 2):
        ctx = _make_ctx(ctx_seed)
        try:
            expected = "true" if py_eval_one(ctx, s) else "false"
        except Exception:
            expected = "error"
        actual = roco_engine.py_eval_cond(_ctx_json(ctx), json.dumps(s, ensure_ascii=False))
        assert actual == expected, f"cond={s!r} ctx_seed={ctx_seed}"


@pytest.mark.parametrize('c_idx', range(0, len(CONDS), 3))
def test_infer_triggers_match(c_idx: int) -> None:
    cond = json.loads(CONDS[c_idx])
    expected = sorted(py_infer_triggers(cond))
    actual = roco_engine.py_infer_triggers(CONDS[c_idx])
    assert actual == expected, f"cond={CONDS[c_idx]}"
