"""resolve / formula 对拍：Rust 求值 vs Python oracle。

语料直接从 data/skills + data/traits 的真实 JSON 提取（所有 "=@..." 公式串、
所有含 "q" 的查询 dict），附加合成随机查询，逐个在多个随机 Ctx 上对拍。
"""

from __future__ import annotations

import dataclasses
import glob
import json
import random
import sys

import pytest

sys.path.insert(0, ".")

try:
    import roco_engine
except ImportError:
    pytest.skip("roco_engine 未编译（maturin develop --release）", allow_module_level=True)

from backend.vm.ctx import Ctx, EventContext
from backend.vm.resolve import _resolve_formula_string, resolve as py_resolve


def _make_ctx(seed: int) -> Ctx:
    """随机但可复现的 Ctx（覆盖所有字段类型）。"""
    rng = random.Random(seed)
    ctx = Ctx()
    for f in dataclasses.fields(Ctx):
        name = f.name
        if name == "event":
            continue
        cur = getattr(ctx, name)
        if isinstance(cur, bool):
            setattr(ctx, name, rng.random() < 0.5)
        elif isinstance(cur, int):
            setattr(ctx, name, rng.randint(-40, 320))
        elif isinstance(cur, float):
            setattr(ctx, name, round(rng.uniform(-2.0, 3.0), 3))
        elif isinstance(cur, dict):
            setattr(ctx, name, {f"k{i}": rng.randint(-3, 9) for i in range(3)})
        elif isinstance(cur, frozenset):
            setattr(ctx, name, frozenset(rng.sample(["火", "水", "电", "武", "妖"], 3)))
    ctx.event = EventContext(
        counter_succeeded=rng.random() < 0.5,
        target_fainted=rng.random() < 0.5,
        self_koed=rng.random() < 0.5,
        last_tick_abnormal="中毒",
        last_tick_target="sprite_opp",
        abnormal_applied_name="灼烧",
        abnormal_applied_target="sprite_opp",
        positive_changed_stat="atk",
        positive_changed_steps=2,
    )
    return ctx


def _norm(v):
    if isinstance(v, bool):
        return ("bool", v)
    if isinstance(v, int):
        return ("int", v)
    if isinstance(v, float):
        return ("float", v)
    if isinstance(v, str):
        return ("str", v)
    if isinstance(v, (set, frozenset, tuple, list)):
        items = sorted(v) if isinstance(v, (set, frozenset)) else [ _norm(x) for x in v ]
        return ("seq", items)
    return ("other", str(v))


def _ctx_json(ctx: Ctx) -> str:
    """Ctx → JSON（frozenset → 排序列表）。"""
    def conv(o):
        if isinstance(o, (frozenset, set)):
            return sorted(o)
        if isinstance(o, tuple):
            return list(o)
        if isinstance(o, dict):
            return {k: conv(v) for k, v in o.items()}
        if isinstance(o, list):
            return [conv(x) for x in o]
        return o
    return json.dumps(conv(dataclasses.asdict(ctx)), ensure_ascii=False)


def _norm_json(s: str):
    v = json.loads(s)
    if isinstance(v, bool):
        return ("bool", v)
    if isinstance(v, int):
        return ("int", v)
    if isinstance(v, float):
        return ("float", v)
    if isinstance(v, str):
        return ("str", v)
    if isinstance(v, list):
        return ("seq", v)
    return ("other", v)


def _walk_collect(o, formulas: set, queries: set):
    if isinstance(o, dict):
        if "q" in o and isinstance(o.get("q"), str):
            queries.add(json.dumps(o, sort_keys=True, ensure_ascii=False))
        for v in o.values():
            _walk_collect(v, formulas, queries)
    elif isinstance(o, list):
        for v in o:
            _walk_collect(v, formulas, queries)
    elif isinstance(o, str) and o.startswith("="):
        formulas.add(o)


def _collect_corpus():
    formulas: set = set()
    queries: set = set()
    paths = (glob.glob("data/skills/*.json") + glob.glob("data/traits/*.json")
             + glob.glob("backend/engine/*.json"))
    for path in paths:
        try:
            data = json.loads(open(path, encoding="utf-8").read())
        except Exception:
            continue
        _walk_collect(data, formulas, queries)
    return sorted(formulas), sorted(queries)


