import json
import random
import re
import sys
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# Ensure we can import from backend
BASE = Path(__file__).resolve().parent.parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from backend.api import schemas
from backend.common.models import SpeciesStats
from backend.sim.action import Action
from backend.sim.battle import Battle
from backend.sim.battleskill import BattleSkill
from backend.sim.factory import SimFactory
from backend.sim.player import Player, PlayStyle
from backend.sim.resolver import _TYPE_CHART
from backend.sim.skill import Skill
from backend.sim.sprite import Sprite

# Agent registry: whitelist of registered AI agents.
# NEVER accept raw file paths — agents are loaded by registered name only.
AGENT_REGISTRY: dict[str, dict] = {
    "Random": {
        "name": "Random",
        "description": "随机选择合法操作。基准线对手，用于衡量其他 AI 的水平。",
        "source": "builtin",
        "module": "roco.ai.agent",
        "class": "RandomAgent",
    },
    "DamageAgent": {
        "name": "DamageAgent",
        "description": "始终选择第一个可用技能。简单攻击型 AI。",
        "source": "example",
        "module": None,
        "file": "examples/my_agent.py",
    },
    "RuleAgent": {
        "name": "RuleAgent",
        "description": "基于启发式评分的内置规则 AI。考虑伤害、属性克制、HP 阈值、道具使用。",
        "source": "builtin",
        "module": "backend.sim.agent",
        "class": "RuleAgent",
    },
    "HealBot": {
        "name": "HealBot",
        "description": "优先防御和回复。低 HP 时换宠，否则聚能。防御型 AI。",
        "source": "demo",
        "module": None,
        "file": "scripts/demo.py",
    },
    "AggroBot": {
        "name": "AggroBot",
        "description": "始终进攻。无视防御，优先选择技能攻击。攻击型 AI。",
        "source": "demo",
        "module": None,
        "file": "scripts/demo.py",
    },
}

app = FastAPI(title="Roco Battle API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/agents")
def get_agents():
    """Return registered AI agents (whitelist-based, no file path routing)."""
    agents = []
    for _name, info in AGENT_REGISTRY.items():
        agents.append({
            "name": info["name"],
            "description": info["description"],
            "source": info["source"],
        })
    return {"agents": agents}


@app.get("/api/agents/{name}")
def get_agent(name: str):
    """Return details for a specific registered agent."""
    info = AGENT_REGISTRY.get(name)
    if info is None:
        msg = f"Agent {name!r} not registered. Available: {', '.join(AGENT_REGISTRY.keys())}."
        raise HTTPException(status_code=404, detail=msg)
    return {
        "name": info["name"],
        "description": info["description"],
        "source": info["source"],
    }


WIKI_ROOT = BASE / "wiki"
SKILLS_DIR = BASE / "data" / "skills"

# In-memory session store
sessions: dict[str, dict] = {}
debug_sessions: dict[str, dict] = {}

def load_sprite_skills() -> list[schemas.SpriteEntry]:
    available = {p.stem for p in SKILLS_DIR.glob("*.json")}
    entries: list[schemas.SpriteEntry] = []

    sprite_dir = WIKI_ROOT / "精灵图鉴"
    if not sprite_dir.is_dir():
        return entries

    for md in sorted(sprite_dir.rglob("*.md")):
        if md.name.startswith("_") or md.stem == "index":
            continue
        text = md.read_text(encoding="utf-8", errors="ignore")

        # Parse frontmatter
        frontmatter = {}
        fm_match = re.search(r'^---\s*\n(.*?)\n---', text, re.DOTALL)
        if fm_match:
            for line in fm_match.group(1).split("\n"):
                kv = re.match(r'^(\w+):\s*(.+)$', line.strip())
                if kv:
                    frontmatter[kv.group(1)] = kv.group(2).strip()

        name_m = re.search(r'^name:\s*"(.+?)"', text, re.MULTILINE)
        sprite_name = name_m.group(1) if name_m else md.stem

        element = ""
        attrs_raw = frontmatter.get("attributes", "")
        attr_m = re.search(r'\[(\w+)', attrs_raw)
        if attr_m:
            element = attr_m.group(1)

        skills = []
        in_section = False
        for line in text.split("\n"):
            if re.match(r"## 技能", line):
                in_section = True
                continue
            if in_section:
                if line.startswith("## "):
                    break
                m = re.search(r"\[(?:[*]*)([^]]+?)(?:[*]*)\]\(([^)]+)\)", line)
                if m:
                    name = m.group(1).strip("*")
                    if name in available:
                        skills.append(name)

        if skills:
            entries.append(schemas.SpriteEntry(
                name=sprite_name,
                element=element,
                number=int(frontmatter.get("number", 0)),
                skills=skills,
            ))

    return entries

SPRITE_ENTRIES = load_sprite_skills()
FACTORY = SimFactory()

# Cache for skill metadata
_skill_cache: dict[str, dict] = {}
_wiki_desc: dict[str, str] = {}

