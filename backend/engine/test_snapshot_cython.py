import dataclasses
import time

import pytest

from backend.common.models import SpeciesStats
from backend.engine.snapshot import build_ctx
from backend.sim.battleskill import BattleSkill
from backend.sim.globals import GlobalEffects
from backend.sim.skill import Skill
from backend.sim.sprite import Sprite
from backend.vm.effect import MarkEffect

snapshot_cy = pytest.importorskip("backend.engine.snapshot_cy")


def _skill(name: str, element: str, energy_cost: int, power: int = 50, combo: int = 1) -> Skill:
    return Skill(
        name=name,
        element=element,
        skill_type="物攻",
        power=power,
        energy_cost=energy_cost,
        combo=combo,
    )


def _sprite(name: str, elements: tuple[str, ...], skills: list[BattleSkill]) -> Sprite:
    species = SpeciesStats(
        name=name,
        hp=220,
        atk=120,
        def_=90,
        sp_atk=110,
        sp_def=100,
        speed=95,
        attributes=",".join(elements),
    )
    sprite = Sprite(
        species=species,
        current_hp=180,
        max_hp=220,
        energy=7,
        initial_stats={
            "atk": 120,
            "def": 90,
            "sp_atk": 110,
            "sp_def": 100,
            "speed": 95,
        },
    )
    sprite.skills = skills
    return sprite


def _ctx_as_dict(ctx):
    return {
        field.name: getattr(ctx, field.name)
        for field in dataclasses.fields(ctx)
    }


def _sample_inputs():
    self_skill = BattleSkill(base=_skill("烈焰", "火", 3, power=80, combo=2))
    self_skill._modifiers["energy_cost"] = -1
    self_skill._modifiers["power_mult"] = 1.2
    self_skill._element_override = "水"

    nullified_skill = BattleSkill(base=_skill("失效", "草", 5))
    nullified_skill.nullified = True
    nullified_skill._modifiers["energy_cost"] = -2

    replaced_skill = BattleSkill(base=_skill("原始", "普通", 4))
    replaced_skill.replaced_by = _skill("替换", "翼", 1, power=40)
    replaced_skill._mech_energy_reduction = -1

    opp_skill = BattleSkill(base=_skill("对手", "草", 2, power=70))
    opp_skill._modifiers["energy_cost"] = 3

    self_sprite = _sprite("自方", ("火", "翼"), [self_skill, nullified_skill, replaced_skill])
    opp_sprite = _sprite("对方", ("草",), [opp_skill])
    self_sprite._modifiers.update({
        "atk": 0.25,
        "combo": 1,
        "combo_mult": 2.0,
        "damage_mult": 1.3,
        "power_mult": 1.1,
        "life_drain": 0.2,
    })
    self_sprite.counters["times_entered"] = 2
    opp_sprite._modifiers["def"] = 0.2

    globals_ = GlobalEffects()
    globals_.weather = "rain"
    globals_.mark_effects = {
        "A": [MarkEffect(name="增伤印记", source="test", stacks=2, category="positive", damage_mult=0.1, condition="is_first")],
        "B": [MarkEffect(name="压制印记", source="test", stacks=3, category="negative")],
    }
    return self_sprite, opp_sprite, self_skill, opp_skill, globals_


def test_build_ctx_cython_matches_python_snapshot():
    self_sprite, opp_sprite, self_skill, opp_skill, globals_ = _sample_inputs()

    kwargs = {
        "team": "A",
        "turn": 3,
        "is_first": True,
        "damage_taken_this_turn": 12,
        "target_fainted": True,
        "skill_index": 1,
        "elements_used_count_self": 2,
        "burst_triggered_count_own": 1,
        "fainted_own": 1,
        "fainted_opp": 2,
        "lives_own": 4,
        "lives_opp": 3,
        "team_elements_own": frozenset({"火", "翼"}),
        "team_elements_opp": frozenset({"草"}),
        "counter_values": {"连击": 2},
        "battle_skill": self_skill,
    }

    ctx_py = build_ctx(self_sprite, opp_sprite, self_skill, opp_skill, globals_, **kwargs)
    ctx_cy = snapshot_cy.build_ctx_cy(self_sprite, opp_sprite, self_skill, opp_skill, globals_, **kwargs)

    assert _ctx_as_dict(ctx_cy) == _ctx_as_dict(ctx_py)


def test_build_ctx_cython_is_not_slower_than_python():
    self_sprite, opp_sprite, self_skill, opp_skill, globals_ = _sample_inputs()
    kwargs = {"team": "A", "turn": 3, "is_first": True, "battle_skill": self_skill}

    # Warm both paths and caches before timing.
    for _ in range(100):
        build_ctx(self_sprite, opp_sprite, self_skill, opp_skill, globals_, **kwargs)
        snapshot_cy.build_ctx_cy(self_sprite, opp_sprite, self_skill, opp_skill, globals_, **kwargs)

    iterations = 3000
    start = time.perf_counter()
    for _ in range(iterations):
        build_ctx(self_sprite, opp_sprite, self_skill, opp_skill, globals_, **kwargs)
    py_elapsed = time.perf_counter() - start

    start = time.perf_counter()
    for _ in range(iterations):
        snapshot_cy.build_ctx_cy(self_sprite, opp_sprite, self_skill, opp_skill, globals_, **kwargs)
    cy_elapsed = time.perf_counter() - start

    assert cy_elapsed <= py_elapsed * 1.10, (
        f"Cython build_ctx should not be materially slower: "
        f"python={py_elapsed:.4f}s cython={cy_elapsed:.4f}s"
    )
