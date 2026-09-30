"""`--bc-init` 必须按检查点结构加载（带可选件的权重不能被默认结构接收）。

背景：v5/v6/v7 权重带 slot_pool / aux_heads / history，而 main() 原先用
`ModularBattleNet(...)` 新建默认结构再 load_state_dict → 直接缺键崩。
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

import torch


def test_bc_init_load_preserves_optional_parts(tmp_path):
    from backend.engine.ai.core.model import ModularBattleNet

    src = ModularBattleNet(trunk_dim=64, num_blocks=1, dropout=0.0,
                           slot_pool=True, aux_heads=True, aux_dim=18, history=2)
    path = tmp_path / "v5_like.pt"
    src.save(str(path))

    loaded = ModularBattleNet.load(str(path), device="cpu")
    assert (loaded.slot_pool, loaded.aux_heads, loaded.aux_dim, loaded.history) \
        == (True, True, 18, 2)

    # 反例：旧写法（默认结构 + load_state_dict）必须失败，证明这个修复是必要的
    plain = ModularBattleNet(trunk_dim=64, num_blocks=1, dropout=0.0)
    try:
        plain.load_state_dict(torch.load(path, map_location="cpu", weights_only=False)["state_dict"])
    except RuntimeError:
        pass
    else:  # pragma: no cover - 若将来结构变了不再缺键，这里会提醒
        raise AssertionError("默认结构居然能吃下带可选件的权重？该测试的前提变了")
