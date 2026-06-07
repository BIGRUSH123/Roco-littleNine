"""终局裁决单元测试。"""

from backend.engine.ai.core.outcome import (
    DEFAULT_DRAW_MARGIN,
    battle_outcome_a,
    eval_score_for_candidate,
    format_reason_counts,
    merge_reason_counts,
)
from backend.engine.ai.train import _load_sprite_skills
from backend.sim.factory import SimFactory


def _battle_with_hp(
    a_hp: list[int],
    b_hp: list[int],
    *,
    cap_hp: int = 300,
) -> object:
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()
    names = sorted(sprite_skills.keys())[:2]
    team_a = [{"name": names[0], "skills": sprite_skills[names[0]][:4]}]
    team_b = [{"name": names[1], "skills": sprite_skills[names[1]][:4]}]
    p1 = factory.build_player("A", team_a)
    p2 = factory.build_player("B", team_b)
    battle = factory.build_battle(p1, p2)
    for i, hp in enumerate(a_hp):
        sprite = battle.player_a.team[i]
        sprite.max_hp = cap_hp
        sprite.current_hp = min(hp, cap_hp)
    for i, hp in enumerate(b_hp):
        sprite = battle.player_b.team[i]
        sprite.max_hp = cap_hp
        sprite.current_hp = min(hp, cap_hp)
    return battle


def test_eval_score_for_candidate():
    assert eval_score_for_candidate(1.0, True) == 1.0
    assert eval_score_for_candidate(-1.0, True) == 0.0
    assert eval_score_for_candidate(0.0, True) == 0.5
    assert eval_score_for_candidate(-1.0, False) == 1.0
    assert eval_score_for_candidate(1.0, False) == 0.0
    assert eval_score_for_candidate(0.0, False) == 0.5


def test_battle_outcome_decisive_winner():
    battle = _battle_with_hp([50], [50])
    battle.winner = "A"
    outcome, reason = battle_outcome_a(battle, max_turns=100)
    assert outcome == 1.0
    assert reason == "decisive_a"


def test_battle_outcome_max_turns_adjudication():
    battle = _battle_with_hp([250], [20])
    battle.turn = 100
    outcome, reason = battle_outcome_a(
        battle, max_turns=100, draw_margin=DEFAULT_DRAW_MARGIN,
    )
    assert outcome == 1.0
    assert reason == "max_turns_a"


def test_battle_outcome_max_turns_draw_when_close():
    battle = _battle_with_hp([150], [148])
    battle.turn = 100
    outcome, reason = battle_outcome_a(
        battle, max_turns=100, draw_margin=0.15,
    )
    assert outcome == 0.0
    assert reason == "max_turns_draw"


def test_merge_and_format_reason_counts():
    merged = merge_reason_counts({"max_turns_a": 2}, {"decisive_b": 1})
    assert merged == {"max_turns_a": 2, "decisive_b": 1}
    text = format_reason_counts(merged)
    assert "decisive_b=1" in text
    assert "max_turns_a=2" in text
