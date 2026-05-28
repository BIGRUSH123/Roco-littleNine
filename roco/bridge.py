"""roco.bridge — VM-to-SDK bridge layer.

Connects the mutable sim types (Battle, Player, Sprite) to the
immutable SDK types (BattleObservation, Action, SpriteSnapshot).
Pure functions, no side effects, directly testable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from roco.ai.observation import Action as SdkAction
from roco.ai.observation import ActionKind, BattleObservation, SpriteSnapshot

if TYPE_CHECKING:
    from backend.sim.action import Action as SimAction
    from backend.sim.agent import Agent as SimAgent
    from backend.sim.battle import Battle
    from backend.sim.player import Player
    from backend.sim.sprite import Sprite
    from roco.ai.agent import BattleAgent


def build_observation(battle: Battle, team: str) -> BattleObservation:
    """Build a BattleObservation from the current battle state.

    Extracts only publicly visible information — no hidden data
    (e.g. opponent's exact energy cost modifiers are not exposed).
    """
    player = _get_player(battle, team)
    opponent = _get_opponent(battle, team)

    my_sprite = _build_sprite_snapshot(player.active)
    opp_sprite = _build_sprite_snapshot(opponent.active)

    my_team = [_build_sprite_snapshot(s) for s in player.team]
    opp_team_size = len(opponent.team)

    field_effects = []
    g = battle.globals
    if g.weather:
        field_effects.append(f"weather:{g.weather}")

    legal_actions = _compute_legal_actions(battle, team)

    return BattleObservation(
        my_sprite=my_sprite,
        opp_sprite=opp_sprite,
        weather=g.weather,
        field_effects=field_effects,
        turn_number=battle.turn,
        legal_actions=legal_actions,
        my_team=my_team,
        opp_team_size=opp_team_size,
    )


def legal_actions_filter(battle: Battle, team: str) -> list[SdkAction]:
    """Compute legal actions for a team from the current battle state."""
    return _compute_legal_actions(battle, team)


def adapt_agent(agent: BattleAgent, team: str) -> SimAgent:
    """Wrap an SDK BattleAgent to match the sim Agent protocol.

    Returns an adapter object compatible with Battle.run().
    """

    class _Adapter:
        def choose_lead(self, battle: Battle) -> int:
            player = _get_player(battle, team)
            alive = [i for i, s in enumerate(player.team) if not s.is_fainted]
            if not alive:
                return 0
            if len(alive) == 1:
                return alive[0]

            obs = build_observation(battle, team)
            choices = [
                SdkAction.switch(i) for i in alive
            ]
            result = agent.select_action(obs, choices)
            if result.kind == ActionKind.SWITCH and result.switch_index in alive:
                return result.switch_index
            return alive[0]

        def choose_action(self, battle: Battle) -> SimAction:
            obs = build_observation(battle, team)
            result = agent.select_action(obs, obs.legal_actions)
            return _to_sim_action(result)

        def choose_replacement(self, battle: Battle) -> int:
            player = _get_player(battle, team)
            alive = [
                i for i in player.alive_sprites
                if i != player.active_index
            ]
            if not alive:
                return -1
            if len(alive) == 1:
                return alive[0]

            obs = build_observation(battle, team)
            choices = [SdkAction.switch(i) for i in alive]
            result = agent.select_action(obs, choices)
            if result.kind == ActionKind.SWITCH and result.switch_index in alive:
                return result.switch_index
            return alive[0]

        def on_game_end(self, winner: str) -> None:
            pass

    adapter = _Adapter()
    adapter.team = team
    return adapter


# ── internal helpers ──


def _get_player(battle: Battle, team: str) -> Player:
    return battle.player_a if team == "A" else battle.player_b


def _get_opponent(battle: Battle, team: str) -> Player:
    return battle.player_b if team == "A" else battle.player_a


def _build_sprite_snapshot(sprite: Sprite) -> SpriteSnapshot:
    """Extract publicly visible sprite state."""
    skills = []
    for sk in sprite.skills:
        name = getattr(sk, 'name', '') or str(sk)
        skills.append(name)

    from backend.vm.effect import AbnormalEffect, StatBuffEffect, StateEffect
    status: list[str] = []
    buffs: dict[str, int] = {}
    for e in getattr(sprite, 'active_effects', []):
        if isinstance(e, AbnormalEffect):
            status.append(f"{e.name}×{e.stacks}" if e.stacks > 1 else e.name)
        elif isinstance(e, StatBuffEffect):
            label = f"{e.stat_key}{'+' if e.steps > 0 else ''}{e.steps}" if e.stat_key else e.name
            buffs[label] = buffs.get(label, 0) + e.steps
        elif isinstance(e, StateEffect):
            status.append(e.name)

    element = getattr(sprite.species, 'element', '') if hasattr(sprite, 'species') else ''

    return SpriteSnapshot(
        name=sprite.species.name if hasattr(sprite, 'species') else str(sprite),
        current_hp=sprite.current_hp,
        max_hp=sprite.max_hp,
        current_ep=sprite.energy,
        max_ep=10,
        element=element,
        status=status,
        buffs=buffs,
        available_skills=skills,
        is_fainted=sprite.is_fainted,
    )


def _compute_legal_actions(battle: Battle, team: str) -> list[SdkAction]:
    """Enumerate all legal actions for a team from the current battle state."""
    player = _get_player(battle, team)
    sprite = player.active
    actions: list[SdkAction] = []

    if sprite.is_fainted:
        for i in player.alive_sprites:
            if i != player.active_index:
                actions.append(SdkAction.switch(i))
        if not actions:
            actions.append(SdkAction.passthrough())
        return actions

    for i, skill in enumerate(sprite.skills):
        if skill.cooldown > 0:
            continue
        if getattr(skill, 'sealed', False):
            continue
        cost = getattr(skill, 'energy_cost', 0)
        if cost > sprite.energy:
            continue
        actions.append(SdkAction.skill(i))

    actions.append(SdkAction.gather())

    for i in player.alive_sprites:
        if i != player.active_index:
            actions.append(SdkAction.switch(i))

    item = player.item
    if item and item.can_use(battle.turn):
        actions.append(SdkAction.item())

    actions.append(SdkAction.passthrough())
    return actions


def _to_sim_action(action: SdkAction) -> SimAction:
    """Convert SDK Action to sim Action."""
    from backend.sim.action import Action as SimAction

    kind_map = {
        ActionKind.SKILL: "skill",
        ActionKind.SWITCH: "switch",
        ActionKind.GATHER: "gather",
        ActionKind.ITEM: "item",
        ActionKind.PASS: "gather",
    }
    return SimAction(
        kind=kind_map.get(action.kind, "gather"),
        skill_index=action.skill_index if action.kind == ActionKind.SKILL else None,
        switch_index=action.switch_index if action.kind == ActionKind.SWITCH else None,
    )
