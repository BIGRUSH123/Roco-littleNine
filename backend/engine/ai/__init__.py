"""backend/engine/ai — AlphaZero 风格 RL 自我博弈训练管线

目录:
  core/     核心逻辑层（模型 / 编码 / MCTS / 评估）
  service/  接口适配层（前端 AI Agent / Advisor）
  data/     工具数据层（随机池等）
  tests/    测试

向后兼容：旧模块路径已自动转发到新位置。使用懒加载避免 Windows spawn 循环导入。
"""

import sys as _sys

# 重型模块的懒加载表：{属性名: (模块路径, 导出名列表)}
# 延迟到首次访问时加载，避免 Windows multiprocessing.spawn 子进程
# 在 import 阶段触发 torch → runpy.run_module 循环重入。
_LAZY_IMPORTS: dict[str, tuple[str, tuple[str, ...]]] = {
    # core.model
    "EntityBottleneckNet":   ("backend.engine.ai.core.model", ("EntityBottleneckNet",)),
    "ModularBattleNet":      ("backend.engine.ai.core.model", ("ModularBattleNet",)),
    "ResidualBlock":         ("backend.engine.ai.core.model", ("ResidualBlock",)),
    "MutualCrossAttention":  ("backend.engine.ai.core.model", ("MutualCrossAttention",)),
    # core.encoder
    "encode_battle_state":       ("backend.engine.ai.core.encoder", ("encode_battle_state",)),
    "tokenize_effects":          ("backend.engine.ai.core.encoder", ("tokenize_effects",)),
    "tokenize_effects_to_ids":   ("backend.engine.ai.core.encoder", ("tokenize_effects_to_ids",)),
    "tokenize_effect_dfs":       ("backend.engine.ai.core.encoder", ("tokenize_effect_dfs",)),
    "MAX_SEQ_LEN":               ("backend.engine.ai.core.encoder", ("MAX_SEQ_LEN",)),
    # core.evaluator
    "PolicyValueEvaluator":          ("backend.engine.ai.core.evaluator", ("PolicyValueEvaluator",)),
    "TorchEvaluator":                ("backend.engine.ai.core.evaluator", ("TorchEvaluator",)),
    "QueuePolicyEvaluator":          ("backend.engine.ai.core.evaluator", ("QueuePolicyEvaluator",)),
    "QueueModelEvaluator":           ("backend.engine.ai.core.evaluator", ("QueueModelEvaluator",)),
    "BatchedInferenceServer":        ("backend.engine.ai.core.evaluator", ("BatchedInferenceServer",)),
    "BatchedModelInferenceServer":   ("backend.engine.ai.core.evaluator", ("BatchedModelInferenceServer",)),
    # core.outcome
    "DEFAULT_DRAW_MARGIN":       ("backend.engine.ai.core.outcome", ("DEFAULT_DRAW_MARGIN",)),
    "DEFAULT_EVAL_MAX_TURNS":    ("backend.engine.ai.core.outcome", ("DEFAULT_EVAL_MAX_TURNS",)),
    "DEFAULT_SELFPLAY_MAX_TURNS": ("backend.engine.ai.core.outcome", ("DEFAULT_SELFPLAY_MAX_TURNS",)),
    "battle_outcome_a":          ("backend.engine.ai.core.outcome", ("battle_outcome_a",)),
    "eval_score_for_candidate":  ("backend.engine.ai.core.outcome", ("eval_score_for_candidate",)),
    "format_reason_counts":      ("backend.engine.ai.core.outcome", ("format_reason_counts",)),
    # core.mcts
    "NUM_ACTIONS":           ("backend.engine.ai.core.mcts", ("NUM_ACTIONS",)),
    "MCTSNode":              ("backend.engine.ai.core.mcts", ("MCTSNode",)),
    "NetworkPolicyAgent":    ("backend.engine.ai.core.mcts", ("NetworkPolicyAgent",)),
    "action_index_to_action": ("backend.engine.ai.core.mcts", ("action_index_to_action",)),
    "get_valid_actions":     ("backend.engine.ai.core.mcts", ("get_valid_actions",)),
    "mcts_search":           ("backend.engine.ai.core.mcts", ("mcts_search",)),
    "policy_select_idx":     ("backend.engine.ai.core.mcts", ("policy_select_idx",)),
    # core.vocab
    "ALL_TOKENS":    ("backend.engine.ai.core.vocab", ("ALL_TOKENS",)),
    "VOCAB_SIZE":    ("backend.engine.ai.core.vocab", ("VOCAB_SIZE",)),
    "VOCAB_TO_ID":   ("backend.engine.ai.core.vocab", ("VOCAB_TO_ID",)),
    "ID_TO_VOCAB":   ("backend.engine.ai.core.vocab", ("ID_TO_VOCAB",)),
    "VAL_NUMERIC":   ("backend.engine.ai.core.vocab", ("VAL_NUMERIC",)),
    "get_token_id":  ("backend.engine.ai.core.vocab", ("get_token_id",)),
    "get_token":     ("backend.engine.ai.core.vocab", ("get_token",)),
    "is_special":    ("backend.engine.ai.core.vocab", ("is_special",)),
    "is_opcode":     ("backend.engine.ai.core.vocab", ("is_opcode",)),
    "is_condition":  ("backend.engine.ai.core.vocab", ("is_condition",)),
    # service.agent
    "set_checkpoint":  ("backend.engine.ai.service.agent", ("set_checkpoint",)),
    "PolicyAgent":     ("backend.engine.ai.service.agent", ("PolicyAgent",)),
    "NeuralMCTSAgent": ("backend.engine.ai.service.agent", ("NeuralMCTSAgent",)),
    # service.advisor
    "Advice":               ("backend.engine.ai.service.advisor", ("Advice",)),
    "advise_single":        ("backend.engine.ai.service.advisor", ("advise_single",)),
    "advise":               ("backend.engine.ai.service.advisor", ("advise",)),
    "make_determinizations": ("backend.engine.ai.service.advisor", ("make_determinizations",)),
    "describe_action":      ("backend.engine.ai.service.advisor", ("describe_action",)),
    # core.replay_buffer
    "DictReplayBuffer":   ("backend.engine.ai.core.replay_buffer", ("DictReplayBuffer",)),
    "DictReplayDataset":  ("backend.engine.ai.core.replay_buffer", ("DictReplayDataset",)),
    "dict_replay_collate": ("backend.engine.ai.core.replay_buffer", ("dict_replay_collate",)),
}

# 缓存已懒加载的模块
_lazy_cache: dict[str, object] = {}


def __getattr__(name: str) -> object:
    if name in _lazy_cache:
        return _lazy_cache[name]
    info = _LAZY_IMPORTS.get(name)
    if info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_path, attrs = info
    __import__(mod_path)
    module = _sys.modules[mod_path]
    for attr in attrs:
        obj = getattr(module, attr)
        _lazy_cache[attr] = obj
        # 绑定到当前模块全局命名空间，后续直接访问（绕过 __getattr__）
        globals()[attr] = obj
    return _lazy_cache[name]


__all__ = list(_LAZY_IMPORTS.keys())
