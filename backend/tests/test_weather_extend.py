"""汇流 — 天气回合数延长（`weather` op 的 `extend` 语义）。

游戏内文本：「雨天的回合数延长4回合，应对防御：改为延长8回合。」
口径（data/IR_GUIDE.md §3B weather）：同天气累加；无天气则起天气；其它天气不生效。
"""

from __future__ import annotations

from backend.sim.action import Action
from backend.sim.battle import Battle
from backend.sim.factory import SimFactory
from backend.sim.traits import dispatch_entry

factory = SimFactory()


def _battle(skills_a=("汇流", "猛烈撞击"), weather: str = "", turns: int = 0):
    p1 = factory.build_player("A", [{"name": "草衣虫", "skills": list(skills_a)}])
    p2 = factory.build_player("B", [{"name": "花衣蝶", "skills": ["猛烈撞击"]}])
    battle = Battle(p1, p2, verbose=False, weather=weather)
    battle.species_db = factory.sprite_db
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0
    if weather:
        battle.globals.set_weather(weather, turns)
    dispatch_entry(p1.team[0], battle, "A")
    dispatch_entry(p2.team[0], battle, "B")
    return battle


def test_huiliu_extends_rain_by_4():
    battle = _battle(weather="rain", turns=8)

    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)

    assert battle.globals.weather == "rain"
    assert battle.globals.weather_turns == 12


def test_huiliu_counter_success_extends_by_8():
    battle = _battle(weather="rain", turns=8, skills_a=("汇流",))

    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True,
                             countered_skill=battle.player_b.active.skills[0])

    assert battle.globals.weather_turns == 16


def test_huiliu_starts_rain_when_no_weather():
    battle = _battle()

    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)

    assert battle.globals.weather == "rain"
    assert battle.globals.weather_turns == 4


def test_huiliu_does_not_override_other_weather():
    battle = _battle(weather="sand", turns=5)

    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)

    assert battle.globals.weather == "sand"
    assert battle.globals.weather_turns == 5


def test_weather_extend_is_idempotent_for_other_elements():
    """非延长（默认 extend=False）仍是重设语义，不受影响。"""
    from backend.engine.replayer import JournalReplayer
    from backend.vm.journal import WeatherSet

    battle = _battle(weather="rain", turns=8)
    r = JournalReplayer(battle.player_a.active, battle.player_b.active, battle.globals,
                        battle._vm_engine.registry, team="A", battle=battle)
    r.replay([WeatherSet(weather="sand", turns=6)])

    assert battle.globals.weather == "sand"
    assert battle.globals.weather_turns == 6


def test_globals_extend_weather_helper():
    from backend.sim.globals import GlobalEffects

    g = GlobalEffects()
    assert g.extend_weather("rain", 4) == "rain"
    assert g.weather_turns == 4

    assert g.extend_weather("rain", 8) == "rain"
    assert g.weather_turns == 12

    # 其它天气：不生效
    assert g.extend_weather("sand", 8) == ""
    assert g.weather == "rain"
    assert g.weather_turns == 12