def _load_wiki_descriptions() -> dict[str, str]:
    """Scan wiki/技能图鉴 for hand-written skill descriptions."""
    global _wiki_desc
    if _wiki_desc:
        return _wiki_desc
    wiki_skill_dir = WIKI_ROOT / '技能图鉴'
    if not wiki_skill_dir.is_dir():
        return {}
    for md in wiki_skill_dir.rglob('*.md'):
        if md.name.startswith('_'):
            continue
        try:
            text = md.read_text(encoding='utf-8', errors='ignore')
            m = re.search(r'^description:\s*"(.+?)"', text, re.MULTILINE)
            if m:
                _wiki_desc[md.stem] = m.group(1)
        except Exception:
            continue
    return _wiki_desc


def load_skill_metadata() -> dict[str, dict]:
    """Load all skill JSON metadata, keyed by skill name. Uses wiki description if available."""
    global _skill_cache
    if _skill_cache:
        return _skill_cache
    wiki_desc = _load_wiki_descriptions()
    for path in SKILLS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["description"] = wiki_desc.get(data["name"]) or _describe_skill(data)
            _skill_cache[data["name"]] = data
        except (json.JSONDecodeError, KeyError):
            continue
    return _skill_cache

def _describe_skill(s: dict) -> str:
    """Generate human-readable skill description from JSON data."""
    parts: list[str] = []
    skill_type = s.get('skill_type', '')
    power = s.get('power', 0)
    combo = s.get('combo', 1)
    effects: list[dict] = s.get('effects', [])

    # Damage type + combo
    if skill_type == '物攻':
        parts.append("造成物伤")
    elif skill_type == '魔攻':
        parts.append("造成魔伤")

    if combo > 1:
        if not parts:
            parts.append(f"{combo}连击")
        else:
            parts[-1] += f"，{combo}连击"

    # Describe non-increment effects
    incr_effects: list[str] = []
    for e in effects:
        kind = e.get('kind', '')
        name = e.get('name', '')
        if kind == 'special':
            if name == 'heal':
                v = e.get('value', 0)
                pct = f"{int(v * 100)}%" if v < 1 else f"{int(v)}"
                parts.append(f"回复{pct}HP")
            elif name == 'gain_energy':
                parts.append(f"回复{e.get('amount', 0)}能量")
            elif name == 'steal_energy':
                parts.append(f"偷取{e.get('amount', 0)}能量")
            elif name == 'life_drain':
                v = e.get('value', 0)
                pct = f"{int(v)}%" if v > 1 else f"{int(v * 100)}%"
                parts.append(f"吸血{pct}")
            elif name == 'charge':
                parts.append(f"蓄力{e.get('amount', 1)}回合")
            elif name == 'burst':
                parts.append("迸发")
            elif name == 'interrupt':
                parts.append("打断")
            elif name == 'reflect_damage':
                parts.append("反伤")
            elif name == 'counter_damage':
                parts.append("反击")
            elif name == 'multi_hit':
                parts.append(f"额外攻击{e.get('value', '')}次")
            elif name == 'escape':
                parts.append("使用后换宠")
            elif name == 'combo_increment':
                incr_effects.append(f"连击+{e.get('amount', 1)}")
            elif name == 'power_increment':
                incr_effects.append(f"威力+{e.get('amount', 0)}")
            elif name == 'energy_cost_increment':
                a = e.get('amount', 0)
                incr_effects.append(f"能耗{'+' if a >= 0 else ''}{a}")
            elif name == 'power_bonus':
                parts.append(f"威力+{e.get('amount', e.get('value', 0))}")
            elif name == 'direct_heal':
                parts.append(f"回复{e.get('amount', 0)}HP")
            elif name == 'dispel_positive':
                parts.append("驱散对方正面效果")
            elif name == 'dispel_negative':
                parts.append("驱散自身负面效果")
        elif kind == 'stat':
            stat_name = e.get('stat', '')
            steps = e.get('steps', 0)
            target = e.get('target', 'self')
            label = {'atk': '物攻', 'def': '物防', 'sp_atk': '魔攻', 'sp_def': '魔防',
                     'speed': '速度', 'power': '威力', 'priority': '先手',
                     'energy_cost': '能耗', 'combo': '连击', 'combo_mult': '连击倍率'}.get(stat_name, stat_name)
            if target == 'self':
                parts.append(f"自身{label}{'+' if steps > 0 else ''}{steps}级")
            else:
                parts.append(f"对方{label}{'+' if steps > 0 else ''}{steps}级")
        elif kind == 'abnormal':
            parts.append(f"使对方{e.get('name', '')}")
        elif kind == 'mark':
            parts.append(f"施加{e.get('name', '')}")
        elif kind == 'weather':
            parts.append(f"召唤{e.get('weather', '')}天气")

    if incr_effects:
        parts.append('每次使用后' + '，'.join(incr_effects))

    if not parts:
        return '无特殊效果'

    return '。'.join(parts) if '每次使用后' in (parts[-1] if parts else '') else '。'.join(filter(None, parts))

