"""Tests for Pass 1: TraitParsePass."""
import pytest

from backend.vm.compiler.context import CompilerContext
from backend.vm.compiler.passes.trait_parse import TraitParsePass
from backend.vm.ir_trait import (
    AndCond,
    BattleSkillMutOp,
    FnCond,
    NotCond,
    OrCond,
    PathCond,
    RemoveEffectOp,
    TraitAbnormalEffect,
    TraitMarkEffect,
    TraitSpecialEffect,
    TraitStatEffect,
    TraitTrigger,
    TraitWeatherEffect,
)
from backend.vm.ir_values import Literal, RefExpr


@pytest.fixture
def parse_pass():
    return TraitParsePass()


def _make_ctx(data: dict) -> CompilerContext:
    return CompilerContext(raw=data, meta={"name": data.get("name", ""), "id": data.get("id", 0)})


class TestParseConditions:
    """Condition parsing tests."""

    def test_path_cond(self, parse_pass):
        """Parse: {"path": "self.energy", "op": "gt", "value": 0}"""
        cond = parse_pass._parse_condition({
            "path": "self.energy", "op": "gt", "value": 0,
        })
        assert isinstance(cond, PathCond)
        assert cond.path == ["self", "energy"]
        assert cond.op == "gt"
        assert isinstance(cond.value, Literal)
        assert cond.value.value == 0

    def test_path_cond_ref_value(self, parse_pass):
        """Parse: {"path": "skill.element", "op": "in", "value": "=@defender_skill_elements"}"""
        cond = parse_pass._parse_condition({
            "path": "skill.element",
            "op": "in",
            "value": "=@defender_skill_elements",
        })
        assert isinstance(cond, PathCond)
        assert cond.path == ["skill", "element"]
        assert cond.op == "in"
        assert isinstance(cond.value, RefExpr)
        assert cond.value.root == "defender_skill_elements"
        assert cond.value.path == []

    def test_and_cond(self, parse_pass):
        """Parse: {"kind": "and", "conditions": [...]}"""
        cond = parse_pass._parse_condition({
            "kind": "and",
            "conditions": [
                {"path": "skill.is_attack", "op": "eq", "value": True},
                {"path": "skill.element", "op": "neq", "value": "光"},
            ],
        })
        assert isinstance(cond, AndCond)
        assert len(cond.conditions) == 2
        assert isinstance(cond.conditions[0], PathCond)
        assert cond.conditions[0].value.value is True

    def test_or_cond(self, parse_pass):
        cond = parse_pass._parse_condition({
            "kind": "or",
            "conditions": [
                {"path": "a", "op": "eq", "value": 1},
                {"path": "b", "op": "eq", "value": 2},
            ],
        })
        assert isinstance(cond, OrCond)
        assert len(cond.conditions) == 2

    def test_not_cond(self, parse_pass):
        cond = parse_pass._parse_condition({
            "kind": "not",
            "condition": {"path": "self.is_fainted", "op": "eq", "value": True},
        })
        assert isinstance(cond, NotCond)
        assert isinstance(cond.condition, PathCond)

    def test_fn_cond(self, parse_pass):
        cond = parse_pass._parse_condition({
            "kind": "fn",
            "name": "is_weekend",
        })
        assert isinstance(cond, FnCond)
        assert cond.name == "is_weekend"

    def test_none_condition(self, parse_pass):
        assert parse_pass._parse_condition(None) is None
        assert parse_pass._parse_condition({}) is None


