"""Tests for shared effect_applier — stat/abnormal/mark/weather/special effects."""
from backend.vm.effect_applier import apply_effect
from backend.vm.ir_trait import (
    TraitAbnormalEffect,
    TraitMarkEffect,
    TraitSpecialEffect,
    TraitStatEffect,
    TraitWeatherEffect,
)
from backend.vm.ir_values import Literal

# ── Mock classes ──

class MockMark:
    def __init__(self, name, category, stacks=0):
        self.name = name
        self.category = category
        self.stacks = stacks


class MockGlobals:
    def __init__(self):
        self.weather = ""
        self.weather_turns = 0
        self._marks: dict[str, list] = {"A": [], "B": []}

    def apply_mark(self, team, name, category, stacks):
        mark = MockMark(name, category, stacks)
        self._marks.setdefault(team, []).append(mark)
        return []

    def get_marks(self, team):
        return self._marks.get(team, []), []

    def get_mark_by_name(self, team, name):
        for m in self._marks.get(team, []):
            if m.name == name:
                return m
        return None

    def set_weather(self, weather, turns=8):
        self.weather = weather
        self.weather_turns = turns

    @staticmethod
    def classify_mark(name):
        positive = {"攻击印记", "蓄电印记", "润泽印记", "风起", "光合印记", "蓄势印记", "龙噬印记"}
        return "positive" if name in positive else "negative"


class MockBattle:
    def __init__(self):
        self.turn = 1
        self.globals = MockGlobals()
        self.scheduled_effects = []
        self.pending_effects: dict[str, list] = {}

    def get_player(self, team):
        return None

    def get_opponent(self, team):
        return None

    def get_team_counter(self, team, key):
        return 0

    def inc_team_counter(self, team, key, delta):
        pass


class MockSprite:
    def __init__(self, name="test_sprite", hp=200, energy=10):
        self.name = name
        self.current_hp = hp
        self.max_hp = hp
        self.energy = energy
        self.effects: list = []
        self.is_fainted = False
        self.counters: dict[str, int] = {}
        self.species = None

    def add_effect(self, effect):
        self.effects.append(effect)

    def heal(self, amount):
        healed = min(amount, self.max_hp - self.current_hp)
        self.current_hp += healed
        return healed

    def gain_energy(self, amount):
        room = max(0, 10 - self.energy)
        gained = min(room, amount)
        self.energy += gained
        return gained

    def lose_energy(self, amount):
        lost = min(amount, self.energy)
        self.energy -= lost
        return lost

    def take_damage(self, amount):
        actual = min(self.current_hp, amount)
        self.current_hp -= actual
        return actual

    def get_stacks(self, name):
        for e in self.effects:
            if getattr(e, 'name', '') == name:
                return getattr(e, 'stacks', 0)
        return 0

    @property
    def max_energy(self):
        return 10


# ── Stat Effect Tests ──

class TestStatEffect:
    def test_basic_stat_boost(self):
        effect = TraitStatEffect(
            stat="atk", steps=Literal(2), source="test_trait"
        )
        sprite = MockSprite()
        result = apply_effect(effect, sprite, None, "A")
        assert len(result) == 1
        assert "物攻" in result[0]
        assert "+20%" in result[0]
        assert sprite.name in result[0]
        # Verify effect was added
        assert len(sprite.effects) == 1
        se = sprite.effects[0]
        assert se.name == "物攻+20%"
        assert se.category == "stat"
        assert se.stat_key == "atk"
        assert se.steps == 2

    def test_stat_debuff(self):
        effect = TraitStatEffect(
            stat="def", steps=Literal(-1), source="test_trait"
        )
        sprite = MockSprite()
        result = apply_effect(effect, sprite, None, "A")
        assert "物防" in result[0]
        assert "-10%" in result[0]
        se = sprite.effects[0]
        assert se.steps == -1

    def test_power_stat(self):
        effect = TraitStatEffect(
            stat="power", steps=Literal(3), source="test_trait"
        )
        sprite = MockSprite()
        result = apply_effect(effect, sprite, None, "A")
        assert "威力" in result[0]
        assert "+30" in result[0]
        se = sprite.effects[0]
        assert se.steps == 3

    def test_speed_stat(self):
        effect = TraitStatEffect(
            stat="speed", steps=Literal(1), source="test_trait"
        )
        sprite = MockSprite()
        result = apply_effect(effect, sprite, None, "A")
        assert "速度" in result[0]
        assert "+10%" in result[0]

    def test_priority_stat(self):
        effect = TraitStatEffect(
            stat="priority", steps=Literal(1), source="test_trait"
        )
        sprite = MockSprite()
        result = apply_effect(effect, sprite, None, "A")
        assert "先手" in result[0]
        assert "+1" in result[0]

    def test_energy_cost_stat(self):
        effect = TraitStatEffect(
            stat="energy_cost", steps=Literal(-1), source="test_trait"
        )
        sprite = MockSprite()
        result = apply_effect(effect, sprite, None, "A")
        assert "能耗" in result[0]
        assert "-1" in result[0]

    def test_combo_stat(self):
        effect = TraitStatEffect(
            stat="combo", steps=Literal(2), source="test_trait"
        )
        sprite = MockSprite()
        result = apply_effect(effect, sprite, None, "A")
        assert "连击" in result[0]
        assert "+2" in result[0]

    def test_zero_steps_no_effect(self):
        effect = TraitStatEffect(
            stat="atk", steps=Literal(0), source="test_trait"
        )
        sprite = MockSprite()
        result = apply_effect(effect, sprite, None, "A")
        # Should still create the effect (display "物攻+0%")
        assert len(sprite.effects) == 1