# --- Helper Functions ---

def _effect_category(e) -> str:
    from backend.vm.effect import AbnormalEffect, StatBuffEffect, StateEffect
    if isinstance(e, StatBuffEffect):
        return 'stat'
    if isinstance(e, AbnormalEffect):
        return 'abnormal'
    if isinstance(e, StateEffect):
        return e.state_type or 'state'
    return getattr(e, 'category', getattr(e, 'state_type', ''))


def _is_trait_metadata_effect(e) -> bool:
    """Return True for effects that are trait metadata, not visible buffs."""
    from backend.vm.effect import ModifierEffect, ObserverEffect
    if isinstance(e, ObserverEffect):
        return True
    if isinstance(e, ModifierEffect):
        return True
    return False


def _is_display_stat_effect(e) -> bool:
    """Return True for StatBuffEffect that is display-only (steps==0, has display_mult or display_value)."""
    from backend.vm.effect import StatBuffEffect
    if isinstance(e, StatBuffEffect) and e.steps == 0:
        if e.display_mult is not None or e.display_value is not None:
            return True
    return False


def _is_trait_stat_effect(e, ability: str) -> bool:
    """Return True for StatBuffEffect whose source is the sprite's trait/ability.

    These are already shown in the trait tooltip and should be hidden from the buff bar.
    """
    from backend.vm.effect import StatBuffEffect
    if not ability:
        return False
    if isinstance(e, StatBuffEffect) and e.steps != 0 and getattr(e, 'source', '') == ability:
        return True
    return False


def _extract_display_effects(sprite) -> list[schemas.EffectSummary]:
    """Extract display-only StatBuffEffects for trait tooltip display."""
    from backend.vm.effect import StatBuffEffect
    result = []
    for e in getattr(sprite, 'active_effects', []):
        if isinstance(e, StatBuffEffect) and e.steps == 0:
            if e.display_mult is not None or e.display_value is not None:
                result.append(schemas.EffectSummary(
                    name=e.name,
                    category='stat',
                    stacks=1,
                    steps=0,
                    display_mult=e.display_mult,
                    display_value=e.display_value,
                    source=getattr(e, 'source', ''),
                ))
    return result

def _compute_trait_info(sprite) -> schemas.TraitInfo | None:
    """Load trait definition and return TraitInfo for API serialization."""
    species = getattr(sprite, 'species', None)
    if not species:
        return None
    trait_name = species.ability or ''
    trait_id = getattr(species, 'ability_id', 0) if species else 0
    if not trait_name and not trait_id:
        return None

    from backend.engine.trait_loader import _trait_cache
    if trait_id and trait_id in _trait_cache:
        data = _trait_cache[trait_id]
    else:
        data_dir = Path(__file__).parent.parent.parent / "data" / "traits"
        fpath = data_dir / f"{trait_name}.json"
        if fpath.exists():
            data = json.loads(fpath.read_text("utf-8"))
        else:
            return schemas.TraitInfo(name=trait_name, description="", display_effects=[])

    return schemas.TraitInfo(
        name=data.get("name", trait_name),
        description=data.get("description", ""),
        display_effects=_extract_display_effects(sprite),
    )

