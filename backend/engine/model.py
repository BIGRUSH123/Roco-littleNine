"""backend/engine/model.py — 对战状态评估 MLP 网络"""

from __future__ import annotations

import torch
import torch.nn as nn


class BattleValueNet(nn.Module):
    """State → value: 输入 (446,) 对战状态向量，输出 [-1, 1] 胜率评估。

    +1 = 己方必胜，-1 = 对方必胜，0 = 均势。
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
        return tuple(dims[:-1])  # exclude final 1-dim layer
