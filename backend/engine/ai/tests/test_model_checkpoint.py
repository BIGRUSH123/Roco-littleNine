"""检查点格式契约 — 裸 state_dict 与标准 save() 都必须能被 ModularBattleNet.load 读取。

背景（2026-09-20 实测）：`bc_pretrain` 用 `torch.save(model.state_dict(), out)` 存裸
state_dict，而 `evaluate_checkpoints` / 部署路径（advisor、service agent）统一走
`ModularBattleNet.load`（期望 `{"state_dict": ...}`）——BC 权重一加载就
`KeyError: 'state_dict'`，既评估不了也部署不了；只有 `train.py --bc-init` 能用
（它自己做裸 load）。本文件把两种格式都可读这个契约锁死。
"""

from __future__ import annotations

import sys
from pathlib import Path

import torch

_PROJ = Path(__file__).resolve().parents[4]
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.engine.ai.core.model import ModularBattleNet
from backend.engine.ai.core.vocab import VOCAB_SIZE


def _model() -> ModularBattleNet:
    """与 bc_init.pt 同构（trunk 256 / 4 blocks / attention）。"""
    return ModularBattleNet(trunk_dim=256, num_blocks=4, dropout=0.0,
                            vocab_size=VOCAB_SIZE, with_attention=True)


def test_load_standard_save_format(tmp_path):
    model = _model()
    path = tmp_path / "std.pt"
    model.save(str(path))

    loaded = ModularBattleNet.load(str(path), device="cpu")
    assert loaded.trunk_dim == 256
    for key, value in model.state_dict().items():
        assert torch.equal(loaded.state_dict()[key], value), key


def test_load_raw_state_dict(tmp_path):
    """裸 state_dict（bc_pretrain 旧格式）也必须能读，否则评估/部署全断。"""
    model = _model()
    path = tmp_path / "raw.pt"
    torch.save(model.state_dict(), path)

    loaded = ModularBattleNet.load(str(path), device="cpu")
    for key, value in model.state_dict().items():
        assert torch.equal(loaded.state_dict()[key], value), key


def test_shipped_bc_init_is_loadable():
    """当前交付的 checkpoints/bc_init.pt 必须能被标准加载器读取。"""
    path = _PROJ / "checkpoints" / "bc_init.pt"
    if not path.exists():
        import pytest
        pytest.skip("bc_init.pt 不存在（尚未做 BC 预训练）")
    loaded = ModularBattleNet.load(str(path), device="cpu")
    assert loaded.num_params > 0