def serialize_battle_state(battle: Battle, session_id: str) -> schemas.BattleState:
    pa = battle.player_a
    pb = battle.player_b

    # 确保位置效果缓存已初始化（首次序列化时 TurnPipeline 可能尚未运行）
    if not hasattr(battle, '_position_power_bonus'):
        from backend.sim.pipeline import TurnPipeline
        battle._position_power_bonus = TurnPipeline._scan_position_effects(battle)

    def _serialize_sprite(s, team='A') -> schemas.SpriteState:
        charging = getattr(s, '_charging', False)
        charged_idx = getattr(s, '_charged_skill_index', -1)
        charged_name = ''
        if charging and 0 <= charged_idx < len(s.skills):
            charged_name = s.skills[charged_idx].name

        mark_e_mod = battle.globals.mark_energy_mod(team)
        pos_bonus_map = getattr(battle, '_position_power_bonus', {})
        skills_data = []
        for i, sk in enumerate(s.skills):
            base_p = sk.base.power
            base_e = sk.base.energy_cost
            pos_bonus = pos_bonus_map.get((team, i), 0)
            perm_p = base_p + int(sk._modifiers.get("power", 0)) + s.power_mod * 10
            eff_p = perm_p + battle.globals.mark_power_bonus(team, sk.base) + pos_bonus
            # 轴承支撑被动：两侧技能能耗-1
            adj_ec = 0
            for offset in (-1, 1):
                ni = i + offset
                if 0 <= ni < len(s.skills) and s.skills[ni].name == '轴承支撑':
                    adj_ec = 1
                    break
            pending_ec = int(sum(m.value for m in s._pending_modifiers if getattr(m, 'stat', '') == 'energy_cost'))
            eff_e = max(0, base_e + s.energy_cost_mod + pending_ec + int(s._modifiers.get("energy_cost", 0)) + int(sk._modifiers.get("energy_cost", 0)) - mark_e_mod - adj_ec)
            skills_data.append(schemas.SkillSummary(
                name=sk.name,
                skill_index=i,
                base_power=base_p,
                effective_power=eff_p,
                position_power_bonus=pos_bonus,
                base_energy_cost=base_e,
                effective_energy_cost=eff_e,
                cooldown=sk.cooldown,
                sealed=sk.sealed,
                transmission=getattr(sk, '_transmission', 0),
                main_axis=(getattr(sk, '_transmission', 0) == -1),
                usable_while_charging=getattr(sk.base, 'usable_while_charging', False),
            ))
        return schemas.SpriteState(
            name=s.name,
            image_key=s.species.display_name(),
            element=s.species.attributes,
            bloodline=s.bloodline,
            bloodline_skills=s.bloodline_skills,
            current_hp=s.current_hp,
            max_hp=s.max_hp,
            energy=s.energy,
            is_fainted=s.is_fainted,
            charging=charged_name if charging else '',
            trait=_compute_trait_info(s),
            energy_cost_mod=s.energy_cost_mod,
            effects=[schemas.EffectSummary(
                name=e.name,
                category=_effect_category(e),
                stacks=getattr(e, 'stacks', 0),
                steps=getattr(e, 'steps', 0),
                display_mult=getattr(e, 'display_mult', None),
                source=getattr(e, 'source', ''),
            ) for e in getattr(s, 'active_effects', [])
             if e.name != '首领化'
             and not _is_trait_metadata_effect(e)
             and not _is_display_stat_effect(e)
             and not _is_trait_stat_effect(e, getattr(s.species, 'ability', ''))],
            skills=skills_data,
        )

    def _serialize_player(p, team='A') -> schemas.PlayerState:
        item_info = None
        if p.item:
            item_info = schemas.ItemState(
                name=p.item.name,
                max_uses=p.item.max_uses,
                uses=p.item.uses,
                cooldown_turns=p.item.cooldown_turns,
                last_use_turn=p.item.last_use_turn,
                is_exhausted=p.item.is_exhausted,
            )
        return schemas.PlayerState(
            name=p.name,
            active_index=p.active_index,
            lives=p.lives,
            item=item_info,
            team=[_serialize_sprite(s, team) for s in p.team],
        )

    marks_a_pos, marks_a_neg = battle.globals.get_marks("A")
    marks_b_pos, marks_b_neg = battle.globals.get_marks("B")

    return schemas.BattleState(
        session_id=session_id,
        turn=battle.turn,
        is_finished=battle.is_finished,
        winner=battle.winner,
        weather=battle.globals.weather,
        weather_turns=battle.globals.weather_turns,
        player_a=_serialize_player(pa, 'A'),
        player_b=_serialize_player(pb, 'B'),
        marks_a=[schemas.MarkSummary(name=m.name, stacks=m.stacks, type='positive') for m in marks_a_pos]
                + [schemas.MarkSummary(name=m.name, stacks=m.stacks, type='negative') for m in marks_a_neg],
        marks_b=[schemas.MarkSummary(name=m.name, stacks=m.stacks, type='positive') for m in marks_b_pos]
                + [schemas.MarkSummary(name=m.name, stacks=m.stacks, type='negative') for m in marks_b_neg],
        mark_energy_mod_a=battle.globals.mark_energy_mod("A"),
        mark_energy_mod_b=battle.globals.mark_energy_mod("B"),
    )

def _build_turn_snapshot(battle, turn_log):
    """Build a lightweight turn snapshot for timeline replay."""
    from backend.api import schemas as s

    sa = battle.player_a.active
    sb = battle.player_b.active

    def _snap(sprite):
        return s.SnapshotSprite(
            name=sprite.name,
            current_hp=sprite.current_hp,
            max_hp=sprite.max_hp,
            energy=sprite.energy,
            is_fainted=sprite.is_fainted,
            effects=[s.EffectSummary(
                name=e.name,
                category=_effect_category(e),
                stacks=getattr(e, 'stacks', 0),
                steps=getattr(e, 'steps', 0),
                display_mult=getattr(e, 'display_mult', None),
                source=getattr(e, 'source', ''),
            ) for e in getattr(sprite, 'active_effects', [])
             if e.name != '首领化'
             and not _is_trait_metadata_effect(e)
             and not _is_display_stat_effect(e)
             and not _is_trait_stat_effect(e, getattr(sprite.species, 'ability', ''))],
            skills=[s.SkillSummary(
                name=sk.name,
                skill_index=i,
                base_power=sk.base.power,
                effective_power=sk.base.power + int(sk._modifiers.get("power", 0)) + sprite.power_mod * 10,
                position_power_bonus=0,
                base_energy_cost=sk.base.energy_cost,
                effective_energy_cost=sk.base.energy_cost + int(sk._modifiers.get("energy_cost", 0)),
                cooldown=sk.cooldown,
                sealed=sk.sealed,
                transmission=0,
                main_axis=False,
            ) for i, sk in enumerate(sprite.skills)],
        )

    return s.TurnSnapshot(
        turn=battle.turn,
        self_sprite=_snap(sa),
        opp_sprite=_snap(sb),
        log_entries=list(turn_log) if turn_log else [],
    )