class TestParseEffects:
    """Effect parsing tests."""

    def test_stat_effect_literal(self, parse_pass):
        """Stat effect with literal steps."""
        eff = parse_pass._parse_effect({
            "kind": "stat",
            "stat": "atk",
            "steps": 5,
            "scope": "battlefield",
            "source": "恶魔的晚宴",
        })
        assert isinstance(eff, TraitStatEffect)
        assert eff.stat == "atk"
        assert isinstance(eff.steps, Literal)
        assert eff.steps.value == 5
        assert eff.scope == "battlefield"
        assert eff.source == "恶魔的晚宴"

    def test_stat_effect_ref_expr(self, parse_pass):
        """Stat effect with RefExpr steps."""
        eff = parse_pass._parse_effect({
            "kind": "stat",
            "stat": "atk",
            "steps": "=@player_fainted_count * 3",
            "scope": "battlefield",
            "source": "悼亡",
        })
        assert isinstance(eff, TraitStatEffect)
        assert eff.stat == "atk"
        assert isinstance(eff.steps, RefExpr)
        assert eff.steps.root == "player_fainted_count"
        assert eff.steps.multiplier == 3.0

    def test_stat_effect_ref_expr_compound(self, parse_pass):
        """Stat effect with compound ref expression (kept as Literal string)."""
        eff = parse_pass._parse_effect({
            "kind": "stat",
            "stat": "atk",
            "steps": "=@player_fainted_count * 3 + opponent_fainted_count * 3",
            "scope": "battlefield",
            "source": "悼亡",
        })
        assert isinstance(eff, TraitStatEffect)
        # Compound multi-term expression should be kept as Literal string
        assert isinstance(eff.steps, Literal)

    def test_abnormal_effect(self, parse_pass):
        eff = parse_pass._parse_effect({
            "kind": "abnormal",
            "name": "中毒",
            "stacks": 4,
            "target": "target",
            "source": "毒腺",
        })
        assert isinstance(eff, TraitAbnormalEffect)
        assert eff.name == "中毒"
        assert isinstance(eff.stacks, Literal)
        assert eff.stacks.value == 4
        assert eff.target == "target"

    def test_mark_effect(self, parse_pass):
        eff = parse_pass._parse_effect({
            "kind": "mark",
            "name": "星陨印记",
            "stacks": 3,
            "mark_target": "opp_team",
        })
        assert isinstance(eff, TraitMarkEffect)
        assert eff.name == "星陨印记"
        assert eff.stacks == 3
        assert eff.mark_target == "opp_team"

    def test_weather_effect(self, parse_pass):
        eff = parse_pass._parse_effect({
            "kind": "weather",
            "weather": "rain",
            "turns": 8,
        })
        assert isinstance(eff, TraitWeatherEffect)
        assert eff.weather == "rain"
        assert eff.turns == 8

    def test_special_effect_energy(self, parse_pass):
        eff = parse_pass._parse_effect({
            "kind": "special",
            "name": "gain_energy",
            "amount": 10,
        })
        assert isinstance(eff, TraitSpecialEffect)
        assert eff.name == "gain_energy"
        assert isinstance(eff.amount, Literal)
        assert eff.amount.value == 10

    def test_special_effect_ref_amount(self, parse_pass):
        eff = parse_pass._parse_effect({
            "kind": "special",
            "name": "gain_energy",
            "amount": "=@team_counters[counter_success] * 5",
        })
        assert isinstance(eff, TraitSpecialEffect)
        assert eff.name == "gain_energy"
        # Compound type - may be kept as Literal
        assert eff.amount is not None

    def test_remove_effect_op(self, parse_pass):
        eff = parse_pass._parse_effect({
            "kind": "remove_effect",
            "source": "冰封",
            "target": "opponent_active",
        })
        assert isinstance(eff, RemoveEffectOp)
        assert eff.source == "冰封"
        assert eff.target == "opponent_active"

    def test_state_effect(self, parse_pass):
        """Pending effects with kind=state."""
        eff = parse_pass._parse_effect({
            "kind": "state",
            "name": "木桶状态",
            "scope": "battlefield",
            "source": "木桶戏法",
        })
        assert isinstance(eff, TraitSpecialEffect)
        assert eff.name == "木桶状态"


class TestParseRefExpr:
    """RefExpr parsing tests."""

    def test_simple_ref(self, parse_pass):
        result = parse_pass._parse_ref_expr("=@player_fainted_count")
        assert isinstance(result, RefExpr)
        assert result.root == "player_fainted_count"
        assert result.path == []
        assert result.multiplier == 1.0
        assert result.offset == 0

    def test_ref_with_multiplier(self, parse_pass):
        result = parse_pass._parse_ref_expr("=@player_fainted_count * 3")
        assert isinstance(result, RefExpr)
        assert result.root == "player_fainted_count"
        assert result.multiplier == 3.0
        assert result.offset == 0

    def test_ref_with_multiplier_and_offset(self, parse_pass):
        result = parse_pass._parse_ref_expr("=@player_fainted_count * 3 + 5")
        assert isinstance(result, RefExpr)
        assert result.root == "player_fainted_count"
        assert result.multiplier == 3.0
        assert result.offset == 5

    def test_ref_with_negative_multiplier(self, parse_pass):
        result = parse_pass._parse_ref_expr("=@player_moe_stacks * -1")
        assert isinstance(result, RefExpr)
        assert result.root == "player_moe_stacks"
        assert result.multiplier == -1.0

    def test_ref_with_dotted_path(self, parse_pass):
        result = parse_pass._parse_ref_expr("=@self.energy")
        assert isinstance(result, RefExpr)
        assert result.root == "self"
        assert result.path == ["energy"]

    def test_ref_with_bracket_path(self, parse_pass):
        result = parse_pass._parse_ref_expr("=@target.effects[name=冻结].stacks")
        assert isinstance(result, RefExpr)
        assert result.root == "target"
        assert result.path == ["effects[name=冻结]", "stacks"]

    def test_ref_with_bracket_root(self, parse_pass):
        result = parse_pass._parse_ref_expr("=@team_counters[element:武]")
        assert isinstance(result, RefExpr)
        assert result.root == "team_counters"
        assert result.path == ["element:武"]

    def test_ref_with_skill_bracket_path(self, parse_pass):
        result = parse_pass._parse_ref_expr("=@self.skills[element=毒].count")
        assert isinstance(result, RefExpr)
        assert result.root == "self"
        assert result.path == ["skills[element=毒]", "count"]

    def test_compound_expression_kept_as_literal(self, parse_pass):
        result = parse_pass._parse_ref_expr("=@player_fainted_count * 3 + opponent_fainted_count * 3")
        assert isinstance(result, Literal)
        assert "=@player_fainted_count" in result.value

    def test_non_ref_string_kept_as_literal(self, parse_pass):
        result = parse_pass._parse_ref_expr("plain_string")
        assert isinstance(result, Literal)
        assert result.value == "plain_string"


