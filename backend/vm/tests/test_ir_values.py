import pickle

import pytest

from backend.vm.ctx import Ctx
from backend.vm.ir_values import Literal, Query, RefExpr
from backend.vm.resolve import resolve


def test_ir_values_are_slots_backed_and_pickleable():
    values = (
        Literal(1),
        Query(field="atk_self"),
        RefExpr(root="self", path=["energy"]),
    )

    for value in values:
        assert not hasattr(value, "__dict__")
        assert pickle.loads(pickle.dumps(value)) == value


class TestLiteral:
    def test_literal_int(self):
        v = Literal(42)
        assert v.value == 42
        assert isinstance(v.value, int)

    def test_literal_str(self):
        v = Literal("物攻")
        assert v.value == "物攻"

    def test_literal_bool(self):
        v = Literal(True)
        assert v.value is True

    def test_literal_frozen(self):
        v = Literal(10)
        with pytest.raises(Exception):
            v.value = 20

    def test_literal_hashable(self):
        d = {Literal(1): "one", Literal(2): "two"}
        assert d[Literal(1)] == "one"

    def test_literal_pickleable(self):
        v = Literal(3.14)
        restored = pickle.loads(pickle.dumps(v))
        assert restored.value == 3.14

    def test_literal_wrapped_query_resolves(self):
        ctx = Ctx(abnormal_stacks_opp={"中毒": 3})
        v = Literal({"q": "abnormal_stacks", "of": "sprite_opp", "name": "中毒", "scale": -1})
        assert resolve(ctx, v) == -3


class TestQuery:
    def test_query_basic(self):
        q = Query(field="hp_self_ratio")
        assert q.field == "hp_self_ratio"
        assert q.scale == 1.0
        assert q.offset == 0

    def test_query_with_scale(self):
        q = Query(field="energy_opp", scale=0.5)
        assert q.scale == 0.5

    def test_query_with_name(self):
        q = Query(field="abnormal_stacks_self", name="灼烧")
        assert q.name == "灼烧"

    def test_query_frozen(self):
        q = Query(field="atk_self")
        with pytest.raises(Exception):
            q.field = "other"

    def test_query_hashable(self):
        q1 = Query(field="hp_self")
        q2 = Query(field="hp_self")
        assert hash(q1) == hash(q2)

    def test_energy_cost_sum_query_uses_skill_type_subkey(self):
        ctx = Ctx(energy_cost_sum_self={"迅捷": 4, "火": 2})
        q = Query(field="energy_cost_sum_self", name="迅捷", scale=0.5)
        assert resolve(ctx, q) == 2.0

    def test_dict_register_without_name_returns_zero(self):
        ctx = Ctx(abnormal_stacks_opp={"中毒": 3})
        q = Query(field="abnormal_stacks_opp", name=None)
        assert resolve(ctx, q) == 0


class TestRefExpr:
    def test_refexpr_basic(self):
        r = RefExpr(root="self", path=["energy"])
        assert r.root == "self"
        assert r.path == ["energy"]

    def test_refexpr_with_multiplier(self):
        r = RefExpr(root="player", path=["fainted_count"], multiplier=3)
        assert r.multiplier == 3

    def test_refexpr_frozen(self):
        r = RefExpr(root="self", path=["energy"])
        with pytest.raises(Exception):
            r.path = ["other"]