def _load_ai_agent(name: str, player):
    """Load an AI agent from the registry by name. Falls back to RuleAgent."""
    info = AGENT_REGISTRY.get(name)
    if info is None:
        from backend.sim.agent import RuleAgent
        return RuleAgent("B", player)

    source = info.get("source", "")
    if source == "builtin":
        mod_name = info.get("module", "")
        cls_name = info.get("class", "")
        import importlib
        mod = importlib.import_module(mod_name)
        cls = getattr(mod, cls_name)
        if cls_name == "RuleAgent":
            return cls("B", player)
        else:
            # SDK agent: adapt via bridge
            from roco.bridge import adapt_agent
            instance = cls() if isinstance(cls, type) else cls
            return adapt_agent(instance, "B")
    elif source in ("example", "demo"):
        file_path = info.get("file", "")
        from roco.tournament import load_agent
        agent = load_agent(file_path)
        from roco.bridge import adapt_agent
        return adapt_agent(agent, "B")
    else:
        from backend.sim.agent import RuleAgent
        return RuleAgent("B", player)


# --- Endpoints ---

@app.get("/api/sprites")
def get_sprites():
    return {"sprites": SPRITE_ENTRIES}

@app.get("/api/sprites/{name}/evolution")
def check_evolution(name: str):
    """检查精灵是否可使用进化之力（同编号有首领形态）。"""
    db = FACTORY.sprite_db
    species = db.get(name, '')
    if species is None:
        raise HTTPException(status_code=404, detail=f"Sprite {name!r} not found in the Pokedex. Check the name spelling or consult /api/sprites for available sprites.")
    for p in db._by_number.get(species.number, []):
        s = db._read_one(p)
        if s and '首领' in (s.form or ''):
            return {
                "sprite": name,
                "number": species.number,
                "can_evolve": True,
                "leader_form": s.display_name(),
                "leader_species": {
                    "name": s.name,
                    "form": s.form,
                    "hp": s.hp,
                    "atk": s.atk,
                    "sp_atk": s.sp_atk,
                    "def": s.def_,
                    "sp_def": s.sp_def,
                    "speed": s.speed,
                },
            }
    return {"sprite": name, "number": species.number, "can_evolve": False}

@app.get("/api/sprites/{name}/bloodlines")
def get_bloodlines(name: str):
    """获取精灵可选血脉系别。"""
    db = FACTORY.sprite_db
    species = db.get(name, '')
    if species is None:
        raise HTTPException(status_code=404, detail=f"Sprite {name!r} not found in the Pokedex. Check the name spelling or consult /api/sprites for available sprites.")
    # 默认血脉=第一属性，可选=bloodline_skills的所有key
    bl_skills = species.bloodline_skills or {}
    return {
        "sprite": name,
        "default_bloodline": species.elements[0] if species.elements else '',
        "available_bloodlines": list(bl_skills.keys()),
        "bloodline_skills": bl_skills,
    }

@app.get("/api/items")
def get_items():
    """返回可用道具列表。"""
    return {
        "items": [
            {
                "name": "愿力",
                "description": "用对应血脉的血脉技能替换一技能",
                "max_uses": 2,
                "cooldown_turns": 4,
                "cooldown_description": "两次使用需间隔3回合",
            },
            {
                "name": "进化之力",
                "description": "进化为同编号的首领形态，全属性+2级",
                "max_uses": 1,
                "cooldown_turns": 0,
                "cooldown_description": "全场仅可使用一次",
                "requirement": "仅限与首领形态同编号的精灵使用",
            },
        ]
    }

@app.get("/api/skills")
def get_skills():
    return {"skills": load_skill_metadata()}

@app.get("/api/type-chart")
def get_type_chart():
    return {"chart": _TYPE_CHART}

@app.post("/api/battle/init")
def init_battle(req: schemas.InitRequest):
    try:
        return _init_battle_impl(req)
    except Exception:
        import sys
        import traceback
        tb = traceback.format_exc()
        print(f"[ERROR] init_battle failed:\n{tb}", file=sys.stderr)
        raise HTTPException(status_code=500, detail=tb[:500])


