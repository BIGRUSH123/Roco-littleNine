"""Value resolution — literal pass-through or query expression against Ctx.

resolve(ctx, value) -> int | float | str
    Literals returned as-is. Query dicts resolved via ADDRESS_MAP -> getattr().

QueryRef is a pre-indexed query for O(1) runtime lookup (used by SkillLoader).
"""

from dataclasses import dataclass
from .ctx import Ctx, ADDRESS_MAP

# Dict-type register queries that require a 'name' key for sub-indexing
_NAMED_DICT_QUERIES = frozenset({
    "counter_value", "abnormal_stacks", "devotion", "skill_count",
})


@dataclass
class QueryRef:
    """Pre-indexed query for O(1) runtime lookup.

    Built at load time by SkillLoader.pre_index_queries(). At runtime,
    field access is a single getattr() — no map lookup.
    """
    field: str              # Ctx attribute name (from ADDRESS_MAP)
    name: str | None = None # sub-key for dict registers
    scale: float = 1.0
    offset: int = 0
    per: float | None = None
    default: int | float | str | None = None
    # If set, this is a dict-type query and name is the sub-key
    is_dict_query: bool = False
    # Which sub-key field to use: "name" | "skill_type" | "element" | "tag"
    sub_key_field: str = "name"


def resolve(ctx: Ctx, value: int | float | str | dict | bool) -> int | float | str:
    """Resolve a value against the Ctx snapshot.

    Literals (int, float, str, bool) are returned unchanged.
    Query dicts ({"q": ..., "of": ...}) are resolved via ADDRESS_MAP.

    Transform chain (applied in order):
        1. per  — int(raw / per)
        2. scale — raw * scale
        3. offset — raw + offset

    Dict registers (abnormal_stacks, devotion, etc.) use the 'name' field
    for sub-indexing. energy_cost_sum uses 'skill_type'/'element'/'tag'.
    """
    # Literal values — pass through (bool before int since bool is subclass of int)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, str)):
        return value
    if not isinstance(value, dict):
        return value

    # Query expression
    q = value.get("q")
    if q is None:
        raise KeyError(f"Query dict missing 'q' key: {value}")

    of = value.get("of", "sprite_self")

    # ADDRESS_MAP lookup
    map_key = (of, q)
    if map_key not in ADDRESS_MAP:
        raise KeyError(f"Unknown query (of={of}, q={q}) — not in ADDRESS_MAP")
    field_name = ADDRESS_MAP[map_key]
    raw = getattr(ctx, field_name)

    # Dict-type registers: sub-index by name or type/element/tag
    if q in _NAMED_DICT_QUERIES:
        sub_key = value.get("name")
        raw = raw.get(sub_key, 0) if isinstance(raw, dict) else 0
    elif q == "energy_cost_sum":
        sub_key = value.get("skill_type") or value.get("element") or value.get("tag")
        raw = raw.get(sub_key, 0) if isinstance(raw, dict) else 0

    # Default fallback (when raw is falsy: 0, "", None, empty)
    if "default" in value and not raw:
        raw = value["default"]

    # String / bool values — return without numeric transform
    if isinstance(raw, (str, bool)):
        return raw

    # Numeric transforms: per -> scale -> offset
    if "per" in value:
        per = value["per"]
        raw = int(raw / per) if per != 0 else raw

    if "scale" in value:
        raw = int(raw * value["scale"])

    if "offset" in value:
        raw = int(raw + value["offset"])

    return raw
