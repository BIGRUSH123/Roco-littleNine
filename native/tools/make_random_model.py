# -*- coding: utf-8 -*-
"""make_random_model.py — 生成同结构随机初始化模型作为校准基线。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import torch  # noqa: E402

from backend.engine.ai.core.model import ModularBattleNet  # noqa: E402
from backend.engine.ai.core.vocab import VOCAB_SIZE  # noqa: E402

torch.manual_seed(20260919)
m = ModularBattleNet(
    trunk_dim=256, num_blocks=4, dropout=0.1,
    vocab_size=VOCAB_SIZE, with_attention=True,
)
m.eval()
out = ROOT / "checkpoints" / "exp17_deliver" / "random_init.pt"
out.parent.mkdir(parents=True, exist_ok=True)
m.save(str(out))
print("saved", out)