# ── Abnormal Effect Tests ──

class TestAbnormalEffect:
    def test_apply_poison(self):
        effect = TraitAbnormalEffect(
            name="中毒", stacks=Literal(3), source="test_trait"
        )
        sprite = MockSprite()
        result = apply_effect(effect, sprite, None, "A")
        assert len(result) == 1
        assert "中毒" in result[0]
        assert "+3" in result[0]
        se = sprite.effects[0]
        assert se.name == "中毒"
        assert se.category == "abnormal"
        assert se.stacks == 3

    def test_apply_paralysis(self):
        effect = TraitAbnormalEffect(
            name="麻痹", stacks=Literal(1), source="test_trait"
        )
        sprite = MockSprite()
        result = apply_effect(effect, sprite, None, "A")
        assert "麻痹" in result[0]
        assert sprite.get_stacks("麻痹") == 1

    def test_default_stacks_is_one(self):
        """TraitAbnormalEffect defaults stacks to Literal(1)."""
        effect = TraitAbnormalEffect(name="灼烧")
        sprite = MockSprite()
        apply_effect(effect, sprite, None, "A")
        assert sprite.get_stacks("灼烧") == 1

    def test_none_sprite_returns_empty(self):
        effect = TraitAbnormalEffect(name="中毒")
        result = apply_effect(effect, None, None, "A")
        assert result == []


# ── Mark Effect Tests ──

class TestMarkEffect:
    def test_apply_mark_on_enemy_team(self):
        effect = TraitMarkEffect(
            name="中毒印记", stacks=2, mark_target="opp_team"
        )
        sprite = MockSprite()
        battle = MockBattle()
        result = apply_effect(effect, sprite, battle, "A")
        assert len(result) == 1
        assert "中毒印记" in result[0]
        assert "+2" in result[0]
        # Check mark was applied to B team (opponent)
        marks, _ = battle.globals.get_marks("B")
        assert len(marks) >= 1

    def test_apply_mark_on_own_team(self):
        effect = TraitMarkEffect(
            name="攻击印记", stacks=1, mark_target="own_team"
        )
        sprite = MockSprite()
        battle = MockBattle()
        result = apply_effect(effect, sprite, battle, "A")
        assert "A方" in result[0]
        marks, _ = battle.globals.get_marks("A")
        assert len(marks) >= 1

    def test_apply_mark_no_battle(self):
        effect = TraitMarkEffect(name="减速", stacks=1)
        sprite = MockSprite()
        result = apply_effect(effect, sprite, None, "A")
        assert result == []


# ── Weather Effect Tests ──

class TestWeatherEffect:
    def test_set_rain(self):
        effect = TraitWeatherEffect(weather="rain", turns=5)
        sprite = MockSprite()
        battle = MockBattle()
        result = apply_effect(effect, sprite, battle, "A")
        assert len(result) == 1
        assert "rain" in result[0] or "天气" in result[0]
        assert battle.globals.weather == "rain"
        assert battle.globals.weather_turns == 5

    def test_default_turns(self):
        effect = TraitWeatherEffect(weather="snow")
        sprite = MockSprite()
        battle = MockBattle()
        apply_effect(effect, sprite, battle, "A")
        assert battle.globals.weather == "snow"
        assert battle.globals.weather_turns == 8

    def test_weather_no_battle(self):
        effect = TraitWeatherEffect(weather="rain")
        sprite = MockSprite()
        result = apply_effect(effect, sprite, None, "A")
        assert result == []


# ── Special Effect Tests ──

