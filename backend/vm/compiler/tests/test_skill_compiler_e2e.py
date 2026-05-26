"""End-to-end tests for the full SkillCompiler pipeline."""
import json
from pathlib import Path

import pytest

from backend.vm.compiler.context import CompilationError
from backend.vm.compiler.skill_compiler import SkillCompiler
from backend.vm.ir_skill import (
    AbnormalOp,
    ChargeOp,
    CountOp,
    DispelOp,
    DoubleOp,
    EscapeOp,
    ExchangeOp,
    HitOp,
    MarkOp,
    ModOp,
    StealOp,
    WhenBlock,
)
from backend.vm.ir_values import Literal, Query

# ── fixtures ──

@pytest.fixture
def compiler():
    return SkillCompiler()


@pytest.fixture
def all_skills():
    """Load all skill JSON files from data/skills/."""
    skills_dir = Path(__file__).parent.parent.parent.parent.parent / "data" / "skills"
    skills = {}
    for file_path in sorted(skills_dir.glob("*.json")):
        if file_path.name.startswith("_"):
            continue
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
        skills[data.get("name", file_path.stem)] = data
    return skills


# ── unit-level E2E cases ──

class TestCompilerE2E:
    """End-to-end compilation of representative skills."""

    def test_compile_simple_attack(self, compiler):
        """丢冰块: 物攻 skill with mod effect."""
        data = {
            "id": 10051,
            "name": "丢冰块",
            "element": "冰",
            "skill_type": "物攻",
            "power": 90,
            "energy_cost": 3,
            "effects": [
                {"target": "sprite_opp", "op": "mod", "stat": "speed", "steps": -3}
            ],
            "description": "造成物伤"
        }
        compiled = compiler.compile(data)
        assert compiled.name == "丢冰块"
        assert compiled.skill_type == "物攻"
        assert compiled.power == 90
        assert len(compiled.effects) == 2  # ModOp + HitOp (HitOp injected at end)
        assert isinstance(compiled.effects[0], ModOp)
        assert isinstance(compiled.effects[1], HitOp)
        assert compiled.effects[1].power == Literal(value=90)

    def test_compile_status_skill(self, compiler):
        """三连破: 状态 skill, no HitOp injected."""
        data = {
            "id": 10301,
            "name": "三连破",
            "element": "普通",
            "skill_type": "状态",
            "energy_cost": 1,
            "combo": 3,
            "effects": [
                {"target": "sprite_self", "op": "mod", "stat": "atk", "steps": 3}
            ],
            "description": "自己获得物攻+30%"
        }
        compiled = compiler.compile(data)
        assert compiled.name == "三连破"
        assert compiled.skill_type == "状态"
        assert compiled.power == 0
        assert len(compiled.effects) == 1
        assert isinstance(compiled.effects[0], ModOp)

    def test_compile_defense_skill(self, compiler):
        """不可接触: 防御 skill with query value."""
        data = {
            "id": 10501,
            "name": "不可接触",
            "element": "毒",
            "skill_type": "防御",
            "energy_cost": 1,
            "counter": "攻击",
            "effects": [
                {
                    "value": {"q": "abnormal_stacks", "of": "sprite_opp",
                              "name": "中毒", "scale": 0.1, "offset": 0.5},
                    "target": "sprite_self",
                    "op": "mod",
                    "stat": "damage_reduction"
                }
            ],
            "description": "减伤"
        }
        compiled = compiler.compile(data)
        assert compiled.name == "不可接触"
        assert compiled.counter == "攻击"
        assert len(compiled.effects) == 1
        op = compiled.effects[0]
        assert isinstance(op, ModOp)
        assert isinstance(op.value, Query)
        assert op.value.field == "abnormal_stacks_opp"
        assert op.value.name == "中毒"

    def test_compile_with_when_block(self, compiler):
        """放晴: when block with else."""
        data = {
            "id": 10007,
            "name": "放晴",
            "element": "光",
            "skill_type": "状态",
            "energy_cost": 0,
            "counter": "防御",
            "effects": [
                {
                    "when": {"cond": "counter_succeeded"},
                    "then": [
                        {"op": "mod", "target": "sprite_self", "stat": "power_mult",
                         "value": 2, "scope": "permanent", "element": "光"}
                    ],
                    "else": [
                        {"op": "mod", "target": "sprite_self", "stat": "power_mult",
                         "value": 1.5, "scope": "permanent", "element": "光"}
                    ]
                }
            ],
            "description": "光系技能威力永久+50%"
        }
        compiled = compiler.compile(data)
        assert compiled.name == "放晴"
        assert len(compiled.effects) == 1
        wb = compiled.effects[0]
        assert isinstance(wb, WhenBlock)
        assert len(wb.then) == 1
        assert len(wb.else_) == 1

    def test_compile_with_escape(self, compiler):
        """恶意逃离: escape + when block."""
        data = {
            "id": 10255,
            "name": "恶意逃离",
            "element": "恶",
            "skill_type": "状态",
            "energy_cost": 1,
            "counter": "防御",
            "effects": [
                {"target": "sprite_self", "op": "escape"},
                {
                    "when": {"cond": "counter_succeeded"},
                    "then": [
                        {"op": "mod", "target": "sprite_opp", "stat": "energy_cost",
                         "value": 4, "scope": "persistent", "skill_filter": "attack"}
                    ]
                }
            ],
            "description": "脱离"
        }
        compiled = compiler.compile(data)
        assert compiled.name == "恶意逃离"
        assert len(compiled.effects) == 2
        assert isinstance(compiled.effects[0], EscapeOp)
        assert isinstance(compiled.effects[1], WhenBlock)

    def test_compile_with_exchange(self, compiler):
        """欺诈契约: exchange op."""
        data = {
            "id": 10264,
            "name": "欺诈契约",
            "element": "恶",
            "skill_type": "状态",
            "energy_cost": 3,
            "effects": [
                {"op": "exchange", "what": "effects"}
            ],
            "description": "交换增益和减益"
        }
        compiled = compiler.compile(data)
        assert compiled.name == "欺诈契约"
        op = compiled.effects[0]
        assert isinstance(op, ExchangeOp)
        assert op.what == "effects"

    def test_compile_with_charge(self, compiler):
        """升龙咆哮: charge op."""
        data = {
            "id": 10901,
            "name": "升龙咆哮",
            "element": "龙",
            "skill_type": "魔攻",
            "power": 200,
            "energy_cost": 3,
            "effects": [
                {
                    "when": {"cond": "charged"},
                    "then": [],
                    "else": [{"op": "charge"}]
                }
            ],
            "description": "蓄力"
        }
        compiled = compiler.compile(data)
        assert compiled.name == "升龙咆哮"
        # HitOp injected at end, after when block
        assert isinstance(compiled.effects[0], WhenBlock)
        assert isinstance(compiled.effects[1], HitOp)
        wb = compiled.effects[0]
        assert len(wb.else_) == 1
        assert isinstance(wb.else_[0], ChargeOp)

    def test_compile_with_count(self, compiler):
        """传感器: count op with sprite_entered condition."""
        data = {
            "id": 10402,
            "name": "传感器",
            "element": "机械",
            "skill_type": "物攻",
            "power": 20,
            "energy_cost": 1,
            "combo": 2,
            "effects": [
                {
                    "when": {
                        "cond": "or",
                        "conditions": [
                            {"cond": "skill_at", "position": 0},
                            {"cond": "skill_at", "position": 2}
                        ]
                    },
                    "then": [
                        {"op": "mod", "target": "skill_off_0", "stat": "combo",
                         "value": 1, "mode": "add", "feeds": "power"}
                    ]
                },
                {
                    "op": "count",
                    "when": {"cond": "sprite_entered"},
                    "then": [
                        {"op": "mod", "target": "skill_off_0", "stat": "power",
                         "value": 1, "mode": "add", "scope": "permanent"}
                    ]
                }
            ],
            "description": "传动1"
        }
        compiled = compiler.compile(data)
        assert compiled.name == "传感器"
        assert compiled.combo == 2
        # Should have: HitOp + WhenBlock + CountOp
        assert len(compiled.effects) == 3
        assert isinstance(compiled.effects[0], WhenBlock)
        assert isinstance(compiled.effects[1], CountOp)

    def test_compile_with_abnormal(self, compiler):
        """毒囊: abnormal op with counter condition."""
        data = {
            "id": 10505,
            "name": "毒囊",
            "element": "毒",
            "skill_type": "物攻",
            "power": 25,
            "energy_cost": 2,
            "counter": "状态",
            "effects": [
                {
                    "when": {"cond": "counter_succeeded"},
                    "then": [
                        {"op": "abnormal", "target": "sprite_opp", "name": "中毒",
                         "stacks": 6}
                    ],
                    "else": [
                        {"op": "abnormal", "target": "sprite_opp", "name": "中毒",
                         "stacks": 2}
                    ]
                }
            ],
            "description": "造成物伤"
        }
        compiled = compiler.compile(data)
        assert compiled.name == "毒囊"
        # HitOp + WhenBlock
        wb = compiled.effects[0]
        assert isinstance(wb, WhenBlock)
        assert isinstance(wb.then[0], AbnormalOp)
        assert wb.then[0].stacks == 6
        assert isinstance(wb.else_[0], AbnormalOp)
        assert wb.else_[0].stacks == 2

    def test_compile_with_mark(self, compiler):
        """增程电池: mark op."""
        data = {
            "id": 10654,
            "name": "增程电池",
            "element": "电",
            "skill_type": "状态",
            "energy_cost": 2,
            "effects": [
                {"op": "mark", "target": "sprite_self", "name": "蓄电印记", "stacks": 1}
            ],
            "description": "获得蓄电印记"
        }
        compiled = compiler.compile(data)
        op = compiled.effects[0]
        assert isinstance(op, MarkOp)
        assert op.name == "蓄电印记"

    def test_compile_with_dispel(self, compiler):
        """焚毁: dispel op."""
        data = {
            "id": 10628,
            "name": "焚毁",
            "element": "火",
            "skill_type": "魔攻",
            "power": 60,
            "energy_cost": 2,
            "effects": [
                {"op": "dispel", "what": "mark", "target": "team_opp"}
            ],
            "description": "驱散敌方印记"
        }
        compiled = compiler.compile(data)
        # DispelOp + HitOp (HitOp injected at end)
        assert isinstance(compiled.effects[0], DispelOp)
        assert isinstance(compiled.effects[1], HitOp)
        assert compiled.effects[0].what == "mark"

    def test_compile_with_steal(self, compiler):
        """小偷小摸: steal op."""
        data = {
            "id": 10218,
            "name": "小偷小摸",
            "element": "幽",
            "skill_type": "状态",
            "energy_cost": 1,
            "effects": [
                {"op": "steal", "target": "sprite_opp", "what": "energy", "amount": 3}
            ],
            "description": "偷取能量"
        }
        compiled = compiler.compile(data)
        op = compiled.effects[0]
        assert isinstance(op, StealOp)
        assert op.amount == 3

    def test_compile_with_double(self, compiler):
        """落井下毒: double op."""
        data = {
            "id": 10517,
            "name": "落井下毒",
            "element": "毒",
            "skill_type": "状态",
            "energy_cost": 6,
            "effects": [
                {"op": "double", "target": "sprite_opp", "what": "negative"}
            ],
            "description": "减益翻倍"
        }
        compiled = compiler.compile(data)
        op = compiled.effects[0]
        assert isinstance(op, DoubleOp)
        assert op.what == "negative"

    def test_compile_empty_effects(self, compiler):
        """Skill with empty effects compiles ok."""
        data = {
            "id": 10130,
            "name": "鸣沙陷阱",
            "element": "地",
            "skill_type": "物攻",
            "power": 60,
            "energy_cost": 4,
            "effects": [],
            "description": "造成物伤"
        }
        compiled = compiler.compile(data)
        assert compiled.name == "鸣沙陷阱"
        # HitOp should be injected
        assert len(compiled.effects) == 1
        assert isinstance(compiled.effects[0], HitOp)

    def test_compile_meta_fields(self, compiler):
        """CompiledSkill carries all meta fields from JSON."""
        data = {
            "id": 10401,
            "name": "主轴",
            "element": "机械",
            "skill_type": "物攻",
            "power": 75,
            "energy_cost": 2,
            "priority": 0,
            "combo": 1,
            "counter": "",
            "effects": [],
            "description": "此技能位置不会改变",
            "tag": "",
            "use_devotion": False,
            "usable_while_charging": False,
            "position_locked": True,
        }
        compiled = compiler.compile(data)
        assert compiled.id == 10401
        assert compiled.element == "机械"
        assert compiled.energy_cost == 2
        assert compiled.position_locked is True
        assert compiled.description == "此技能位置不会改变"

    def test_compilation_error_on_bad_data(self, compiler):
        """Invalid effect produces CompilationError."""
        data = {
            "id": 99999,
            "name": "BadSkill",
            "element": "普通",
            "skill_type": "状态",
            "power": 0,
            "energy_cost": 1,
            "effects": [
                {"op": "mod", "target": "invalid_target", "stat": "atk",
                 "value": 1, "scope": "bad_scope"}
            ],
            "description": "bad"
        }
        with pytest.raises(CompilationError):
            compiler.compile(data)


class TestCompileAllSkills:
    """Compile all ~470 skills from data/skills/ and verify success."""

    def test_compiles_all_skills(self, compiler, all_skills):
        errors = []
        for name, data in all_skills.items():
            try:
                compiled = compiler.compile(data)
                assert compiled.name == data["name"], \
                    f"Name mismatch: {compiled.name} != {data['name']}"
            except CompilationError as e:
                errors.append(f"{name}: {e}")
            except Exception as e:
                errors.append(f"{name}: {type(e).__name__}: {e}")

        assert len(errors) == 0, \
            f"{len(errors)}/{len(all_skills)} skills failed:\n" + \
            "\n".join(errors[:10])