def _init_battle_impl(req: schemas.InitRequest):
    if not req.team:
        raise HTTPException(status_code=400, detail="Team cannot be empty — at least one sprite with skills is required to start a battle.")

    team_a_specs = [{
        "name": s.name, "skills": s.skills,
        "bloodline": s.bloodline, "form": s.form,
    } for s in req.team]

    if req.opponent_team:
        team_b_specs = [{
            "name": s.name, "skills": s.skills,
            "bloodline": s.bloodline, "form": s.form,
        } for s in req.opponent_team]
    else:
        # Generate random opponent team
        team_b_specs = []
        pool = list(SPRITE_ENTRIES)
        random.shuffle(pool)
        for entry in pool[:len(team_a_specs)]:
            chosen_skills = random.sample(entry.skills, min(6, len(entry.skills)))
            team_b_specs.append({"name": entry.name, "skills": chosen_skills, "bloodline": None, "form": ""})

    # 道具
    from backend.sim.player import Item
    item = None
    if req.item == '愿力':
        item = Item.wish()
    elif req.item == '进化之力':
        item = Item.leader()

    player_a = FACTORY.build_player("玩家", team_a_specs, item=item)
    player_b = FACTORY.build_player("AI", team_b_specs, style=PlayStyle(aggression=0.7))

    # 设置首发
    li = req.lead_index
    if 0 <= li < len(player_a.team):
        player_a.active_index = li

    battle = FACTORY.build_battle(player_a, player_b)

    # Load AI agent from registry (default: RuleAgent)
    agent_b = _load_ai_agent(req.ai_agent or "RuleAgent", player_b)
    player_b.active_index = agent_b.choose_lead(battle)

    session_id = str(uuid.uuid4())
    sessions[session_id] = {
        "battle": battle,
        "agent_b": agent_b,
        "ai_agent_name": req.ai_agent or "RuleAgent",
    }

    return serialize_battle_state(battle, session_id)

@app.post("/api/battle/action")
def battle_action(req: schemas.ActionRequest):
    if req.session_id not in sessions:
        raise HTTPException(status_code=404, detail="Battle session not found — it may have expired. Start a new battle via POST /api/battle/init.")

    session = sessions[req.session_id]
    battle: Battle = session["battle"]
    agent_b: RuleAgent = session["agent_b"]

    if battle.is_finished:
        return {"state": serialize_battle_state(battle, req.session_id), "log": []}

    # 道具：只应用效果不执行回合，前端重新选择动作
    if req.action_type == "item":
        item_result = battle._resolve_item('A')
        # 延迟到下一回合日志中显示，避免出现在错误回合上下文
        session['pending_item_log'] = item_result or ''
        return {
            "state": serialize_battle_state(battle, req.session_id),
            "log": [],
        }

    # 验证请求参数，skill_index 由 DummyAgent 在传动后动态解析
    if req.action_type == "skill":
        if not req.skill_name:
            raise HTTPException(status_code=400, detail="Missing skill_name — required when action_type is 'skill'. Provide the name of a skill your active sprite knows.")
        found = any(skill.name == req.skill_name and not skill.sealed for skill in battle.player_a.active.skills)
        if not found:
            matched_sealed = any(skill.name == req.skill_name and skill.sealed for skill in battle.player_a.active.skills)
            if matched_sealed:
                raise HTTPException(status_code=400, detail=f"Skill {req.skill_name!r} is sealed and cannot be used.")
            raise HTTPException(status_code=400, detail=f"Skill {req.skill_name!r} not available — your active sprite does not know this skill.")
    elif req.action_type == "switch":
        if req.switch_index is None:
            raise HTTPException(status_code=400, detail="Missing switch_index — required when action_type is 'switch'. Provide the bench index of the sprite to switch to.")
    elif req.action_type not in ("gather",):
        raise HTTPException(status_code=400, detail=f"Unknown action_type {req.action_type!r}. Valid types: skill, switch, gather, item.")

    # 在 execute_turn 内部传动后才解析 skill_index（避免 position 过期）
    class DummyAgent:
        def __init__(self, team: str, kind: str, skill_name: str = '', switch_index: int = 0):
            self.team = team
            self._kind = kind
            self._skill_name = skill_name
            self._switch_index = switch_index

        def choose_lead(self, battle):
            return 0

        def choose_action(self, battle):
            if self._kind == 'skill' and self._skill_name:
                sprite = battle.get_player(self.team).active
                for i, sk in enumerate(sprite.skills):
                    if sk.name == self._skill_name:
                        return Action(kind='skill', skill_index=i)
                return Action(kind='gather')
            if self._kind == 'switch':
                return Action(kind='switch', switch_index=self._switch_index)
            return Action(kind=self._kind)

        def choose_replacement(self, battle):
            for i, s in enumerate(battle.get_player(self.team).team):
                if not s.is_fainted:
                    return i
            return 0

        def on_game_end(self, winner):
            pass

    agent_a = DummyAgent("A", req.action_type,
                         skill_name=req.skill_name or '',
                         switch_index=req.switch_index or 0)
    
    # Execute turn
    try:
        battle.execute_turn(agent_a, agent_b)
    except Exception:
        import sys
        import traceback
        tb = traceback.format_exc()
        print(f"[ERROR] battle.execute_turn failed:\n{tb}", file=sys.stderr)
        raise HTTPException(status_code=500, detail=tb[:500])
    
    # Get the latest turn record
    turn_log = []
    if battle.log:
        latest_record = battle.log[-1]
        turn_log = latest_record.to_frontend_events()
        # 将上一轮使用的道具日志插入本回合（在 >>>SPRITES: 标记之后）
        pending_item = session.pop('pending_item_log', None)
        if pending_item:
            # events[0]=回合标题, events[1]=>>>SPRITES:  — 道具标记插入在两者之后
            turn_log.insert(2, f'>>>ITEM:A:{pending_item}')
        
    turn_snap = _build_turn_snapshot(battle, turn_log)

    return {
        "state": serialize_battle_state(battle, req.session_id),
        "log": turn_log,
        "turn_snapshot": turn_snap,
    }