class TestSpecialEffect:
    def test_heal_by_value_pct(self):
        effect = TraitSpecialEffect(
            name="heal", value=Literal(0.5)
        )
        sprite = MockSprite(hp=200)
        sprite.current_hp = 50  # damaged (missing 150 HP)
        result = apply_effect(effect, sprite, None, "A")
        assert len(result) == 1
        assert "回复" in result[0]
        # 50% of max_hp=200 → heal 100, capped at missing 150 → actual heal 100
        assert sprite.current_hp == 150

    def test_heal_by_amount(self):
        effect = TraitSpecialEffect(
            name="heal", amount=Literal(30)
        )
        sprite = MockSprite(hp=100)
        sprite.current_hp = 50
        result = apply_effect(effect, sprite, None, "A")
        assert "回复" in result[0]
        # amount/max_hp = 30/200 = 0.15 pct, so heal = round(200 * 0.15) = 30
        assert sprite.current_hp == 80

    def test_direct_heal(self):
        effect = TraitSpecialEffect(
            name="direct_heal", amount=Literal(40)
        )
        sprite = MockSprite(hp=100)
        sprite.current_hp = 60
        result = apply_effect(effect, sprite, None, "A")
        assert "回复" in result[0]
        assert sprite.current_hp == 100

    def test_gain_energy(self):
        effect = TraitSpecialEffect(
            name="gain_energy", amount=Literal(3)
        )
        sprite = MockSprite(energy=5)
        result = apply_effect(effect, sprite, None, "A")
        assert "回复" in result[0]
        assert "E" in result[0]
        assert sprite.energy == 8

    def test_gain_energy_capped_at_max(self):
        effect = TraitSpecialEffect(
            name="gain_energy", amount=Literal(10)
        )
        sprite = MockSprite(energy=8)
        result = apply_effect(effect, sprite, None, "A")
        assert sprite.energy == 10
        assert "2" in result[0] or result[0].endswith("E")

    def test_lose_energy(self):
        effect = TraitSpecialEffect(
            name="lose_energy", amount=Literal(2)
        )
        sprite = MockSprite(energy=8)
        result = apply_effect(effect, sprite, None, "A")
        assert "-" in result[0]
        assert "E" in result[0]
        assert sprite.energy == 6

    def test_energy_set(self):
        effect = TraitSpecialEffect(
            name="energy_set", amount=Literal(5)
        )
        sprite = MockSprite(energy=8)
        result = apply_effect(effect, sprite, None, "A")
        assert "能量" in result[0]
        assert sprite.energy == 5

    def test_take_damage(self):
        effect = TraitSpecialEffect(
            name="take_damage", amount=Literal(25)
        )
        sprite = MockSprite(hp=100)
        result = apply_effect(effect, sprite, None, "A")
        assert "HP" in result[0]
        assert sprite.current_hp == 75

    def test_unknown_special_effect(self):
        effect = TraitSpecialEffect(name="unknown_thing")
        sprite = MockSprite()
        result = apply_effect(effect, sprite, None, "A")
        assert result == []

    def test_none_sprite_special(self):
        effect = TraitSpecialEffect(name="heal", amount=Literal(10))
        result = apply_effect(effect, None, None, "A")
        assert result == []


# ── Edge case tests ──

class TestEdgeCases:
    def test_none_sprite(self):
        effect = TraitStatEffect(stat="atk", steps=Literal(1))
        result = apply_effect(effect, None, None, "A")
        assert result == []

    def test_heal_at_full_hp(self):
        effect = TraitSpecialEffect(name="heal", amount=Literal(50))
        sprite = MockSprite(hp=200)  # at full HP
        result = apply_effect(effect, sprite, None, "A")
        # healed=0 → no event
        assert result == [] or "0" in str(result)

    def test_irvalue_literal_resolution(self):
        """Verify that Literal IRValues are resolved to their .value."""
        effect = TraitStatEffect(
            stat="sp_atk", steps=Literal(2), source="test"
        )
        sprite = MockSprite()
        apply_effect(effect, sprite, None, "A")
        se = sprite.effects[0]
        assert se.steps == 2
        assert se.stat_key == "sp_atk"


# ── Mark operation special effects ──

class TestMarkOperations:
    def test_dispel_mark(self):
        effect = TraitSpecialEffect(
            name="dispel_mark", amount=Literal(2), target_team="opp"
        )
        sprite = MockSprite()
        battle = MockBattle()
        # Add some marks to opp team first
        battle.globals.apply_mark("B", "中毒印记", "negative", 3)
        result = apply_effect(effect, sprite, battle, "A")
        assert len(result) >= 1
        assert "驱散" in result[0]

    def test_dispel_mark_no_battle(self):
        effect = TraitSpecialEffect(name="dispel_mark", amount=Literal(1))
        sprite = MockSprite()
        result = apply_effect(effect, sprite, None, "A")
        assert result == []

    def test_steal_mark(self):
        # First apply a mark to opp_team (B)
        effect_steal = TraitSpecialEffect(name="steal_mark", amount=Literal(1))
        sprite = MockSprite()
        battle = MockBattle()
        battle.globals.apply_mark("B", "减速", "negative", 2)
        result = apply_effect(effect_steal, sprite, battle, "A")
        assert len(result) >= 1
        assert "偷取" in result[0]

    def test_team_counter_add(self):
        effect = TraitSpecialEffect(
            name="team_counter_add", amount=Literal(3)
        )
        sprite = MockSprite()
        battle = MockBattle()
        # team_counter_add has no key field; should be silent
        result = apply_effect(effect, sprite, battle, "A")
        assert result == []

    def test_lives_delta(self):
        effect = TraitSpecialEffect(
            name="lives_delta", amount=Literal(1), target_team="own"
        )
        sprite = MockSprite()
        battle = MockBattle()
        result = apply_effect(effect, sprite, battle, "A")
        # Without a real player object, this should still not crash
        assert isinstance(result, list)
