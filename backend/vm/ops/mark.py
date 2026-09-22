"""mark opcode — add, dispel, steal, or convert marks on a team.

V2: Supports typed MarkOp with action dispatch.
"""

from ..ctx import Ctx
from ..journal import MarkChange, Mutation
from ..resolve import resolve

#: `name` 的两条随机占位（薄纱环「随机获得1种正面/负面印记」）。
#: 与 devotion 的 `name:"random"` 同一写法约定。
RANDOM_POSITIVE = "random_positive"
RANDOM_NEGATIVE = "random_negative"


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def resolve_random_mark_name(name: str) -> str | None:
    """把 `random_positive` / `random_negative` 解析成一枚具体印记模板名。

    候选按名字**排序**后 `random.choice`（排序是为了不受模板字典插入顺序影响，
    与巧变池同口径：对局由 `random.seed(seed)` 播种 → 录制可复现）。
    无候选模板时返回 None（调用方按空操作处理）。
    """
    import random

    from backend.engine.mark_config import NEGATIVE_MARK_NAMES, POSITIVE_MARK_NAMES

    pool = (POSITIVE_MARK_NAMES if name == RANDOM_POSITIVE
            else NEGATIVE_MARK_NAMES if name == RANDOM_NEGATIVE else None)
    if pool is None:
        return None
    candidates = sorted(pool)
    if not candidates:
        return None
    return random.choice(candidates)


def op_mark(ctx: Ctx, effect) -> list[Mutation]:
    """Apply/dispel/steal/convert/enhance marks on a team.

    action="apply": add stacks (default)
    action="dispel": remove stacks from target_team
    action="steal": remove from target_team, add to own
    action="convert": convert abnormal stacks on self → marks on target_team
    action="enhance_all": +delta stacks to every **existing** mark of the team

    Uses 'stacks' for fixed stacks or 'value' (query) for dynamic stacks.
    """
    target = _get(effect, "target", "sprite_self")
    name = _get(effect, "name")
    action = _get(effect, "action", "apply")

    if isinstance(effect, dict):
        delta = effect.get("stacks", resolve(ctx, effect.get("value", 1)))
    else:
        delta = resolve(ctx, effect.value) if effect.value is not None else effect.stacks

    # `name: "random_positive"/"random_negative"`（薄纱环）：随机取一枚模板名再施加
    if action == "apply" and name in (RANDOM_POSITIVE, RANDOM_NEGATIVE):
        picked = resolve_random_mark_name(name)
        if picked is None:
            return []
        name = picked

    # Normalize: 5 target values → own/opp（team_both 只在 enhance_all 下有意义）
    if target == "team_both":
        team = "both"
    else:
        team = "own" if target in ("team_own", "own_team", "sprite_self") else "opp"
    # Allow explicit target_team override (used by steal/conversion ops)
    explicit_team = _get(effect, "target_team", "")
    if explicit_team:
        team = "own" if explicit_team in ("own", "own_team", "team_own") else "opp"

    result = [MarkChange(
        target_team=team,
        name=name,
        delta=int(delta),
        action=action,
        ratio=_get(effect, "ratio", 1.0),
        source_abnormal=_get(effect, "source_abnormal", None),
    )]

    combo = max(1, ctx.combo_self)
    if combo > 1 and result:
        result = result * combo

    return result
