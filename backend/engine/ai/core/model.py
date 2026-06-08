"""backend/engine/ai/core/model.py — 对战状态评估 + 策略网络

模型架构:
  EntityBottleneckNet — 实体化瓶颈 + AST Transformer + 展平融合（v4）
    原始数值 → Log1pNorm → 线性瓶颈
    离散类别 → nn.Embedding（双元素 sum pooling）
    实体级交叉注意力（6×6 博弈矩阵） → 展平保留位置
    AST 双流 Transformer（token emb + value proj） → masked mean 池化
    多流展平拼接（1248）→ 残差主干 → 双头输出（价值 + 17 动作策略）
"""

from __future__ import annotations

import math
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F

from backend.engine.ai.core.mcts import NUM_ACTIONS as MCTS_NUM_ACTIONS
from backend.engine.ai.core.vocab import VOCAB_SIZE


# ═══════════════════════════════════════════════════════════════════
# 基础组件
# ═══════════════════════════════════════════════════════════════════

class ResidualBlock(nn.Module):
    """带 LayerNorm 的残差 MLP 块。"""

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

    支持单向量 (B, D) 和实体级 (B, N, D) 两种输入。
    """

    def __init__(self, dim: int, num_heads: int = 4, dropout: float = 0.0):
        super().__init__()
        self.num_heads = num_heads
        self.head_dim = dim // num_heads
        assert self.head_dim * num_heads == dim, "dim must be divisible by num_heads"

        self.q_proj = nn.Linear(dim, dim)
        self.k_proj = nn.Linear(dim, dim)
        self.v_proj = nn.Linear(dim, dim)
        self.out_proj = nn.Linear(dim, dim)
        self.dropout = nn.Dropout(dropout)
        self.scale = math.sqrt(self.head_dim)

    def _attend(self, query: torch.Tensor, key_value: torch.Tensor) -> torch.Tensor:
        """跨实体注意力：(B, N, D) → (B, N, D)，每实体 attend 对方所有实体。

        注：单向量路径 (B, D) 为遗留兼容，当前只调用实体级 (B, N, D)。
        """
        B, *rest = query.shape
        H, D = self.num_heads, self.head_dim
        has_seq = (len(query.shape) == 3)  # (B, N, D) — 实体级； (B, D) — 遗留单向量

        q = self.q_proj(query)
        k = self.k_proj(key_value)
        v = self.v_proj(key_value)

        if has_seq:
            N = query.shape[1]
            q = q.view(B, N, H, D).transpose(1, 2)   # (B, H, N, D) — N 个实体互相 attend
            k = k.view(B, N, H, D).transpose(1, 2)
            v = v.view(B, N, H, D).transpose(1, 2)
        else:
            # 遗留：单向量路径，头内特征间注意力
            q = q.view(B, H, D)   # (B, H, D)
            k = k.view(B, H, D)
            v = v.view(B, H, D)

        attn = (q @ k.transpose(-2, -1)) / self.scale
        attn = F.softmax(attn, dim=-1)
        attn = self.dropout(attn)
        out = attn @ v

        if has_seq:
            out = out.transpose(1, 2).reshape(B, N, H * D)
        else:
            out = out.reshape(B, H * D)

        return query + self.out_proj(out)

    def forward(self, own_features: torch.Tensor, opp_features: torch.Tensor):
        own_out = self._attend(own_features, opp_features)
        opp_out = self._attend(opp_features, own_features)
        return own_out, opp_out


class Log1pNorm(nn.Module):
    """log1p 归一化：x → log(1 + |x|) * sign(x) / log(max_val + 1)。"""

    def __init__(self, max_val: float = 1000.0):
        super().__init__()
        self.register_buffer('_scale', torch.tensor(math.log(max_val + 1)))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return torch.sign(x) * torch.log1p(torch.abs(x)) / self._scale


# ═══════════════════════════════════════════════════════════════════
# 模型定义
# ═══════════════════════════════════════════════════════════════════

class EntityBottleneckNet(nn.Module):
    """实体化瓶颈网络 + AST Transformer：结构化矩阵 → 瓶颈压缩 → 延迟融合。

    输入为 encoder.py 返回的 dict:
      - sprite_stats:    (B, 12, 7)  float32
      - sprite_elements: (B, 12, 2) int64    双元素 ID (主/副, 0=PAD)
      - sprite_states:   (B, 12, 105) float32
      - skill_stats:     (B, 10, 2)  float32   [power, energy_cost] raw
      - skill_elements:  (B, 10, 2) int64    双元素 ID (主/PAD)
      - skill_states:    (B, 10, 9) float32  [sealed, cooldown, 类型OneHot(5), combo, transmission] raw
      - global_stats:    (B, 15,)    float32
      - global_elements: (B, 1,)     int64
      - ast_tokens:      (B, 384)    int64    token ID 序列 (PAD=0)
      - ast_values:      (B, 384)    float32  对应值序列
    """

    NUM_ACTIONS = MCTS_NUM_ACTIONS

    def __init__(
        self,
        trunk_dim: int = 256,
        num_blocks: int = 4,
        dropout: float = 0.1,
        vocab_size: int = VOCAB_SIZE,
        ast_max_len: int = 384,
        *,
        with_attention: bool = True,
    ):
        super().__init__()
        self.trunk_dim = trunk_dim
        self.num_blocks = num_blocks
        self.dropout_val = float(dropout)
        self.vocab_size = vocab_size
        self.ast_max_len = ast_max_len
        self.with_attention = with_attention

        # ── 归一化 ──
        self.log1p_stats = Log1pNorm(max_val=1000.0)

        # ── Embedding 表 ──
        self.element_emb = nn.Embedding(19, 16, padding_idx=0)  # 0=PAD, 1-18=elements
        self.weather_emb = nn.Embedding(5, 4, padding_idx=0)    # 0=none, 1=rain, 2=sand, 3=snow, 4=blizzard

        # ── 精灵瓶颈 ──
        self.sprite_stats_enc = nn.Sequential(
            nn.Linear(7, 32),
            nn.LayerNorm(32),
            nn.GELU(),
        )
        self.sprite_states_enc = nn.Sequential(
            nn.Linear(105, 64),
            nn.LayerNorm(64),
            nn.GELU(),
        )
        # fusion: stats(32) + element_emb(16) + states(64) = 112
        self.sprite_bottleneck = nn.Sequential(
            nn.Linear(112, 64),
            nn.LayerNorm(64),
            nn.GELU(),
            nn.Dropout(dropout),
        )

        # ── 技能瓶颈 ──
        self.skill_stats_enc = nn.Sequential(
            nn.Linear(2, 16),
            nn.LayerNorm(16),
            nn.GELU(),
        )
        self.skill_states_enc = nn.Sequential(
            nn.Linear(9, 16),
            nn.LayerNorm(16),
            nn.GELU(),
        )
        # fusion: stats(16) + element_emb(16) + states(16) = 48
        self.skill_bottleneck = nn.Sequential(
            nn.Linear(48, 32),
            nn.LayerNorm(32),
            nn.GELU(),
            nn.Dropout(dropout),
        )

        # ── 全局瓶颈 ──
        self.global_stats_enc = nn.Sequential(
            nn.Linear(15, 32),
            nn.LayerNorm(32),
            nn.GELU(),
        )
        # fusion: stats(32) + weather_emb(4) = 36
        self.global_bottleneck = nn.Sequential(
            nn.Linear(36, 32),
            nn.LayerNorm(32),
            nn.GELU(),
            nn.Dropout(dropout),
        )

        # ── AST 双流 Transformer 编码器 ──
        self.ast_dim = 128
        self.ast_token_emb = nn.Embedding(vocab_size, self.ast_dim, padding_idx=0)
        self.ast_value_proj = nn.Linear(1, self.ast_dim)
        self.ast_pos_emb = nn.Embedding(self.ast_max_len, self.ast_dim)
        self.register_buffer(
            "_ast_pos_ids",
            torch.arange(self.ast_max_len, dtype=torch.long).unsqueeze(0),
            persistent=False,
        )
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=self.ast_dim, nhead=4, dim_feedforward=self.ast_dim * 4,
            dropout=dropout, batch_first=True,
        )
        self.ast_transformer = nn.TransformerEncoder(encoder_layer, num_layers=2)

        # ── 交叉注意力 ──
        if self.with_attention:
            self.cross_attn = MutualCrossAttention(64, num_heads=4, dropout=dropout)

        # ── 延迟融合 ──
        # sp_own_flat(6*64=384) + sp_opp_flat(384) + sk_flat(10*32=320) + g_pool(32) + ast(128) = 1248
        fusion_in = 384 + 384 + 320 + 32 + self.ast_dim  # 1248
        self.fusion = nn.Sequential(
            nn.Linear(fusion_in, trunk_dim),
            nn.LayerNorm(trunk_dim),
            nn.GELU(),
        )

        # ── 残差塔 ──
        self.blocks = nn.ModuleList([
            ResidualBlock(trunk_dim, dropout) for _ in range(num_blocks)
        ])

        # ── 价值头 ──
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

        # ── 策略头（四个独立子头） ──
        policy_hidden = max(trunk_dim // 4, 32)
        self.skill_head = nn.Sequential(
            nn.Linear(trunk_dim, policy_hidden),
            nn.LayerNorm(policy_hidden),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(policy_hidden, 10),
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
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.xavier_uniform_(m.weight)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
            if isinstance(m, nn.Embedding):
                nn.init.normal_(m.weight, mean=0, std=0.1)
                # 重置 padding 行为零向量 (nn.init 会覆盖)
                if m.padding_idx is not None:
                    with torch.no_grad():
                        m.weight[m.padding_idx] = 0.0

    def forward(self, state: dict[str, torch.Tensor]) -> tuple[torch.Tensor, torch.Tensor]:
        """返回 (value, policy_logits)。"""
        B = state["sprite_stats"].shape[0]

        # ── 解包 ──
        sp_stats = state["sprite_stats"]       # (B, 12, 7)
        sp_elems = state["sprite_elements"]    # (B, 12, 2)
        sp_states = state["sprite_states"]     # (B, 12, 105)
        sk_stats = state["skill_stats"]        # (B, 10, 2)
        sk_elems = state["skill_elements"]     # (B, 10, 2)
        sk_states = state["skill_states"]      # (B, 10, 9)
        g_stats = state["global_stats"]        # (B, 15)
        g_elems = state["global_elements"]     # (B, 1)
        ast_tokens = state["ast_tokens"].long()   # (B, SeqLen)
        ast_values = state["ast_values"].float()  # (B, SeqLen)

        # ── 精灵编码 ──
        sp_stats_n = self.log1p_stats(sp_stats)          # (B, 12, 7)
        sp_s = self.sprite_stats_enc(sp_stats_n)          # (B, 12, 32)
        # 双属性嵌入: (B, 12, 2) → embed → (B, 12, 2, 16) → sum → (B, 12, 16)
        # 单属性"水": [水向量] + [0向量] = [水向量]
        # 双属性"水+翼": [水向量] + [翼向量] = 复合抗性表示
        sp_e = self.element_emb(sp_elems.long()).sum(dim=2)  # (B, 12, 16)
        sp_t = self.sprite_states_enc(sp_states)              # (B, 12, 64)
        sp_cat = torch.cat([sp_s, sp_e, sp_t], dim=-1)       # (B, 12, 112)
        sp_enc = self.sprite_bottleneck(sp_cat)            # (B, 12, 64)

        # 拆分为己方 (0-5) / 对方 (6-11) — 保留实体维度用于交叉注意力
        sp_own_entities = sp_enc[:, :6, :]   # (B, 6, 64)
        sp_opp_entities = sp_enc[:, 6:, :]   # (B, 6, 64)

        # ── 实体级交叉注意力（己方每只精灵 attend 对方每只精灵） ──
        if self.with_attention:
            sp_own_entities, sp_opp_entities = self.cross_attn(sp_own_entities, sp_opp_entities)

        # Flatten: 保留 0 号位（活跃精灵）的绝对位置感知
        sp_own_flat = sp_own_entities.reshape(B, -1)  # (B, 384)
        sp_opp_flat = sp_opp_entities.reshape(B, -1)  # (B, 384)

        # ── 技能编码 ──
        sk_stats_n = self.log1p_stats(sk_stats)            # (B, 10, 2)
        sk_s = self.skill_stats_enc(sk_stats_n)             # (B, 10, 16)
        sk_e = self.element_emb(sk_elems.long()).sum(dim=2) # (B, 10, 16)
        sk_t = self.skill_states_enc(self.log1p_stats(sk_states))  # (B, 10, 16)
        sk_cat = torch.cat([sk_s, sk_e, sk_t], dim=-1)     # (B, 10, 48)
        sk_enc = self.skill_bottleneck(sk_cat)               # (B, 10, 32)

        # Flatten: 与策略头 10 个 Action 严格对齐
        sk_flat = sk_enc.reshape(B, -1)                     # (B, 320)

        # ── 全局编码 ──
        g_enc = self.global_stats_enc(g_stats)               # (B, 32)
        g_w = self.weather_emb(g_elems.long()).squeeze(1)    # (B, 4)
        g_cat = torch.cat([g_enc, g_w], dim=-1)              # (B, 36)
        g_pool = self.global_bottleneck(g_cat)               # (B, 32)

        # ── AST Transformer 编码 ──
        # 双流融合: Token 词向量 + Value 浮点投影 + 位置编码
        B_ast, SeqLen = ast_tokens.shape
        non_pad_mask = ast_tokens != 0

        if not non_pad_mask.any():
            # 全部为 PAD（无 AST 数据）：直接用零向量
            ast_global = torch.zeros(B, self.ast_dim, device=ast_tokens.device)
        else:
            effective_len = int(non_pad_mask.any(dim=0).nonzero()[-1].item()) + 1
            if effective_len < SeqLen:
                ast_tokens = ast_tokens[:, :effective_len]
                ast_values = ast_values[:, :effective_len]
                non_pad_mask = non_pad_mask[:, :effective_len]
                SeqLen = effective_len

            t_emb = self.ast_token_emb(ast_tokens)                # (B, SeqLen, 128)
            v_emb = self.ast_value_proj(ast_values.unsqueeze(-1)) # (B, SeqLen, 128)
            pos_ids = self._ast_pos_ids[:, :SeqLen].expand(B_ast, -1)
            p_emb = self.ast_pos_emb(pos_ids)                     # (B, SeqLen, 128)
            ast_seq = t_emb + v_emb + p_emb

            # Padding Mask: 忽略 PAD token (ID=0)
            if non_pad_mask.all():
                ast_out = self.ast_transformer(ast_seq)  # (B, SeqLen, 128)
                ast_global = ast_out.mean(dim=1)
            else:
                padding_mask = ~non_pad_mask
                ast_out = self.ast_transformer(ast_seq, src_key_padding_mask=padding_mask)  # (B, SeqLen, 128)
            # 全遮序列的 softmax 输入均为 -inf，输出 NaN。根据 IEEE 754，
            # NaN * 0 = NaN，masked mean 前必须显式归零阻断污染。
                ast_out = torch.nan_to_num(ast_out, nan=0.0)
            # 全局池化（masked mean — 排除 PAD，防止拉低响应）
                mask_expanded = non_pad_mask.unsqueeze(-1).float()
                ast_global = (ast_out * mask_expanded).sum(dim=1) / mask_expanded.sum(dim=1).clamp(min=1e-8)  # (B, 128)

        # ── 延迟融合 ──
        fused = torch.cat([sp_own_flat, sp_opp_flat, sk_flat, g_pool, ast_global], dim=-1)
        h = self.fusion(fused)

        # ── 残差塔 ──
        for block in self.blocks:
            h = block(h)

        # ── 双头输出 ──
        value = self.value_head(h)
        logits = torch.cat([
            self.skill_head(h),    # (B, 10)
            self.switch_head(h),   # (B, 5)
            self.gather_head(h),   # (B, 1)
            self.item_head(h),     # (B, 1)
        ], dim=-1)                 # → (B, 17)
        return value, logits

    def forward_with_mask(
        self, state: dict[str, torch.Tensor], mask: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """返回 (value, masked_softmax_probs)。mask 中 1=可用, 0=禁用。"""
        value, logits = self.forward(state)
        masked_logits = logits.masked_fill(mask < 0.5, -1e9)
        probs = F.softmax(masked_logits, dim=-1)
        probs = probs * mask  # 全零 mask 时 softmax 输出均匀分布，乘 mask 归零
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
            "vocab_size": self.vocab_size,
            "with_attention": self.with_attention,
            "ast_max_len": self.ast_max_len,
            "type": "EntityBottleneckNet",
        }, path)

    @classmethod
    def load(cls, path: str, device: str = "cpu") -> "EntityBottleneckNet":
        data = torch.load(path, map_location=device, weights_only=False)
        model = cls(
            trunk_dim=data.get("trunk_dim", 256),
            num_blocks=data.get("num_blocks", 4),
            dropout=float(data.get("dropout", 0.1)),
            vocab_size=data.get("vocab_size", VOCAB_SIZE),
            ast_max_len=data.get("ast_max_len", 384),
            with_attention=data.get("with_attention", True),
        )
        model.load_state_dict(data["state_dict"])
        model.to(device)
        model.eval()
        return model


# ── 向后兼容别名 ──
ModularBattleNet = EntityBottleneckNet