class TestParseTriggers:
    """Full trigger parsing tests."""

    def test_simple_counter_success_trigger(self, parse_pass):
        """圣火骑士: counter_success with battleskill_mut."""
        data = {
            "id": 20029,
            "name": "圣火骑士",
            "description": "应对成功后，下次攻击威力翻倍。",
            "triggers": [{
                "on": "counter_success",
                "battleskill_mut": [{
                    "filter": {"is_attack": True},
                    "field": "next_attack_mult",
                    "op": "set",
                    "value": 2.0,
                }],
            }],
        }
        ctx = _make_ctx(data)
        result = parse_pass.apply(ctx)
        assert len(result.errors) == 0
        assert len(result.ir) == 1
        trigger = result.ir[0]
        assert isinstance(trigger, TraitTrigger)
        assert trigger.on == "counter_success"
        assert len(trigger.battleskill_mut) == 1
        mut = trigger.battleskill_mut[0]
        assert isinstance(mut, BattleSkillMutOp)
        assert mut.field == "next_attack_mult"
        assert mut.op == "set"
        assert isinstance(mut.value, Literal)
        assert mut.value.value == 2.0
        assert mut.filter == {"is_attack": True}

    def test_entry_stat_effect_with_ref(self, parse_pass):
        """悼亡: entry with stat effect using RefExpr."""
        data = {
            "id": 20052,
            "name": "悼亡",
            "description": "双方队伍每有1只力竭精灵，双攻+30%。",
            "triggers": [{
                "on": "entry",
                "effects_mode": "replace",
                "effects": [
                    {
                        "kind": "stat",
                        "stat": "atk",
                        "steps": "=@player_fainted_count * 3",
                        "scope": "battlefield",
                        "source": "悼亡",
                    },
                ],
            }],
        }
        ctx = _make_ctx(data)
        result = parse_pass.apply(ctx)
        assert len(result.errors) == 0
        trigger = result.ir[0]
        assert trigger.on == "entry"
        assert trigger.effects_mode == "replace"
        effect = trigger.effects[0]
        assert isinstance(effect, TraitStatEffect)
        assert isinstance(effect.steps, RefExpr)
        assert effect.steps.root == "player_fainted_count"
        assert effect.steps.multiplier == 3.0

    def test_defend_with_condition_and_use_modifiers(self, parse_pass):
        """完全偏振: defend with PathCond and use_modifiers."""
        data = {
            "id": 20042,
            "name": "完全偏振",
            "description": "抵抗自己携带技能系别的攻击伤害。",
            "triggers": [{
                "on": "defend",
                "condition": {
                    "path": "skill.element",
                    "op": "in",
                    "value": "=@defender_skill_elements",
                },
                "use_modifiers": {
                    "damage_mult": {
                        "op": "set",
                        "value": 0.0,
                    },
                },
            }],
        }
        ctx = _make_ctx(data)
        result = parse_pass.apply(ctx)
        assert len(result.errors) == 0
        trigger = result.ir[0]
        assert trigger.on == "defend"
        assert isinstance(trigger.condition, PathCond)
        assert trigger.condition.path == ["skill", "element"]
        assert trigger.use_modifiers is not None
        assert "damage_mult" in trigger.use_modifiers

    def test_entry_with_conditional_replace(self, parse_pass):
        """得寸进尺: conditional_replace with clear_condition."""
        data = {
            "id": 20045,
            "name": "得寸进尺",
            "description": "天气为雨天时，双攻+100%。天气不满足时效果消失。",
            "triggers": [{
                "on": "entry",
                "clear_condition": {
                    "path": "battle.globals.weather",
                    "op": "eq",
                    "value": "rain",
                },
                "effects_mode": "conditional_replace",
                "effects": [
                    {
                        "kind": "stat",
                        "stat": "atk",
                        "steps": 10,
                        "scope": "battlefield",
                        "source": "得寸进尺",
                    },
                ],
            }],
        }
        ctx = _make_ctx(data)
        result = parse_pass.apply(ctx)
        assert len(result.errors) == 0
        trigger = result.ir[0]
        assert trigger.effects_mode == "conditional_replace"
        assert trigger.clear_condition is not None
        assert isinstance(trigger.clear_condition, PathCond)
        assert trigger.clear_condition.path == ["battle", "globals", "weather"]

    def test_entry_with_battleskill_mut_ref_value(self, parse_pass):
        """身经百练: entry with battleskill_mut using RefExpr value."""
        data = {
            "id": 20132,
            "name": "身经百练",
            "description": "根据己方队伍应对成功次数提升水系和武系技能威力。",
            "triggers": [{
                "on": "entry",
                "effects_mode": "replace",
                "battleskill_mut": [{
                    "filter": {"element": "水"},
                    "field": "power_mod",
                    "op": "add",
                    "value": "=@team_counters[counter_success]",
                }],
            }],
        }
        ctx = _make_ctx(data)
        result = parse_pass.apply(ctx)
        assert len(result.errors) == 0
        trigger = result.ir[0]
        mut = trigger.battleskill_mut[0]
        assert isinstance(mut.value, RefExpr)
        assert mut.value.root == "team_counters"
        assert mut.value.path == ["counter_success"]

    def test_multiple_triggers(self, parse_pass):
        """灵魂灼伤: two triggers on same hook with different conditions."""
        data = {
            "id": 20092,
            "name": "灵魂灼伤",
            "description": "冰系技能使敌方获得4层灼烧，火系技能使敌方获得2层冻结。",
            "triggers": [
                {
                    "on": "skill_use",
                    "condition": {"path": "skill.element", "op": "eq", "value": "冰"},
                    "effects": [{"kind": "abnormal", "name": "灼烧", "stacks": 4, "target": "target", "source": "灵魂灼伤"}],
                },
                {
                    "on": "skill_use",
                    "condition": {"path": "skill.element", "op": "eq", "value": "火"},
                    "effects": [{"kind": "abnormal", "name": "冻结", "stacks": 2, "target": "target", "source": "灵魂灼伤"}],
                },
            ],
        }
        ctx = _make_ctx(data)
        result = parse_pass.apply(ctx)
        assert len(result.errors) == 0
        assert len(result.ir) == 2
        assert result.ir[0].condition is not None
        assert result.ir[1].condition is not None

    def test_entry_with_flags(self, parse_pass):
        """警惕: trigger with only flags (no effects)."""
        data = {
            "id": 20125,
            "name": "警惕",
            "description": "回合结束时，若自己能量为0则脱离。",
            "triggers": [{
                "on": "turn_end",
                "condition": {"path": "self.energy", "op": "lte", "value": 0},
                "flags": {"_escape_pending": True},
            }],
        }
        ctx = _make_ctx(data)
        result = parse_pass.apply(ctx)
        assert len(result.errors) == 0
        trigger = result.ir[0]
        assert trigger.flags == {"_escape_pending": True}

    def test_entry_with_pending_effects(self, parse_pass):
        """茶多酚: trigger with pending_effects."""
        data = {
            "id": 20114,
            "name": "茶多酚",
            "description": "离场后，更换入场的精灵回复20%生命且免疫寄生。",
            "triggers": [{
                "on": "leave",
                "pending_effects": [
                    {"kind": "state", "name": "回复20%HP", "source": "茶多酚"},
                    {"kind": "state", "name": "免疫寄生", "source": "茶多酚"},
                ],
            }],
        }
        ctx = _make_ctx(data)
        result = parse_pass.apply(ctx)
        assert len(result.errors) == 0
        trigger = result.ir[0]
        assert len(trigger.pending_effects) == 2

    def test_empty_triggers_produces_error(self, parse_pass):
        """Trait with no triggers should produce an error."""
        data = {
            "id": 99999,
            "name": "空特性",
            "triggers": [],
        }
        ctx = _make_ctx(data)
        result = parse_pass.apply(ctx)
        assert len(result.errors) > 0
