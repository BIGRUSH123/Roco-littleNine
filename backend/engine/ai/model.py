"""backend/engine/ai/model.py — 对战状态评估 + 策略网络

模型架构:
  BattleValueNet     — 单头 value-only，监督学习用
  BattleNet          — 双头 MLP（原始版本，简单快速）
  ModularBattleNet   — 模块化双头网络（v2，更强表达力）
"""

from __future__ import annotations

import math
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F


# ═══════════════════════════════════════════════════════════════════
# 基础组件
# ═══════════════════════════════════════════════════════════════════

class ResidualBlock(nn.Module):
    """带 LayerNorm 的残差 MLP 块。

    与 AlphaZero 的 Conv+BN 残差块不同，我们使用 LayerNorm+Linear，
    更适合非空间结构化向量输入。
    """

    def __init__(self, dim: int, dropout: float = 0.0):
        super().__init__()
        self.net = nn.Sequential(
            nn.LayerNorm(dim),
            nn.Linear(dim, dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(dim, dim),
            nn.Dropout(dropout),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + self.net(x)


class MutualCrossAttention(nn.Module):
    """己方 ↔ 对方精灵特征的互注意力。

    让网络学习"我的精灵如何对抗对方的"以及"对方精灵如何威胁我的"。
    多头注意力（默认4头），残差连接。
    """

    def __init__(self, dim: int, num_heads: int = 4, dropout: float = 0.0):
        super().__init__()
        self.num_heads = num_heads
        self.head_dim = dim // num_heads
        assert self.head_dim * num_heads == dim, "dim 必须能被 num_heads 整除"

        self.q_proj = nn.Linear(dim, dim)
        self.k_proj = nn.Linear(dim, dim)
        self.v_proj = nn.Linear(dim, dim)
        self.out_proj = nn.Linear(dim, dim)
        self.dropout = nn.Dropout(dropout)
        self.scale = math.sqrt(self.head_dim)

    def _attend(self, query: torch.Tensor, key_value: torch.Tensor) -> torch.Tensor:
        """单方向注意力：query 关注 key_value。"""
        B = query.shape[0]
        H, D = self.num_heads, self.head_dim

        q = self.q_proj(query).view(B, H, D).transpose(0, 1)        # (H, B, D)
        k = self.k_proj(key_value).view(B, H, D).transpose(0, 1)
        v = self.v_proj(key_value).view(B, H, D).transpose(0, 1)

        attn = (q @ k.transpose(-2, -1)) / self.scale               # (H, B, B)
        attn = F.softmax(attn, dim=-1)
        attn = self.dropout(attn)

        out = (attn @ v).transpose(0, 1).reshape(B, H * D)          # (B, dim)
        return query + self.out_proj(out)

    def forward(self, own_features: torch.Tensor, opp_features: torch.Tensor):
        """返回 (own_attended, opp_attended)。"""
        own_out = self._attend(own_features, opp_features)
        opp_out = self._attend(opp_features, own_features)
        return own_out, opp_out


# ═══════════════════════════════════════════════════════════════════
# 模型定义
# ═══════════════════════════════════════════════════════════════════

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
    """双头网络：value [-1,1] + policy logits (11 维)。

    动作空间 (11):
      0-3 → 技能槽 0-3
      4-8 → 换宠到板凳 0-4
      9   → 聚能
      10  → 使用道具
    """

    INPUT_DIM = 446
    NUM_ACTIONS = 11

    def __init__(self, hidden: tuple[int, ...] = (256, 128), dropout: float = 0.0):
        super().__init__()
        self.dropout = float(dropout)
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
            "dropout": self.dropout,
            "type": "BattleNet",
        }, path)

    @classmethod
    def load(cls, path: str, device: str = "cpu") -> "BattleNet":
        data = torch.load(path, map_location=device, weights_only=False)
        model = cls(
            hidden=data.get("hidden", (256, 128)),
            dropout=float(data.get("dropout", 0.0)),
        )
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


