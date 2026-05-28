"""Devotion system — team-level permanent buffs for the 虫 (Insect) element.

Five devotion types, each providing a different persistent benefit.
Random devotion picks from all available types.
"""

DEVOTION_TYPES: dict[str, dict] = {
    "连击数+1":  {"combo": 1},
    "能耗-2":    {"energy_cost": -2},
    "中毒两层":  {"abnormal": {"name": "中毒", "stacks": 2}},
    "威力+20":   {"power": 20},
    "10%吸血":   {"life_drain": 0.1},
}
