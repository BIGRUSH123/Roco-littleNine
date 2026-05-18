"""Tests for Pass 1: SkillParsePass."""
import pytest
from backend.vm.ir_skill import (
    CondExpr, AndCond, OrCond, NotCond,
    WhenBlock, WhenBranch,
    ModOp, HitOp, MarkOp, AbnormalOp, WeatherOp,
    DispelOp, StealOp, TickOp, DoubleOp, ChargeOp,
    EscapeOp, ReturnOp, LockOp, InterruptOp,
    ExchangeOp, ResetOp, RedirectOp, ReplayOp,
    BorrowOp, CountOp,
)
from backend.vm.ir_values import Literal, Query
from backend.vm.compiler.context import CompilerContext
from backend.vm.compiler.passes.skill_parse import SkillParsePass


class TestSkillParsePass:
    """Tests for the SkillParsePass."""

    def test_parse_simple_mod(self):
        """Parse a simple stat modification."""
        data = {
            "effects": [
                {"target": "sprite_self", "op": "mod", "stat": "atk", "steps": 3}
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        assert len(ctx.ir) == 1
        assert len(ctx.errors) == 0
        op = ctx.ir[0]
        assert isinstance(op, ModOp)
        assert op.target == "sprite_self"
        assert op.stat == "atk"
        assert op.steps == 3

    def test_parse_mod_with_value(self):
        """Parse a mod with explicit value."""
        data = {
            "effects": [
                {"op": "mod", "target": "sprite_self", "stat": "power",
                 "value": 70, "mode": "add", "on_next": True}
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        assert len(ctx.errors) == 0
        op = ctx.ir[0]
        assert isinstance(op, ModOp)
        assert op.value == Literal(value=70)
        assert op.mode == "add"
        assert op.on_next is True

    def test_parse_value_as_literal(self):
        """Value as plain number becomes Literal."""
        data = {
            "effects": [
                {"op": "mod", "target": "sprite_self", "stat": "hp",
                 "value": 0.3}
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        op = ctx.ir[0]
        assert isinstance(op.value, Literal)
        assert op.value.value == 0.3

    def test_parse_value_as_query(self):
        """Value with 'q' key becomes Query resolved via ADDRESS_MAP."""
        data = {
            "effects": [
                {"op": "mod", "target": "sprite_self", "stat": "power",
                 "value": {"q": "energy", "of": "sprite_self", "scale": 10}}
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        assert len(ctx.errors) == 0
        op = ctx.ir[0]
        assert isinstance(op.value, Query)
        assert op.value.field == "energy_self"
        assert op.value.scale == 10

    def test_parse_query_with_name(self):
        """Query with 'name' for dict-type registers."""
        data = {
            "effects": [
                {"op": "mod", "target": "sprite_self", "stat": "power",
                 "value": {"q": "abnormal_stacks", "of": "sprite_opp",
                           "name": "中毒", "scale": 0.1, "offset": 0.5}}
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        op = ctx.ir[0]
        assert isinstance(op.value, Query)
        assert op.value.field == "abnormal_stacks_opp"
        assert op.value.name == "中毒"
        assert op.value.scale == 0.1
        assert op.value.offset == 0.5

    def test_parse_query_unknown_address(self):
        """Query with unknown (of, q) raises error."""
        data = {
            "effects": [
                {"op": "mod", "target": "sprite_self", "stat": "power",
                 "value": {"q": "nonexistent", "of": "sprite_self"}}
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        assert len(ctx.errors) == 1
        assert "Unknown query address" in ctx.errors[0].message

    def test_parse_when_block_simple(self):
        """Parse a simple WhenBlock with cond/then/else."""
        data = {
            "effects": [
                {
                    "when": {"cond": "counter_succeeded"},
                    "then": [
                        {"op": "mod", "target": "sprite_self", "stat": "atk",
                         "value": 1}
                    ],
                    "else": [
                        {"op": "mod", "target": "sprite_self", "stat": "def",
                         "value": 1}
                    ]
                }
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        assert len(ctx.errors) == 0
        assert len(ctx.ir) == 1
        wb = ctx.ir[0]
        assert isinstance(wb, WhenBlock)
        assert isinstance(wb.cond, CondExpr)
        assert wb.cond.cond == "counter_succeeded"
        assert len(wb.then) == 1
        assert isinstance(wb.then[0], ModOp)
        assert len(wb.else_) == 1
        assert isinstance(wb.else_[0], ModOp)

    def test_parse_when_block_with_and_cond(self):
        """Parse WhenBlock with AND compound condition."""
        data = {
            "effects": [
                {
                    "when": {
                        "cond": "and",
                        "conditions": [
                            {"cond": "is_attack"},
                            {"cond": "hp_below", "ratio": 0.5},
                        ]
                    },
                    "then": [
                        {"op": "mod", "target": "sprite_self", "stat": "atk",
                         "value": 1}
                    ]
                }
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        assert len(ctx.errors) == 0
        wb = ctx.ir[0]
        assert isinstance(wb.cond, AndCond)
        assert len(wb.cond.conditions) == 2

    def test_parse_when_block_with_or_cond(self):
        """Parse WhenBlock with OR compound condition."""
        data = {
            "effects": [
                {
                    "when": {
                        "cond": "or",
                        "conditions": [
                            {"cond": "skill_at", "position": 0},
                            {"cond": "skill_at", "position": 2},
                        ]
                    },
                    "then": [
                        {"op": "mod", "target": "skill_off_0", "stat": "combo",
                         "value": 1, "mode": "add", "feeds": "power"}
                    ]
                }
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        assert len(ctx.errors) == 0
        wb = ctx.ir[0]
        assert isinstance(wb.cond, OrCond)
        assert len(wb.cond.conditions) == 2

    def test_parse_abnormal(self):
        """Parse an abnormal op."""
        data = {
            "effects": [
                {"op": "abnormal", "target": "sprite_opp", "name": "中毒", "stacks": 2}
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        assert len(ctx.errors) == 0
        op = ctx.ir[0]
        assert isinstance(op, AbnormalOp)
        assert op.name == "中毒"
        assert op.stacks == 2
        assert op.target == "sprite_opp"

    def test_parse_mark(self):
        """Parse a mark op."""
        data = {
            "effects": [
                {"op": "mark", "target": "team_opp", "name": "星陨印记",
                 "stacks": 1}
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        assert len(ctx.errors) == 0
        op = ctx.ir[0]
        assert isinstance(op, MarkOp)
        assert op.name == "星陨印记"
        assert op.target == "team_opp"

    def test_parse_mark_with_query_value(self):
        """Parse mark with query-based value."""
        data = {
            "effects": [
                {"op": "mark", "target": "team_opp", "name": "星陨印记",
                 "value": {"q": "mark_count", "of": "team_opp", "name": "any"}}
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        op = ctx.ir[0]
        assert isinstance(op, MarkOp)
        assert isinstance(op.value, Query)
        assert op.value.field == "mark_count_opp"

    def test_parse_escape(self):
        """Parse an escape op."""
        data = {
            "effects": [
                {"op": "escape", "target": "sprite_self"}
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        op = ctx.ir[0]
        assert isinstance(op, EscapeOp)
        assert op.target == "sprite_self"

    def test_parse_return(self):
        """Parse a return op."""
        data = {
            "effects": [
                {"op": "return", "target": "sprite_opp"}
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        op = ctx.ir[0]
        assert isinstance(op, ReturnOp)
        assert op.target == "sprite_opp"

    def test_parse_exchange(self):
        """Parse an exchange op."""
        data = {
            "effects": [
                {"op": "exchange", "what": "effects"}
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        op = ctx.ir[0]
        assert isinstance(op, ExchangeOp)
        assert op.what == "effects"

    def test_parse_double(self):
        """Parse a double op."""
        data = {
            "effects": [
                {"op": "double", "target": "sprite_opp", "what": "negative"}
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        op = ctx.ir[0]
        assert isinstance(op, DoubleOp)
        assert op.target == "sprite_opp"
        assert op.what == "negative"

    def test_parse_charge(self):
        """Parse a charge op."""
        data = {
            "effects": [
                {"op": "charge"}
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        op = ctx.ir[0]
        assert isinstance(op, ChargeOp)
        assert op.target == "sprite_self"

    def test_parse_dispel(self):
        """Parse a dispel op."""
        data = {
            "effects": [
                {"op": "dispel", "target": "team_opp", "what": "mark"}
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        op = ctx.ir[0]
        assert isinstance(op, DispelOp)
        assert op.what == "mark"
        assert op.target == "team_opp"

    def test_parse_steal(self):
        """Parse a steal op."""
        data = {
            "effects": [
                {"op": "steal", "target": "sprite_opp", "what": "energy", "amount": 3}
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        op = ctx.ir[0]
        assert isinstance(op, StealOp)
        assert op.what == "energy"
        assert op.amount == 3

    def test_parse_lock(self):
        """Parse a lock op."""
        data = {
            "effects": [
                {"op": "lock", "target": "sprite_opp", "turns": 2}
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        op = ctx.ir[0]
        assert isinstance(op, LockOp)
        assert op.turns == 2

    def test_parse_interrupt(self):
        """Parse an interrupt op."""
        data = {
            "effects": [
                {"op": "interrupt", "target": "sprite_opp"}
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        op = ctx.ir[0]
        assert isinstance(op, InterruptOp)

    def test_parse_weather(self):
        """Parse a weather op."""
        data = {
            "effects": [
                {"op": "weather", "weather": "rain", "turns": 8}
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        op = ctx.ir[0]
        assert isinstance(op, WeatherOp)
        assert op.weather == "rain"
        assert op.turns == 8

    def test_parse_count(self):
        """Parse a count op with when condition."""
        data = {
            "effects": [
                {
                    "op": "count",
                    "when": {"cond": "sprite_entered"},
                    "then": [
                        {"op": "mod", "target": "skill_off_0", "stat": "power",
                         "value": 1, "mode": "add", "scope": "permanent"}
                    ]
                }
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        assert len(ctx.errors) == 0
        op = ctx.ir[0]
        assert isinstance(op, CountOp)
        assert isinstance(op.when, CondExpr)
        assert op.when.cond == "sprite_entered"
        assert len(op.then) == 1

    def test_parse_with_feeds_and_needs(self):
        """Parse effect with feeds/needs declarations."""
        data = {
            "effects": [
                {"op": "mod", "target": "skill_off_0", "stat": "power",
                 "value": 50, "feeds": "power"}
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        op = ctx.ir[0]
        assert op.feeds == "power"

    def test_parse_unknown_op(self):
        """Unknown op type produces error."""
        data = {
            "effects": [
                {"op": "nonexistent_op"}
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        assert len(ctx.errors) == 1
        assert "Unknown op type" in ctx.errors[0].message

    def test_parse_empty_effects(self):
        """Empty effects list produces empty IR."""
        data = {"effects": []}
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        assert len(ctx.ir) == 0
        assert len(ctx.errors) == 0

    def test_parse_no_effects_key(self):
        """Missing effects key produces empty IR."""
        data = {}
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        assert len(ctx.ir) == 0
        assert len(ctx.errors) == 0

    def test_parse_replay(self):
        """Parse borrow op."""
        data = {
            "effects": [
                {"op": "borrow", "from_": "sprite_self"}
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        op = ctx.ir[0]
        assert isinstance(op, BorrowOp)

    def test_parse_conditions_with_params(self):
        """Simple conditions with params are parsed correctly."""
        data = {
            "effects": [
                {
                    "when": {"cond": "hp_below", "ratio": 0.5},
                    "then": [
                        {"op": "mod", "target": "sprite_self", "stat": "atk",
                         "value": 1}
                    ]
                }
            ]
        }
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        wb = ctx.ir[0]
        assert isinstance(wb.cond, CondExpr)
        assert wb.cond.cond == "hp_below"
        assert wb.cond.params == {"ratio": 0.5}
