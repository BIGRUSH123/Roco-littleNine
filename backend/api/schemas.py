"""scripts/api/schemas.py — API 契约层

所有前后端通信的 Pydantic 模型集中于此。
API 层不应直接暴露 domain 对象（Sprite, BattleSkill 等），
而是通过这里的 schema 解耦内部模型与外部契约。
"""


from pydantic import BaseModel

# ═══════════════════════════════════════════════════════════════════
# 请求模型
# ═══════════════════════════════════════════════════════════════════

class SpriteSelection(BaseModel):
    name: str
    skills: list[str]
    bloodline: str | None = None
    form: str = ''


class InitRequest(BaseModel):
    team: list[SpriteSelection]
    opponent_team: list[SpriteSelection] | None = None
    lead_index: int = 0
    item: str | None = None
    ai_agent: str | None = None  # registered agent name (whitelist)


class ActionRequest(BaseModel):
    session_id: str
    action_type: str  # "skill" | "switch" | "gather" | "item"
    skill_name: str | None = None
    switch_index: int | None = None


class DebugActionRequest(BaseModel):
    session_id: str
    action_a: dict  # {type, skill_name?, switch_index?}
    action_b: dict


# ═══════════════════════════════════════════════════════════════════
# 响应模型
# ═══════════════════════════════════════════════════════════════════

class SkillSummary(BaseModel):
    name: str
    skill_index: int            # 技能栏位置（0=1号位）
    base_power: int
    effective_power: int        # 含位置加成 / 印记加成
    position_power_bonus: int   # 当前位的威力加成（械斗等在X号位）
    base_energy_cost: int
    effective_energy_cost: int  # 含轴承支撑被动 / 印记减费
    cooldown: int
    transmission: int           # 传动等级（0=不传动）
    main_axis: bool             # 主轴：不参与传动


class EffectSummary(BaseModel):
    name: str
    category: str
    stacks: int = 1
    steps: int = 0


class SpriteState(BaseModel):
    name: str
    element: str
    bloodline: str
    bloodline_skills: dict[str, int]
    current_hp: int
    max_hp: int
    energy: int
    is_fainted: bool
    charging: str
    trait: str
    energy_cost_mod: int
    effects: list[EffectSummary]
    skills: list[SkillSummary]


class ItemState(BaseModel):
    name: str
    max_uses: int
    uses: int
    cooldown_turns: int
    last_use_turn: int
    is_exhausted: bool


class PlayerState(BaseModel):
    name: str
    active_index: int
    lives: int
    item: ItemState | None = None
    team: list[SpriteState]


class MarkSummary(BaseModel):
    name: str
    stacks: int
    type: str  # "positive" | "negative"


class BattleState(BaseModel):
    session_id: str
    turn: int
    is_finished: bool
    winner: str | None = None
    weather: str
    weather_turns: int
    player_a: PlayerState
    player_b: PlayerState
    marks_a: list[MarkSummary]
    marks_b: list[MarkSummary]
    mark_energy_mod_a: int
    mark_energy_mod_b: int


# ═══════════════════════════════════════════════════════════════════
# 回合快照（回放）
# ═══════════════════════════════════════════════════════════════════

class SnapshotSprite(BaseModel):
    name: str
    current_hp: int
    max_hp: int
    energy: int
    is_fainted: bool
    effects: list[EffectSummary]
    skills: list[SkillSummary]


class TurnSnapshot(BaseModel):
    turn: int
    self_sprite: SnapshotSprite
    opp_sprite: SnapshotSprite
    log_entries: list[str]


# ═══════════════════════════════════════════════════════════════════
# 杂项响应
# ═══════════════════════════════════════════════════════════════════

class SpriteEntry(BaseModel):
    name: str
    element: str
    number: int
    skills: list[str]


class SpriteListResponse(BaseModel):
    sprites: list[SpriteEntry]


class EvolutionResponse(BaseModel):
    sprite: str
    number: int
    can_evolve: bool
    leader_form: str = ''
    leader_species: dict | None = None


class BloodlineResponse(BaseModel):
    sprite: str
    default_bloodline: str
    available_bloodlines: list[str]
    bloodline_skills: dict[str, int]


class ItemInfo(BaseModel):
    name: str
    description: str
    max_uses: int
    cooldown_turns: int
    cooldown_description: str
    requirement: str = ''


class ItemListResponse(BaseModel):
    items: list[ItemInfo]


class TypeChartResponse(BaseModel):
    chart: dict[str, dict[str, float]]


class DebugInitResponse(BattleState):
    debug_skills_a: list[str] = []
    debug_skills_b: list[str] = []
