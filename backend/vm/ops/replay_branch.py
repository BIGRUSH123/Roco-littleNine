"""replay_branch opcode — 重放当前「选择」技能的另一支/相同一支。

VM 产出 ReplayChoice mutation，由 engine/battle._handle_replay_choice 消费：
从 battle._last_choice_execution 读取本次「选择」技能的分支数据并重放目标分支。
"""

from ..journal import Mutation, ReplayChoice


def op_replay_branch(ctx, effect) -> list[Mutation]:
    if isinstance(effect, dict):
        which = effect.get("which", "other")
    else:
        which = getattr(effect, "which", "other") or "other"
    return [ReplayChoice(which=which)]
