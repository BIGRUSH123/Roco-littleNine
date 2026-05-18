"""Ctx — turn snapshot (read-only register set) + ADDRESS_MAP.

All query expressions resolve against this flat dataclass. The engine
builds a fresh Ctx per skill invocation so skill #2 observes skill #1's effects.
"""

from dataclasses import dataclass, field


@dataclass
class Ctx:
    """Turn snapshot — read-only register set.

    Fields map to IR register names. Every query expression resolves via
    ADDRESS_MAP -> getattr(ctx, field_name). Dict registers use 'name'
    parameter for sub-indexing.

    All fields have sensible defaults so tests can construct minimal Ctx objects.
    """

    # ── 己方精灵 ──
    hp_self: int = 0
    hp_self_ratio: float = 1.0
    hp_self_max: int = 100
    hp_self_missing_ratio: float = 0.0  # (max - current) / max
    energy_self: int = 0
    atk_self: int = 100
    def_self: int = 100
    sp_atk_self: int = 100
    sp_def_self: int = 100
    speed_self: int = 100
    damage_reduction_self: float = 0.0  # 0.0=no reduction, 1.0=immune
    abnormal_count_self: int = 0
    abnormal_stacks_self: dict[str, int] = field(default_factory=dict)
    positive_count_self: int = 0
    first_action_self: bool = False
    charged_self: bool = False
    is_charging_self: bool = False       # charging (not yet released)
    self_koed: bool = False             # KO'd this turn
    times_entered_self: int = 0          # cumulative entry count
    times_left_self: int = 0             # cumulative leave count
    elements_used_count_self: int = 0    # distinct elements used
    skills_energy_sum_self: int = 0      # sum of all skill energy costs
    just_entered: bool = False           # entered this turn (for sprite_entered cond)
    skill_elements_self: frozenset = frozenset()  # elements of carried skills
    stat_stages_self: dict[str, int] = field(default_factory=dict)  # {stat: stage} positive=boost
    energy_cost_sum_self: dict[str, int] = field(default_factory=dict)  # {type/element/tag: total energy}
    zero_cost_skill_count_self: int = 0  # number of 0-cost skills carried

    # ── 敌方精灵 ──
    hp_opp: int = 0
    hp_opp_ratio: float = 1.0
    hp_opp_max: int = 100
    hp_opp_missing_ratio: float = 0.0
    energy_opp: int = 0
    atk_opp: int = 100
    def_opp: int = 100
    sp_atk_opp: int = 100
    sp_def_opp: int = 100
    speed_opp: int = 100
    damage_reduction_opp: float = 0.0
    abnormal_count_opp: int = 0
    abnormal_stacks_opp: dict[str, int] = field(default_factory=dict)
    positive_count_opp: int = 0
    charged_opp: bool = False
    skill_elements_opp: frozenset = frozenset()
    stat_stages_opp: dict[str, int] = field(default_factory=dict)  # {stat: stage} positive=boost
    skills_energy_sum_opp: int = 0

    # ── 双方队伍 ──
    mark_count_own: int = 0              # total mark stacks on own team
    mark_stacks_own: dict[str, int] = field(default_factory=dict)  # {name: stacks}
    mark_count_opp: int = 0              # total mark stacks on opponent team
    mark_stacks_opp: dict[str, int] = field(default_factory=dict)  # {name: stacks}
    mark_count_both: int = 0             # own + opp
    skill_count_own: dict[str, int] = field(default_factory=dict)  # {skill_name: count}
    devotion_own: dict[str, int] = field(default_factory=dict)     # {name: stacks}
    devotion_opp: dict[str, int] = field(default_factory=dict)
    abnormal_stacks_battle: dict[str, int] = field(default_factory=dict)  # {name: total across both sides}
    fainted_own: int = 0                 # own team fainted count
    fainted_opp: int = 0                 # opponent team fainted count
    burst_triggered_count_own: int = 0   # distinct burst types triggered by own team
    opp_switched: bool = False           # opponent switched this turn
    self_switched: bool = False          # self was switched out this turn (non-KO)

    # ── 技能（当前发动的技能） ──
    power_self: int = 0                  # skill base power
    adjacent_power_sum: int = 0          # sum of adjacent skill powers
    power_opp: int = 0                   # opponent current skill base power
    skill_type_self: str = ""            # "物攻" | "魔攻" | "动态攻击" | "防御" | "状态"
    skill_type_opp: str = ""             # opponent skill type
    element_self: str = ""               # current skill element
    element_opp: str = ""                # opponent current skill element
    skill_tag_self: str = ""             # current skill tag
    combo_self: int = 0                  # current combo count
    energy_cost_self: int = 0            # current skill energy cost
    energy_cost_reduction_self: int = 0  # cumulative energy cost reduction (base - current)
    energy_cost_opp: int = 0             # opponent skill total energy cost
    counter_succeeded: bool = False      # this skill countered the opponent's
    was_countered: bool = False          # this skill was countered
    prev_counter_succeeded: bool = False # previous turn counter succeeded
    damage_taken_this_turn: int = 0      # number of hits taken this turn
    damage_reduced_self: int = 0         # damage reduced this turn
    devotion_triggered: bool = False     # devotion triggered this action
    prev_skill_type: str = ""            # previous skill type (for prev_skill_is)
    target_fainted: bool = False         # target fainted from this skill
    prev_damage_taken_self: bool = False # self took damage last turn
    prev_damage_taken_opp: bool = False  # opponent took damage last turn

    # ── 技能追踪 ──
    skill_index: int = 0                 # position in skill list (0-indexed)
    skill_position_changed: bool = False # skill position changed (count only)
    last_tick_damage_self: int = 0       # most recent tick damage taken
    last_tick_damage_opp: int = 0        # most recent tick damage dealt

    # ── 战场 ──
    weather: str = ""                    # current weather
    last_tick_abnormal: str = ""         # most recent tick abnormal name
    last_tick_target: str = ""           # most recent tick target ("sprite_self" | "sprite_opp")
    abnormal_changed_name: str = ""      # most recent abnormal stack change name
    abnormal_changed_target: str = ""    # target of most recent abnormal change
    abnormal_applied_name: str = ""      # most recent abnormal application name
    abnormal_applied_target: str = ""    # target of most recent abnormal application
    skills_energy_changed_of: str = ""   # whose skill energy changed ("sprite_self" | "sprite_opp")
    positive_changed_of: str = ""        # whose positive count changed
    energy_changed_of: str = ""          # whose energy changed
    turn_end: bool = False               # turn-end settlement signal (count only)
    turn: int = 0
    is_first: bool = False               # this skill is first action this turn

    # ── 计次器快照 ──
    counter_values: dict[str, int] = field(default_factory=dict)  # {name: count}


