# backend/vm/tests/test_ir_skill.py
import pickle

import pytest

from backend.vm.ir_skill import (
    AbnormalOp,
    AndCond,
    CondExpr,
    EscapeOp,
    HitOp,
    MarkOp,
    ModOp,
    NotCond,
    ReturnOp,
    SkillIROp,
    WeatherOp,
    WhenBlock,
    WhenBranch,
)
from backend.vm.ir_values import Literal, Query


class TestSkillCondition:
    def test_cond_expr(self):
        c = CondExpr(cond="on_ko", params={})
        assert c.cond == "on_ko"

    def test_and_cond(self):
        c = AndCond(conditions=(
            CondExpr(cond="is_first"),
            CondExpr(cond="opp_is_attack"),
        ))
        assert len(c.conditions) == 2

    def test_not_cond(self):
        c = NotCond(condition=CondExpr(cond="hp_below", params={"ratio": 0.5}))
        assert isinstance(c.condition, CondExpr)

    def test_cond_frozen(self):
        c = CondExpr(cond="on_ko")
        with pytest.raises(Exception):
            c.cond = "other"


class TestWhenBlock:
    def test_when_block_basic(self):
        wb = WhenBlock(
            cond=CondExpr(cond="on_ko"),
            then=(ModOp(target="sprite_self", stat="energy", value=Literal(6)),),
        )
        assert isinstance(wb.cond, CondExpr)
        assert len(wb.then) == 1

    def test_when_block_with_elif(self):
        wb = WhenBlock(
            cond=CondExpr(cond="on_ko"),
            then=(ModOp(target="sprite_self", stat="energy", value=Literal(6)),),
            elif_=(WhenBranch(
                cond=CondExpr(cond="counter_succeeded"),
                then=(ModOp(target="sprite_self", stat="atk", value=Literal(1)),),
            ),),
        )
        assert len(wb.elif_) == 1

    def test_when_block_hashable(self):
        wb = WhenBlock(
            cond=CondExpr(cond="on_ko"),
            then=(ModOp(target="sprite_self", stat="energy", value=Literal(6)),),
        )
        d = {wb: "test"}
        assert d[wb] == "test"


class TestModOp:
    def test_mod_basic(self):
        op = ModOp(target="sprite_self", stat="power", value=Literal(20))
        assert op.stat == "power"
        assert op.value == Literal(20)

    def test_mod_with_query_value(self):
        op = ModOp(target="sprite_self", stat="power",
                   value=Query(field="energy_self", scale=10))
        assert isinstance(op.value, Query)

    def test_mod_frozen(self):
        op = ModOp(target="sprite_self", stat="atk", value=Literal(1))
        with pytest.raises(Exception):
            op.stat = "def"


class TestHitOp:
    def test_hit_basic(self):
        op = HitOp(power=Literal(100), type="物攻")
        assert op.power == Literal(100)
        assert op.type == "物攻"

    def test_hit_with_combo(self):
        op = HitOp(power=Query(field="power_self"), type="魔攻", combo=3)
        assert op.combo == 3


class TestOtherOps:
    def test_mark_op(self):
        op = MarkOp(target="team_opp", name="中毒印记", stacks=2)
        assert op.name == "中毒印记"
        assert op.stacks == 2

    def test_abnormal_op(self):
        op = AbnormalOp(target="sprite_opp", name="灼烧", stacks=1)
        assert op.name == "灼烧"

    def test_weather_op(self):
        op = WeatherOp(weather="rain", turns=8)
        assert op.weather == "rain"

    def test_escape_op(self):
        op = EscapeOp(target="sprite_self")
        assert op.target == "sprite_self"

    def test_return_op(self):
        op = ReturnOp(target="sprite_opp")
        assert op.target == "sprite_opp"


class TestIRPickle:
    def test_skill_ir_pickleable(self):
        ops = [
            ModOp(target="sprite_self", stat="atk", value=Literal(1)),
            HitOp(power=Literal(100), type="物攻"),
        ]
        restored = pickle.loads(pickle.dumps(ops))
        assert restored[0].stat == "atk"
        assert restored[1].power == Literal(100)


class TestSkillIROpUnion:
    def test_type_check(self):
        op: SkillIROp = ModOp(target="sprite_self", stat="atk", value=Literal(1))
        assert isinstance(op, ModOp)
        op2: SkillIROp = HitOp(power=Literal(100), type="物攻")
        assert isinstance(op2, HitOp)