class ModularBattleNet(nn.Module):
    """模块化双头网络：分解编码 + 交叉注意力 + 残差主干。

    与 BattleNet 对比:
      BattleNet:  446 → 256 → 128 → value(1) + policy(11)   (~150K 参数)
      v2:         模块编码 → 交叉注意力 → 残差塔(4块) → 独立双头  (~800K 参数，可调)

    设计来源:
      - AlphaZero:    残差连接防止深层梯度消失
      - Transformer:  多头交叉注意力建模己方↔对方精灵交互
      - Pokemon AI:   模块化状态分解（全局/场上/板凳分离编码）

    状态分解 (446 维):
      [0:56]   全局状态（天气/印记/魔力/道具/奉献）
      [56:171] 己方场上精灵
      [171:286]对方场上精灵
      [286:366]己方板凳 ×5
      [366:446]对方板凳 ×5
    """

    INPUT_DIM = 446
    NUM_ACTIONS = 11  # 技能0-3 + 换宠4-8 + 聚能9 + 道具10

    # 各模块维度（需与 encode.py 一致）
    GLOBAL_DIM = 56
    SPRITE_DIM = 115
    BENCH_DIM = 80

    def __init__(
        self,
        trunk_dim: int = 256,
        num_blocks: int = 4,
        dropout: float = 0.1,
        *,
        with_attention: bool = True,
    ):
        super().__init__()
        self.trunk_dim = trunk_dim
        self.num_blocks = num_blocks
        self.dropout_val = float(dropout)
        self.with_attention = with_attention

        # ── 模块化编码器 ──
        self.global_enc = nn.Sequential(
            nn.Linear(self.GLOBAL_DIM, 64),
            nn.LayerNorm(64),
            nn.GELU(),
        )
        # 共用精灵编码器（己方和对方结构相同）
        self.sprite_enc = nn.Sequential(
            nn.Linear(self.SPRITE_DIM, 128),
            nn.LayerNorm(128),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(128, 128),
            nn.LayerNorm(128),
            nn.GELU(),
        )
        # 共用板凳编码器
        self.bench_enc = nn.Sequential(
            nn.Linear(self.BENCH_DIM, 64),
            nn.LayerNorm(64),
            nn.GELU(),
        )

        # ── 交叉注意力 ──
        if self.with_attention:
            self.cross_attn = MutualCrossAttention(128, num_heads=4, dropout=dropout)

        # ── 特征融合 ──
        # 全局(64) + 己方精灵(128) + 对方精灵(128) + 己方板凳(64) + 对方板凳(64) = 448
        fusion_in = 64 + 128 + 128 + 64 + 64  # 448
        self.fusion = nn.Sequential(
            nn.Linear(fusion_in, trunk_dim),
            nn.LayerNorm(trunk_dim),
            nn.GELU(),
        )

        # ── 残差塔 ──
        self.blocks = nn.ModuleList([
            ResidualBlock(trunk_dim, dropout) for _ in range(num_blocks)
        ])

        # ── 价值头（更深的独立路径） ──
        self.value_head = nn.Sequential(
            nn.Linear(trunk_dim, trunk_dim // 2),
            nn.LayerNorm(trunk_dim // 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(trunk_dim // 2, trunk_dim // 4),
            nn.LayerNorm(trunk_dim // 4),
            nn.GELU(),
            nn.Linear(trunk_dim // 4, 1),
            nn.Tanh(),
        )

        # ── 策略头（分离：技能/换宠/聚能/道具 四个独立子头） ──
        # 各子头有独立参数，梯度隔离 → 网络学会"技能排名"和"是否换宠"是不同尺度的问题
        policy_hidden = max(trunk_dim // 4, 32)

        self.skill_head = nn.Sequential(
            nn.Linear(trunk_dim, policy_hidden),
            nn.LayerNorm(policy_hidden),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(policy_hidden, 4),
        )
        self.switch_head = nn.Sequential(
            nn.Linear(trunk_dim, policy_hidden),
            nn.LayerNorm(policy_hidden),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(policy_hidden, 5),
        )
        self.gather_head = nn.Sequential(
            nn.Linear(trunk_dim, policy_hidden),
            nn.LayerNorm(policy_hidden),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(policy_hidden, 1),
        )
        self.item_head = nn.Sequential(
            nn.Linear(trunk_dim, policy_hidden),
            nn.LayerNorm(policy_hidden),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(policy_hidden, 1),
        )

        self._init_weights()

    def _init_weights(self):
        """Xavier 初始化线性层。"""
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.xavier_uniform_(m.weight)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)

    def _decompose(self, x: torch.Tensor):
        """将 (B, 446) 输入分解为各模块张量。"""
        g = x[:, :self.GLOBAL_DIM]                                         # (B, 56)
        own = x[:, self.GLOBAL_DIM:self.GLOBAL_DIM + self.SPRITE_DIM]     # (B, 115)
        opp_start = self.GLOBAL_DIM + self.SPRITE_DIM                     # 171
        opp = x[:, opp_start:opp_start + self.SPRITE_DIM]                  # (B, 115)
        bench_own_start = opp_start + self.SPRITE_DIM                     # 286
        bench_own = x[:, bench_own_start:bench_own_start + self.BENCH_DIM]  # (B, 80)
        bench_opp_start = bench_own_start + self.BENCH_DIM                # 366
        bench_opp = x[:, bench_opp_start:bench_opp_start + self.BENCH_DIM]  # (B, 80)
        return g, own, opp, bench_own, bench_opp

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """返回 (value, policy_logits)。"""
        # 1. 分解 + 编码
        g, own, opp, bench_own, bench_opp = self._decompose(x)

        g_enc = self.global_enc(g)           # (B, 64)
        own_enc = self.sprite_enc(own)       # (B, 128)
        opp_enc = self.sprite_enc(opp)       # (B, 128)
        b_own_enc = self.bench_enc(bench_own)  # (B, 64)
        b_opp_enc = self.bench_enc(bench_opp)  # (B, 64)

        # 2. 交叉注意力
        if self.with_attention:
            own_enc, opp_enc = self.cross_attn(own_enc, opp_enc)

        # 3. 融合
        fused = torch.cat([g_enc, own_enc, opp_enc, b_own_enc, b_opp_enc], dim=-1)
        h = self.fusion(fused)

        # 4. 残差塔
        for block in self.blocks:
            h = block(h)

        # 5. 双头输出 — 策略头分离为技能/换宠/聚能/道具四个独立子头
        value = self.value_head(h)
        logits = torch.cat([
            self.skill_head(h),    # (B, 4)
            self.switch_head(h),   # (B, 5)
            self.gather_head(h),   # (B, 1)
            self.item_head(h),     # (B, 1)
        ], dim=-1)                 # → (B, 11)
        return value, logits

    def forward_with_mask(
        self, x: torch.Tensor, mask: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """返回 (value, masked_softmax_probs)。mask 中 1=可用, 0=禁用。"""
        value, logits = self.forward(x)
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
            "trunk_dim": self.trunk_dim,
            "num_blocks": self.num_blocks,
            "dropout": self.dropout_val,
            "with_attention": self.with_attention,
            "type": "ModularBattleNet",
        }, path)

    @classmethod
    def load(cls, path: str, device: str = "cpu") -> "ModularBattleNet":
        data = torch.load(path, map_location=device, weights_only=False)
        model = cls(
            trunk_dim=data.get("trunk_dim", 256),
            num_blocks=data.get("num_blocks", 4),
            dropout=float(data.get("dropout", 0.1)),
            with_attention=data.get("with_attention", True),
        )
        model.load_state_dict(data["state_dict"])
        model.to(device)
        model.eval()
        return model
