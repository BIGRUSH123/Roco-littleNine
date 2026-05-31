"""backend/engine/ai/model.py — 对战状态评估 + 策略网络"""

from __future__ import annotations

from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F


class BattleValueNet(nn.Module):
    """State → value: 单头 value-only，监督学习用。

    +1 = 己方必胜，-1 = 对方必胜。
    """

    INPUT_DIM = 446

    def __init__(self, hidden: tuple[int, ...] = (256, 128), dropout: float = 0.0):
        super().__init__()
        layers: list[nn.Module] = []
        in_dim = self.INPUT_DIM
        for h in hidden:
            layers.append(nn.Linear(in_dim, h))
            layers.append(nn.ReLU())
            if dropout > 0:
                layers.append(nn.Dropout(dropout))
            in_dim = h
        layers.append(nn.Linear(in_dim, 1))
        layers.append(nn.Tanh())
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)

    @property
    def num_params(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)

    def save(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        torch.save({"state_dict": self.state_dict(), "hidden": self.hidden_dims}, path)

    @classmethod
    def load(cls, path: str, device: str = "cpu") -> "BattleValueNet":
        data = torch.load(path, map_location=device, weights_only=False)
        model = cls(hidden=data.get("hidden", (256, 128)))
        model.load_state_dict(data["state_dict"])
        model.to(device)
        model.eval()
        return model

    @property
    def hidden_dims(self) -> tuple[int, ...]:
        dims: list[int] = []
        for m in self.net:
            if isinstance(m, nn.Linear):
                dims.append(m.out_features)
        return tuple(dims[:-1])


class BattleNet(nn.Module):
    """双头网络：value [-1,1] + policy logits (10 维)。

    动作空间 (10):
      0-3 → 技能槽 0-3
      4-8 → 换宠到板凳 0-4
      9   → 使用道具
    """

    INPUT_DIM = 446
    NUM_ACTIONS = 10

    def __init__(self, hidden: tuple[int, ...] = (256, 128), dropout: float = 0.0):
        super().__init__()
        # 共享主干
        trunk: list[nn.Module] = []
        in_dim = self.INPUT_DIM
        for h in hidden:
            trunk.append(nn.Linear(in_dim, h))
            trunk.append(nn.ReLU())
            if dropout > 0:
                trunk.append(nn.Dropout(dropout))
            in_dim = h
        self.trunk = nn.Sequential(*trunk)

        # 双头
        last_hidden = hidden[-1] if hidden else self.INPUT_DIM
        self.value_head = nn.Sequential(nn.Linear(last_hidden, 1), nn.Tanh())
        self.policy_head = nn.Linear(last_hidden, self.NUM_ACTIONS)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """返回 (value, policy_logits)。"""
        shared = self.trunk(x)
        value = self.value_head(shared)
        logits = self.policy_head(shared)
        return value, logits

    def forward_with_mask(
        self, x: torch.Tensor, mask: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """返回 (value, masked_softmax_probs)。mask 中 1=可用, 0=禁用。"""
        value, logits = self.forward(x)
        # 将禁用动作的 logit 设为 -inf
        masked_logits = logits.masked_fill(mask == 0, -1e9)
        probs = F.softmax(masked_logits, dim=-1)
        return value, probs

    @property
    def num_params(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)

    def save(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        torch.save({
            "state_dict": self.state_dict(),
            "hidden": self.hidden_dims,
            "type": "BattleNet",
        }, path)

    @classmethod
    def load(cls, path: str, device: str = "cpu") -> "BattleNet":
        data = torch.load(path, map_location=device, weights_only=False)
        model = cls(hidden=data.get("hidden", (256, 128)))
        model.load_state_dict(data["state_dict"])
        model.to(device)
        model.eval()
        return model

    @property
    def hidden_dims(self) -> tuple[int, ...]:
        dims: list[int] = []
        for m in self.trunk:
            if isinstance(m, nn.Linear):
                dims.append(m.out_features)
        return tuple(dims)
