"""backend/engine/test_serializer.py — 序列化往返测试"""

import pytest


class TestEffectSerialization:
    """Effect to_dict -> from_dict round-trip tests."""

    def test_stat_buff_roundtrip(self):
        from backend.engine.serializer import effect_from_dict, effect_to_dict
        from backend.vm.effect import StatBuffEffect

        original = StatBuffEffect(
            name="atk", source="专注力", scope="battlefield",
            stat_key="atk", steps=3, display_mult=0.3,
        )
        d = effect_to_dict(original)
        restored = effect_from_dict(d)
        assert isinstance(restored, StatBuffEffect)
        assert restored.name == "atk"
        assert restored.stat_key == "atk"
        assert restored.steps == 3
        assert restored.display_mult == 0.3
        assert restored.source == "专注力"

    def test_stat_buff_inherent(self):
        from backend.engine.serializer import effect_from_dict, effect_to_dict
        from backend.vm.effect import StatBuffEffect

        original = StatBuffEffect(
            name="speed", source="trait", stat_key="speed",
            steps=1, is_inherent=True,
        )
        d = effect_to_dict(original)
        assert d["is_inherent"] is True
        restored = effect_from_dict(d)
        assert restored.is_inherent is True

    def test_abnormal_roundtrip(self):
        from backend.engine.serializer import effect_from_dict, effect_to_dict
        from backend.vm.effect import AbnormalEffect

        original = AbnormalEffect(
            name="灼烧", source="skill", scope="battlefield",
            stacks=3, tick_damage_pct=0.03, tick_element="火",
            max_stacks=10,
        )
        d = effect_to_dict(original)
        restored = effect_from_dict(d)
        assert isinstance(restored, AbnormalEffect)
        assert restored.name == "灼烧"
        assert restored.stacks == 3
        assert restored.tick_damage_pct == 0.03

    def test_state_roundtrip(self):
        from backend.engine.serializer import effect_from_dict, effect_to_dict
        from backend.vm.effect import StateEffect

        original = StateEffect(
            name="charging", source="skill", scope="turn",
            state_type="charging", params={"skill": "火焰拳"},
        )
        d = effect_to_dict(original)
        restored = effect_from_dict(d)
        assert isinstance(restored, StateEffect)
        assert restored.state_type == "charging"
        assert restored.params == {"skill": "火焰拳"}

    def test_modifier_roundtrip(self):
        from backend.engine.serializer import effect_from_dict, effect_to_dict
        from backend.vm.effect import ModifierEffect

        original = ModifierEffect(
            name="max_energy", source="trait", attr="max_energy",
            value=12.0, mode="set",
        )
        d = effect_to_dict(original)
        restored = effect_from_dict(d)
        assert isinstance(restored, ModifierEffect)
        assert restored.attr == "max_energy"
        assert restored.value == 12.0

    def test_mark_roundtrip(self):
        from backend.engine.serializer import effect_from_dict, effect_to_dict
        from backend.vm.effect import MarkEffect

        original = MarkEffect(
            name="星陨印记", source="星陨", scope="persistent",
            stacks=5, category="negative", starfall_damage=30,
        )
        d = effect_to_dict(original)
        restored = effect_from_dict(d)
        assert isinstance(restored, MarkEffect)
        assert restored.name == "星陨印记"
        assert restored.stacks == 5
        assert restored.starfall_damage == 30

    def test_observer_roundtrip(self):
        from backend.engine.serializer import effect_from_dict, effect_to_dict
        from backend.vm.effect import ObserverEffect

        original = ObserverEffect(
            name="test_obs", source="trait", scope="battlefield",
            cond={"trigger": "post_damage"}, then=[],
            listen=frozenset(["post_damage"]), threshold=2,
            reset_on_fire=False,
        )
        d = effect_to_dict(original)
        restored = effect_from_dict(d)
        assert isinstance(restored, ObserverEffect)
        assert restored.name == "test_obs"
        assert restored.threshold == 2
        assert restored.reset_on_fire is False
        assert "post_damage" in restored.listen

    def test_unknown_type_raises(self):
        from backend.engine.serializer import effect_from_dict
        with pytest.raises(ValueError, match="Unknown effect type"):
            effect_from_dict({"_type": "NonExistent"})


