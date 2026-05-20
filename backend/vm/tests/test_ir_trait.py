import pickle

import pytest

from backend.vm.ir_trait import (
    ActionModifierOp,
    AndCond,
    BattleSkillMutOp,
    CompiledTrait,
    FnCond,
    MutateEffectOp,
    PathCond,
    TraitSpecialEffect,
    TraitStatEffect,
    TraitTrigger,
    UseModifierOp,
)
from backend.vm.ir_values import Literal, RefExpr


class TestTraitCondition:
    def test_path_cond(self):
        c = PathCond(path=["self", "energy"], op="gt", value=Literal(5))
        assert c.path == ["self", "energy"]
        assert c.op == "gt"

    def test_fn_cond(self):
        c = FnCond(name="is_weekend")
        assert c.name == "is_weekend"

    def test_and_cond(self):
        c = AndCond(conditions=(
            PathCond(path=["self", "hp_ratio"], op="lt", value=Literal(0.5)),
            PathCond(path=["battle", "weather"], op="eq", value=Literal("rain")),
        ))
        assert len(c.conditions) == 2

    def test_cond_frozen(self):
        c = PathCond(path=["self", "energy"], op="gt", value=Literal(5))
        with pytest.raises(Exception):
            c.path = ["other"]


class TestMutateEffectOp:
    def test_mutate_basic(self):
        op = MutateEffectOp(
            target="target",
            filter={"is_stat": True, "steps<0": True},
            delta_steps=-3,
        )
        assert op.delta_steps == -3

    def test_mutate_frozen(self):
        op = MutateEffectOp(target="self", filter={}, delta_steps=0)
        with pytest.raises(Exception):
            op.target = "other"


class TestBattleSkillMutOp:
    def test_battleskill_mut(self):
        op = BattleSkillMutOp(
            filter={"is_attack": True},
            field="next_attack_mult",
            value=Literal(2.0),
        )
        assert op.field == "next_attack_mult"

    def test_battleskill_mut_with_ref(self):
        op = BattleSkillMutOp(
            filter={"is_attack": True},
            field="next_attack_mult",
            value=RefExpr(root="team_counters", path=["counter_success"], multiplier=0.5),
        )
        assert isinstance(op.value, RefExpr)


class TestUseModifierOp:
    def test_use_modifier(self):
        op = UseModifierOp(
            key="damage_mult",
            value=Literal(0.0),
            op="set",
        )
        assert op.key == "damage_mult"


class TestActionModifierOp:
    def test_action_modifier(self):
        op = ActionModifierOp(action="forbid_skill", slot=0)
        assert op.action == "forbid_skill"


class TestTraitEffects:
    def test_stat_effect(self):
        e = TraitStatEffect(
            target="self", stat="atk",
            steps=RefExpr(root="player", path=["bug_count"], multiplier=1.5),
            source="虫群突袭",
        )
        assert e.stat == "atk"
        assert isinstance(e.steps, RefExpr)

    def test_special_effect_heal(self):
        e = TraitSpecialEffect(
            name="heal", value=Literal(0.5), target="self",
        )
        assert e.name == "heal"


class TestTraitTrigger:
    def test_trigger_basic(self):
        t = TraitTrigger(
            on="entry",
            effects=(
                TraitStatEffect(target="self", stat="atk", steps=Literal(2), source="test"),
            ),
        )
        assert t.on == "entry"
        assert t.effects_mode == "accumulate"

    def test_trigger_with_condition(self):
        t = TraitTrigger(
            on="entry",
            condition=PathCond(path=["self", "hp_ratio"], op="lt", value=Literal(0.5)),
            effects=(TraitStatEffect(target="self", stat="def", steps=Literal(2), source="test"),),
            effects_mode="replace",
        )
        assert t.effects_mode == "replace"

    def test_trigger_with_counter(self):
        t = TraitTrigger(
            on="modifier",
            counter="hits_taken",
            counter_op="inc",
            counter_trigger={"op": "gte", "value": 3},
            counter_reset=True,
            effects=(TraitSpecialEffect(name="gain_energy", amount=Literal(5)),),
        )
        assert t.counter == "hits_taken"

    def test_trigger_frozen(self):
        t = TraitTrigger(on="entry")
        with pytest.raises(Exception):
            t.on = "leave"


class TestCompiledTrait:
    def test_compiled_trait(self):
        ct = CompiledTrait(
            id=20029,
            name="圣火骑士",
            description="应对成功后，下次攻击威力翻倍。",
            triggers=(
                TraitTrigger(
                    on="counter_success",
                    battleskill_mut=(
                        BattleSkillMutOp(
                            filter={"is_attack": True},
                            field="next_attack_mult",
                            value=Literal(2.0),
                        ),
                    ),
                ),
            ),
        )
        assert ct.id == 20029
        assert len(ct.triggers) == 1

    def test_compiled_trait_pickleable(self):
        ct = CompiledTrait(id=1, name="test", description="", triggers=())
        restored = pickle.loads(pickle.dumps(ct))
        assert restored.id == 1