FORMULAS, QUERIES = _collect_corpus()


def test_corpus_nonempty() -> None:
    assert len(FORMULAS) > 20, f"公式语料过少: {len(FORMULAS)}"
    assert len(QUERIES) > 20, f"查询语料过少: {len(QUERIES)}"


@pytest.mark.parametrize('f_idx', range(len(FORMULAS)))
def test_formulas_match(f_idx: int) -> None:
    formula = FORMULAS[f_idx]
    for ctx_seed in (1, 2, 3):
        ctx = _make_ctx(ctx_seed)
        try:
            # Python oracle 收完整公式（含 '='），函数内部自行剥离
            expected = _norm(_resolve_formula_string(ctx, formula))
        except Exception:
            continue  # Python 抛错的输入不在对拍范围
        # Rust 绑定接收已剥离 '=' 的表达式
        actual = _norm_json(roco_engine.py_resolve_formula(
            _ctx_json(ctx), formula[1:]))
        assert actual == expected, f"formula={formula!r} ctx_seed={ctx_seed}"


@pytest.mark.parametrize('q_idx', range(len(QUERIES)))
def test_queries_match(q_idx: int) -> None:
    query = json.loads(QUERIES[q_idx])
    for ctx_seed in (1, 2, 3):
        ctx = _make_ctx(ctx_seed)
        try:
            expected = _norm(py_resolve(ctx, query))
        except Exception:
            continue
        actual = _norm_json(roco_engine.py_resolve(
            _ctx_json(ctx),
            json.dumps(query, ensure_ascii=False)))
        assert actual == expected, f"query={query!r} ctx_seed={ctx_seed}"


def test_synthetic_queries_match() -> None:
    """合成查询：随机 of/q/name/transform 组合。"""
    from backend.vm.ctx import ADDRESS_MAP
    rng = random.Random(4242)
    keys = list(ADDRESS_MAP.keys())
    for _ in range(400):
        of, q = rng.choice(keys)
        query = {"q": q, "of": of}
        if rng.random() < 0.5:
            query["name"] = rng.choice(["k0", "k1", "k2", "缺"])
        if rng.random() < 0.3:
            query["per"] = rng.choice([2, 3, 5])
        if rng.random() < 0.3:
            query["scale"] = rng.choice([0.5, 1.5, 2])
        if rng.random() < 0.3:
            query["offset"] = rng.choice([-1, 0.5, 3])
        if rng.random() < 0.2:
            query["default"] = rng.choice([0, 7, 2.5])
        for ctx_seed in (1, 2):
            ctx = _make_ctx(ctx_seed)
            try:
                expected = _norm(py_resolve(ctx, query))
            except Exception:
                continue
            actual = _norm_json(roco_engine.py_resolve(
                _ctx_json(ctx),
                json.dumps(query, ensure_ascii=False)))
            assert actual == expected, f"query={query!r} ctx_seed={ctx_seed}"


def test_synthetic_formulas_match() -> None:
    """合成公式：@ref 算术组合，覆盖优先级/除法/函数。"""
    rng = random.Random(777)
    refs = [
        "@self.energy", "@self.hp_ratio", "@self.atk", "@target.hp",
        "@self.effects[name=中毒].stacks", "@self.counters[k0]",
        "@opponent.team_counters[k1]", "@self.skills[element=火].count",
        "3", "2.5", "10",
    ]
    ops = ["+", "-", "*", "/", "(", ")"]
    for _ in range(300):
        parts = [rng.choice(refs)]
        for _ in range(rng.randint(1, 4)):
            parts.append(rng.choice(ops))
            parts.append(rng.choice(refs))
        expr = " ".join(parts)
        for ctx_seed in (1, 2):
            ctx = _make_ctx(ctx_seed)
            try:
                # Python oracle 收 '=' 前缀完整公式（内部会剥离首字符）
                expected = _norm(_resolve_formula_string(ctx, "=" + expr))
            except Exception:
                continue
            actual = _norm_json(roco_engine.py_resolve_formula(
                _ctx_json(ctx), expr))
            assert actual == expected, f"expr={expr!r} ctx_seed={ctx_seed}"