class TestBattleSerialization:
    """Full battle to_dict -> from_dict round-trip."""

    @pytest.fixture
    def factory(self):
        from backend.sim.factory import SimFactory
        return SimFactory()

    @pytest.fixture
    def battle(self, factory):
        """Create a simple battle with two sprites."""
        p1 = factory.build_player("训练师A", [
            {"name": "水灵"},
        ])
        p2 = factory.build_player("训练师B", [
            {"name": "草衣虫"},
        ])
        return factory.build_battle(p1, p2)

    def test_battle_roundtrip_no_turns(self, battle, factory):
        """Round-trip before any turns are executed."""
        from backend.engine.serializer import battle_from_dict, battle_to_dict

        data = battle_to_dict(battle)
        restored = battle_from_dict(data, factory.sprite_db, factory._build_skill_list)

        assert restored.turn == battle.turn
        assert restored.player_a.name == battle.player_a.name
        assert restored.player_b.name == battle.player_b.name
        assert restored.globals.weather == battle.globals.weather
        assert len(restored.player_a.team) == len(battle.player_a.team)
        assert restored.player_a.team[0].name == battle.player_a.team[0].name
        assert restored.player_a.team[0].current_hp == battle.player_a.team[0].current_hp

    def test_battle_roundtrip_after_turn(self, battle, factory):
        """Execute one turn, then round-trip."""
        from backend.engine.serializer import battle_from_dict, battle_to_dict
        from backend.sim.agent import RuleAgent

        agent_a = RuleAgent("A", battle.player_a)
        agent_b = RuleAgent("B", battle.player_b)
        battle.execute_turn(agent_a, agent_b)

        data = battle_to_dict(battle)
        restored = battle_from_dict(data, factory.sprite_db, factory._build_skill_list)

        assert restored.turn == battle.turn
        assert len(restored.log) == len(battle.log)
        assert restored.log[0].turn == battle.log[0].turn
        assert restored.log[0].sprite_a == battle.log[0].sprite_a

    def test_battle_version_field(self, battle):
        """Verify battle_to_dict includes version/type metadata."""
        from backend.engine.serializer import battle_to_dict

        data = battle_to_dict(battle)
        assert data["version"] == "1.0"
        assert data["type"] == "match"
        assert "player_a" in data
        assert "player_b" in data
        assert "globals" in data
        assert "vm_state" in data


class TestSnapshotRestore:
    """Snapshot and restore workflow tests."""

    @pytest.fixture
    def factory(self):
        from backend.sim.factory import SimFactory
        return SimFactory()

    @pytest.fixture
    def battle(self, factory):
        p1 = factory.build_player("训练师A", [
            {"name": "水灵"},
        ])
        p2 = factory.build_player("训练师B", [
            {"name": "草衣虫"},
        ])
        return factory.build_battle(p1, p2)

    def test_snapshot_at_turn_zero(self, battle):
        """Snapshot at turn 0 should exist after construction (via entry)."""
        battle.save_snapshot()
        assert 0 in battle.snapshots
        snap = battle.snapshots[0]
        assert snap["turn"] == 0

    def test_snapshot_and_restore(self, battle):
        """Save snapshot at turn 0, run turns, restore back."""
        from backend.sim.agent import RuleAgent

        agent_a = RuleAgent("A", battle.player_a)
        agent_b = RuleAgent("B", battle.player_b)

        battle.save_snapshot()
        hp_before = battle.player_a.team[0].current_hp
        energy_before = battle.player_a.team[0].energy

        for _ in range(3):
            if battle.is_finished:
                break
            battle.execute_turn(agent_a, agent_b)

        assert battle.turn >= 1

        battle.restore_snapshot(0)

        assert battle.turn == 0
        assert len(battle.log) == 0
        assert battle.player_a.team[0].current_hp == hp_before
        assert battle.player_a.team[0].energy == energy_before

    def test_restore_invalid_turn_raises(self, battle):
        with pytest.raises(ValueError, match="快照不存在"):
            battle.restore_snapshot(999)

    def test_clear_snapshots(self, battle):
        battle.save_snapshot()
        assert len(battle.snapshots) > 0
        battle.clear_snapshots()
        assert len(battle.snapshots) == 0

    def test_snapshots_discard_future(self, battle):
        """Restoring to an earlier turn discards snapshots after it."""
        from backend.sim.agent import RuleAgent

        agent_a = RuleAgent("A", battle.player_a)
        agent_b = RuleAgent("B", battle.player_b)

        for _ in range(3):
            if battle.is_finished:
                break
            battle.execute_turn(agent_a, agent_b)

        assert 0 in battle.snapshots
        battle.restore_snapshot(1)
        assert 0 in battle.snapshots
        assert 1 in battle.snapshots
        assert 2 not in battle.snapshots
        assert 3 not in battle.snapshots
