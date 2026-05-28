"""Debug: verify the mult_mod → display effect pipeline end-to-end."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from backend.sim.factory import SimFactory


def main():
    # Create a battle with a sprite that has the 囤积 trait
    factory = SimFactory()

    # Find a sprite with 囤积 trait
    # Let's check what sprites have ability "囤积"
    from backend.common.models import load_species_index
    species_map = load_species_index()

    hoarding_sprites = [
        name for name, sp in species_map.items()
        if getattr(sp, 'ability', '') == '囤积'
    ]
    print(f"Sprites with 囤积 trait: {hoarding_sprites}")

    if not hoarding_sprites:
        print("No sprites found with 囤积 trait!")
        return

    # Pick the first one and create a battle
    sprite_name = hoarding_sprites[0]
    print(f"Using sprite: {sprite_name}")

    # Create a simple 1v1 battle
    from backend.sim.battle import Battle
    from backend.sim.player import Player, PlayStyle

    species = species_map[sprite_name]
    print(f"  ability: {species.ability}")
    print(f"  ability_id: {getattr(species, 'ability_id', 'N/A')}")

    # Load sprite via factory
    sprite = factory.create_sprite(sprite_name, bloodline="")
    print(f"  sprite created: {sprite.name}")
    print(f"  sprite.species.ability: {sprite.species.ability}")
    print(f"  initial energy: {sprite.energy}")
    print(f"  initial _modifiers: {sprite._modifiers}")

    # Check if active_effects already has our display effect
    from backend.vm.effect import StatBuffEffect
    display_effects = [
        e for e in sprite.active_effects
        if isinstance(e, StatBuffEffect) and e.steps == 0 and e.display_mult is not None
    ]
    print(f"  display-only StatBuffEffects (before): {len(display_effects)}")
    for e in display_effects:
        print(f"    stat_key={e.stat_key}, source={e.source}, display_mult={e.display_mult}, display_name={e.display_name}")

    # Now simulate: trait loader should register observers, then energy change should trigger
    from backend.engine.trait_loader import TraitLoader
    loader = TraitLoader()
    loader.load_for_sprite(sprite)

    print(f"  after trait load, _modifiers: {sprite._modifiers}")

    display_effects = [
        e for e in sprite.active_effects
        if isinstance(e, StatBuffEffect) and e.steps == 0 and e.display_mult is not None
    ]
    print(f"  display-only StatBuffEffects (after load): {len(display_effects)}")
    for e in display_effects:
        print(f"    stat_key={e.stat_key}, source={e.source}, display_mult={e.display_mult}, display_name={e.display_name}")

    # Now trigger energy change to fire the observer
    print(f"\n  -- Changing energy from {sprite.energy}...")
    # The observer fires on energy_changed. Let's simulate by changing energy directly
    # and manually firing the observer

    # Check if observer was registered
    print("  ObserverRegistry has observers? (need battle context)")

    # Let's try a full battle simulation
    print("\n--- Full battle test ---")

    p1 = Player("Test", [sprite], play_style=PlayStyle.MANUAL)
    # Create a dummy opponent
    opp = factory.create_sprite("蹦蹦花", bloodline="")
    p2 = Player("Opp", [opp], play_style=PlayStyle.MANUAL)

    battle = Battle(p1, p2)
    print(f"  Battle created, turn={battle.turn}")
    print(f"  Active sprite: {battle.player_a.active.name}")
    print(f"  Energy: {battle.player_a.active.energy}")
    print(f"  _modifiers: {battle.player_a.active._modifiers}")

    ae = battle.player_a.active.active_effects
    display_effects = [
        e for e in ae
        if isinstance(e, StatBuffEffect) and e.steps == 0 and e.display_mult is not None
    ]
    print(f"  display effects: {len(display_effects)}")
    for e in display_effects:
        print(f"    {e.stat_key} source={e.source} display_mult={e.display_mult}")

if __name__ == '__main__':
    main()
