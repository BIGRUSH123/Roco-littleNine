"""魔力（lives）默认值必须只有一处来源。

背景：历史上 `Player.lives` 默认 4，而 `Ctx` / `snapshot.build_ctx` / `battle`
的兜底各写了 5 —— 一旦有路径吃到默认值就会比真实魔力多 1，而
`battle_outcome_a` 的 margin 里恰好有 `lives * 0.25` 这一项（静默偏一点）。
"""
from __future__ import annotations

import inspect
import sys

sys.path.insert(0, ".")

from backend.common.constants import DEFAULT_LIVES  # noqa: E402


def test_lives_default_single_source():
    from backend.engine.snapshot import build_ctx
    from backend.sim.factory import SimFactory
    from backend.sim.player import Player
    from backend.vm.ctx import Ctx

    assert DEFAULT_LIVES == 4
    assert Player.__dataclass_fields__["lives"].default == DEFAULT_LIVES
    assert Ctx.__dataclass_fields__["lives_own"].default == DEFAULT_LIVES
    assert Ctx.__dataclass_fields__["lives_opp"].default == DEFAULT_LIVES
    assert inspect.signature(build_ctx).parameters["lives_own"].default == DEFAULT_LIVES
    assert inspect.signature(build_ctx).parameters["lives_opp"].default == DEFAULT_LIVES
    assert inspect.signature(SimFactory.build_player).parameters["lives"].default == DEFAULT_LIVES


def test_serializer_lives_fallback_uses_constant():
    from backend.engine import serializer

    src = inspect.getsource(serializer.player_from_dict)
    assert 'get("lives", DEFAULT_LIVES)' in src, "serializer 的 lives 兜底应走 DEFAULT_LIVES"