# ── ADDRESS_MAP ──
# Maps (of, q) tuples to Ctx field names. The resolve() function uses this
# for O(1) field lookup instead of linear search.

ADDRESS_MAP: dict[tuple[str, str], str] = {
    # sprite_self
    ("sprite_self", "hp"):                 "hp_self",
    ("sprite_self", "hp_ratio"):           "hp_self_ratio",
    ("sprite_self", "hp_missing_ratio"):   "hp_self_missing_ratio",
    ("sprite_self", "energy"):             "energy_self",
    ("sprite_self", "skills_energy_sum"):  "skills_energy_sum_self",
    ("sprite_self", "abnormal_count"):     "abnormal_count_self",
    ("sprite_self", "abnormal_stacks"):    "abnormal_stacks_self",
    ("sprite_self", "times_entered"):      "times_entered_self",
    ("sprite_self", "times_left"):         "times_left_self",
    ("sprite_self", "elements_used_count"):"elements_used_count_self",
    ("sprite_self", "positive_count"):     "positive_count_self",
    ("sprite_self", "zero_cost_skill_count"): "zero_cost_skill_count_self",
    ("sprite_self", "atk"):               "atk_self",
    ("sprite_self", "def"):               "def_self",
    ("sprite_self", "sp_atk"):            "sp_atk_self",
    ("sprite_self", "sp_def"):            "sp_def_self",
    ("sprite_self", "speed"):             "speed_self",
    ("sprite_self", "hp_max"):            "hp_self_max",
    ("sprite_self", "adjacent_power_sum"): "adjacent_power_sum",
    ("sprite_self", "damage_reduced"):     "damage_reduced_self",
    ("sprite_self", "damage_reduction"):   "damage_reduction_self",
    ("sprite_self", "last_tick_damage"):   "last_tick_damage_self",
    ("sprite_self", "charged"):            "charged_self",
    ("sprite_self", "is_charging"):        "is_charging_self",
    ("sprite_self", "first_action"):       "first_action_self",
    ("sprite_self", "energy_cost_sum"):    "energy_cost_sum_self",

    # sprite_opp
    ("sprite_opp", "hp"):                  "hp_opp",
    ("sprite_opp", "hp_ratio"):            "hp_opp_ratio",
    ("sprite_opp", "hp_missing_ratio"):    "hp_opp_missing_ratio",
    ("sprite_opp", "energy"):             "energy_opp",
    ("sprite_opp", "abnormal_count"):      "abnormal_count_opp",
    ("sprite_opp", "abnormal_stacks"):     "abnormal_stacks_opp",
    ("sprite_opp", "positive_count"):      "positive_count_opp",
    ("sprite_opp", "last_tick_damage"):    "last_tick_damage_opp",
    ("sprite_opp", "atk"):                 "atk_opp",
    ("sprite_opp", "def"):                 "def_opp",
    ("sprite_opp", "sp_atk"):              "sp_atk_opp",
    ("sprite_opp", "sp_def"):              "sp_def_opp",
    ("sprite_opp", "speed"):              "speed_opp",
    ("sprite_opp", "charged"):             "charged_opp",
    ("sprite_opp", "damage_reduction"):    "damage_reduction_opp",
    ("sprite_opp", "hp_max"):             "hp_opp_max",
    ("sprite_opp", "skills_energy_sum"):   "skills_energy_sum_opp",

    # battle
    ("battle", "abnormal_stacks"):         "abnormal_stacks_battle",
    ("battle", "weather"):                 "weather",

    # team_own
    ("team_own", "mark_count"):            "mark_count_own",
    ("team_own", "skill_count"):           "skill_count_own",
    ("team_own", "devotion"):              "devotion_own",
    ("team_own", "fainted"):              "fainted_own",
    ("team_own", "burst_triggered_count"): "burst_triggered_count_own",

    # team_opp
    ("team_opp", "mark_count"):            "mark_count_opp",
    ("team_opp", "devotion"):              "devotion_opp",
    ("team_opp", "fainted"):              "fainted_opp",

    # team_both
    ("team_both", "mark_count"):           "mark_count_both",

    # skill_off_0 (current attacking skill)
    ("skill_off_0", "power_base"):         "power_self",
    ("skill_off_0", "element"):            "element_self",
    ("skill_off_0", "adjacent_power_sum"): "adjacent_power_sum",
    ("skill_off_0", "combo_current"):      "combo_self",
    ("skill_off_0", "energy_cost"):        "energy_cost_self",
    ("skill_off_0", "counter_value"):      "counter_values",
    ("skill_off_0", "energy_cost_reduction"): "energy_cost_reduction_self",

    # skill_opp_current
    ("skill_opp_current", "power_base"):   "power_opp",
    ("skill_opp_current", "element"):       "element_opp",
    ("skill_opp_current", "energy_total"): "energy_cost_opp",
}