# ── Batch Battle Endpoint ──────────────────────────────────────────

@app.post("/api/battle/batch")
def batch_battle(req: schemas.BatchRequest):
    """Run multiple battles against a selected AI agent and return aggregate stats."""
    if not req.team:
        raise HTTPException(status_code=400, detail="Team cannot be empty — at least one sprite with skills is required.")
    if req.rounds < 1 or req.rounds > 100:
        raise HTTPException(status_code=400, detail="Rounds must be between 1 and 100.")

    team_specs = [{
        "name": s.name, "skills": s.skills,
        "bloodline": s.bloodline, "form": s.form,
    } for s in req.team]

    ai_name = req.ai_agent or "RuleAgent"

    import time
    wins = 0
    losses = 0
    draws = 0
    total_turns = 0
    total_ms = 0.0

    for _ in range(req.rounds):
        t0 = time.perf_counter()
        try:
            pa = FACTORY.build_player("玩家", team_specs)
            pb = FACTORY.build_player("AI", team_specs, style=PlayStyle(aggression=0.7))
            battle = FACTORY.build_battle(pa, pb)
            agent_b = _load_ai_agent(ai_name, pb)
            pb.active_index = agent_b.choose_lead(battle)

            from backend.sim.agent import RuleAgent as SimRuleAgent
            agent_a = SimRuleAgent("A", pa)
            pa.active_index = agent_a.choose_lead(battle)

            while not battle.is_finished:
                battle.execute_turn(agent_a, agent_b)

            total_turns += battle.turn
            if battle.winner == "玩家":
                wins += 1
            elif battle.winner == "AI":
                losses += 1
            else:
                draws += 1
        except Exception:
            losses += 1
        total_ms += (time.perf_counter() - t0) * 1000

    return schemas.BatchResult(
        rounds=req.rounds,
        wins=wins,
        losses=losses,
        draws=draws,
        win_rate=wins / req.rounds if req.rounds > 0 else 0.0,
        avg_turns=total_turns / req.rounds if req.rounds > 0 else 0.0,
        avg_duration_ms=total_ms / req.rounds if req.rounds > 0 else 0.0,
        ai_agent=ai_name,
    )


# ── Debug Mode Endpoints ───────────────────────────────────────────

def _make_debug_sprite(name: str, skills: list[BattleSkill], team: str) -> Sprite:
    """Create a tanky debug sprite with given skills."""
    species = SpeciesStats(
        name=name, number=999, hp=300, atk=60, sp_atk=60,
        def_=250, sp_def=250, speed=50, attributes='普通', ability='',
    )
    stats = {'hp': 999, 'atk': 60, 'sp_atk': 60, 'def': 250, 'sp_def': 250, 'speed': 50}
    s = Sprite(
        species=species, bloodline='普通', initial_stats=stats,
        current_hp=999, max_hp=999, energy=10,
    )
    s.skills = skills
    return s


def _make_dummy_agent(team: str, kind: str, skill_name: str = '', switch_index: int = 0):
    """Create an agent that resolves skill by name in choose_action (after transmission)."""

    class DummyAgent:
        def __init__(self, t: str, k: str, sn: str, si: int):
            self.team = t
            self._kind = k
            self._skill_name = sn
            self._switch_index = si

        def choose_lead(self, battle):
            return 0

        def choose_action(self, battle):
            if self._kind == 'skill' and self._skill_name:
                sprite = battle.get_player(self.team).active
                for i, sk in enumerate(sprite.skills):
                    if sk.name == self._skill_name:
                        return Action(kind='skill', skill_index=i)
                return Action(kind='gather')
            if self._kind == 'switch':
                return Action(kind='switch', switch_index=self._switch_index)
            return Action(kind=self._kind)

        def choose_replacement(self, battle):
            for i, sp in enumerate(battle.get_player(self.team).team):
                if not sp.is_fainted:
                    return i
            return 0

        def on_game_end(self, winner):
            pass

    return DummyAgent(team, kind, skill_name, switch_index)


