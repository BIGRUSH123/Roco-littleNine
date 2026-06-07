"""backend/engine/ai/core — 核心逻辑层

使用懒加载避免 Windows multiprocessing.spawn 子进程循环导入。
"""

import sys as _sys

_LAZY_IMPORTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "ModularBattleNet": ("backend.engine.ai.core.model", ("ModularBattleNet",)),
    "ResidualBlock": ("backend.engine.ai.core.model", ("ResidualBlock",)),
    "MutualCrossAttention": ("backend.engine.ai.core.model", ("MutualCrossAttention",)),
    "encode_battle_state": ("backend.engine.ai.core.encoder", ("encode_battle_state",)),
    "tokenize_effects": ("backend.engine.ai.core.encoder", ("tokenize_effects",)),
    "tokenize_effects_to_ids": ("backend.engine.ai.core.encoder", ("tokenize_effects_to_ids",)),
    "tokenize_effect_dfs": ("backend.engine.ai.core.encoder", ("tokenize_effect_dfs",)),
    "MAX_SEQ_LEN": ("backend.engine.ai.core.encoder", ("MAX_SEQ_LEN",)),
    "PolicyValueEvaluator": ("backend.engine.ai.core.evaluator", ("PolicyValueEvaluator",)),
    "TorchEvaluator": ("backend.engine.ai.core.evaluator", ("TorchEvaluator",)),
    "QueuePolicyEvaluator": ("backend.engine.ai.core.evaluator", ("QueuePolicyEvaluator",)),
    "QueueModelEvaluator": ("backend.engine.ai.core.evaluator", ("QueueModelEvaluator",)),
    "BatchedInferenceServer": ("backend.engine.ai.core.evaluator", ("BatchedInferenceServer",)),
    "BatchedModelInferenceServer": ("backend.engine.ai.core.evaluator", ("BatchedModelInferenceServer",)),
    "INFERENCE_STOP": ("backend.engine.ai.core.evaluator", ("INFERENCE_STOP",)),
    "NUM_ACTIONS": ("backend.engine.ai.core.mcts", ("NUM_ACTIONS",)),
    "MCTSNode": ("backend.engine.ai.core.mcts", ("MCTSNode",)),
    "NetworkPolicyAgent": ("backend.engine.ai.core.mcts", ("NetworkPolicyAgent",)),
    "action_index_to_action": ("backend.engine.ai.core.mcts", ("action_index_to_action",)),
    "get_valid_actions": ("backend.engine.ai.core.mcts", ("get_valid_actions",)),
    "mcts_search": ("backend.engine.ai.core.mcts", ("mcts_search",)),
    "policy_select_idx": ("backend.engine.ai.core.mcts", ("policy_select_idx",)),
    "DEFAULT_DRAW_MARGIN": ("backend.engine.ai.core.outcome", ("DEFAULT_DRAW_MARGIN",)),
    "DEFAULT_EVAL_MAX_TURNS": ("backend.engine.ai.core.outcome", ("DEFAULT_EVAL_MAX_TURNS",)),
    "DEFAULT_SELFPLAY_MAX_TURNS": ("backend.engine.ai.core.outcome", ("DEFAULT_SELFPLAY_MAX_TURNS",)),
    "battle_outcome_a": ("backend.engine.ai.core.outcome", ("battle_outcome_a",)),
    "eval_score_for_candidate": ("backend.engine.ai.core.outcome", ("eval_score_for_candidate",)),
    "format_reason_counts": ("backend.engine.ai.core.outcome", ("format_reason_counts",)),
    "ALL_TOKENS": ("backend.engine.ai.core.vocab", ("ALL_TOKENS",)),
    "VOCAB_SIZE": ("backend.engine.ai.core.vocab", ("VOCAB_SIZE",)),
    "VOCAB_TO_ID": ("backend.engine.ai.core.vocab", ("VOCAB_TO_ID",)),
    "ID_TO_VOCAB": ("backend.engine.ai.core.vocab", ("ID_TO_VOCAB",)),
    "VAL_NUMERIC": ("backend.engine.ai.core.vocab", ("VAL_NUMERIC",)),
    "get_token_id": ("backend.engine.ai.core.vocab", ("get_token_id",)),
    "get_token": ("backend.engine.ai.core.vocab", ("get_token",)),
    "is_special": ("backend.engine.ai.core.vocab", ("is_special",)),
    "is_opcode": ("backend.engine.ai.core.vocab", ("is_opcode",)),
    "is_condition": ("backend.engine.ai.core.vocab", ("is_condition",)),
}

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
        globals()[attr] = obj
    return _lazy_cache[name]


__all__ = list(_LAZY_IMPORTS.keys())