def _parse_action_info(action_data: dict, player, label: str) -> dict:
    """Extract action info, validate skill exists. Returns {kind, skill_name, switch_index}.
    skill_index is NOT computed here — DummyAgent resolves it after transmission."""
    atype = action_data.get('type', 'gather')
    if atype == 'skill':
        skill_name = action_data.get('skill_name', '')
        found = any(skill.name == skill_name for skill in player.active.skills)
        if not found:
            raise HTTPException(status_code=400, detail=f"{label}: skill {skill_name!r} not available — the sprite does not know this skill.")
        return {'kind': 'skill', 'skill_name': skill_name, 'switch_index': 0}
    elif atype == 'switch':
        return {'kind': 'switch', 'skill_name': '', 'switch_index': action_data.get('switch_index', 0)}
    elif atype == 'gather':
        return {'kind': 'gather', 'skill_name': '', 'switch_index': 0}
    else:
        raise HTTPException(status_code=400, detail=f"{label}: unknown action_type {atype!r}. Valid types: skill, switch, gather.")


@app.post("/api/debug/init")
def debug_init():
    """Initialize a debug battle with two tanky sprites.
    Player A: all skills with special effects.
    Player B: 3 simple skills (attack, defense, status)."""

    # Load all skills, classify by whether they have special effects
    # Sort order: 物攻/魔攻 (0) < 防御 (1) < 状态 (2)
    _TYPE_ORDER = {'物攻': 0, '魔攻': 0, '动态攻击': 0, '防御': 1, '状态': 2}
    all_skills: list[BattleSkill] = []
    special_skills: list[BattleSkill] = []
    for path in sorted(SKILLS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            bs = BattleSkill(base=Skill.load(data))
            all_skills.append(bs)
            has_special = any(
                e.get('kind') == 'special'
                for e in data.get('effects', [])
            )
            if has_special:
                special_skills.append(bs)
        except (json.JSONDecodeError, KeyError):
            continue

    special_skills.sort(key=lambda bs: _TYPE_ORDER.get(bs.base.skill_type, 9))

    # Player B: 3 debug skills
    b_skills = []
    for name in ('丢冰块', '不动如山', '假寐'):
        found = [bs for bs in all_skills if bs.name == name]
        if found:
            b_skills.append(found[0])

    # Create sprites
    sprite_a = _make_debug_sprite('测试员A', special_skills, 'A')
    sprite_b = _make_debug_sprite('测试员B', b_skills, 'B')

    player_a = Player(name='我方(调试)', team=[sprite_a], style=PlayStyle())
    player_b = Player(name='对方(调试)', team=[sprite_b], style=PlayStyle())

    battle = FACTORY.build_battle(player_a, player_b)

    session_id = str(uuid.uuid4())
    debug_sessions[session_id] = {'battle': battle}

    result = serialize_battle_state(battle, session_id)
    result['debug_skills_a'] = [bs.name for bs in special_skills]
    result['debug_skills_b'] = [bs.name for bs in b_skills]
    return result


@app.post("/api/debug/action")
def debug_action(req: schemas.DebugActionRequest):
    if req.session_id not in debug_sessions:
        raise HTTPException(status_code=404, detail="Debug session not found — it may have expired. Start a new debug session via POST /api/debug/init.")

    session = debug_sessions[req.session_id]
    battle: Battle = session['battle']

    if battle.is_finished:
        return {'state': serialize_battle_state(battle, req.session_id), 'log': []}

    # 道具：先应用效果，然后用聚能替代执行回合
    item_log = []
    if req.action_a.get('type') == 'item':
        result = battle._resolve_item('A')
        if result:
            item_log.append(f'[A] {result}')
        req.action_a = dict(req.action_a, type='gather')
    if req.action_b.get('type') == 'item':
        result = battle._resolve_item('B')
        if result:
            item_log.append(f'[B] {result}')
        req.action_b = dict(req.action_b, type='gather')

    info_a = _parse_action_info(req.action_a, battle.player_a, 'Player A')
    info_b = _parse_action_info(req.action_b, battle.player_b, 'Player B')

    agent_a = _make_dummy_agent('A', info_a['kind'], info_a['skill_name'], info_a['switch_index'])
    agent_b = _make_dummy_agent('B', info_b['kind'], info_b['skill_name'], info_b['switch_index'])

    battle.execute_turn(agent_a, agent_b)

    turn_log = item_log[:]
    if battle.log:
        turn_log += battle.log[-1].to_frontend_events()

    turn_snap = _build_turn_snapshot(battle, turn_log)

    return {
        'state': serialize_battle_state(battle, req.session_id),
        'log': turn_log,
        'turn_snapshot': turn_snap,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.api.main:app", host="0.0.0.0", port=8000, reload=True)
