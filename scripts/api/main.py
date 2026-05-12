import json
import uuid
import random
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Ensure we can import from scripts
import sys
BASE = Path(__file__).resolve().parent.parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from scripts.sim.factory import SimFactory
from scripts.sim.agent import RuleAgent
from scripts.sim.battle import Battle
from scripts.sim.resolver import _TYPE_CHART
from scripts.sim.player import Player, PlayStyle
from scripts.sim.action import Action
from scripts.sim.sprite import Sprite
from scripts.sim.skill import Skill
from scripts.sim.battleskill import BattleSkill
from scripts.common.models import SpeciesStats

app = FastAPI(title="Roco Battle API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WIKI_ROOT = BASE / "wiki"
SKILLS_DIR = BASE / "data" / "skills"

# In-memory session store
sessions: Dict[str, dict] = {}
debug_sessions: Dict[str, dict] = {}

def load_sprite_skills() -> List[dict]:
    available = {p.stem for p in SKILLS_DIR.glob("*.json")}
    entries = []

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

        # Parse element from frontmatter attributes: [光] -> 光
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
            entries.append({
                "name": sprite_name,
                "element": element,
                "number": int(frontmatter.get("number", 0)),
                "skills": skills,
            })

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
        parts.append(f"造成物伤")
    elif skill_type == '魔攻':
        parts.append(f"造成魔伤")

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

# --- Pydantic Models ---

class SpriteSelection(BaseModel):
    name: str
    skills: List[str]

class InitRequest(BaseModel):
    team: List[SpriteSelection]
    opponent_team: Optional[List[SpriteSelection]] = None
    lead_index: int = 0

class ActionRequest(BaseModel):
    session_id: str
    action_type: str # "skill", "switch", "gather", "item"
    skill_name: Optional[str] = None
    switch_index: Optional[int] = None

class DebugActionRequest(BaseModel):
    session_id: str
    action_a: dict  # {type: "skill"|"switch"|"gather", skill_name?: str, switch_index?: int}
    action_b: dict  # same structure

# --- Helper Functions ---

def serialize_battle_state(battle: Battle, session_id: str) -> dict:
    pa = battle.player_a
    pb = battle.player_b
    
    def serialize_sprite(s):
        charging = getattr(s, '_charging', False)
        charged_idx = getattr(s, '_charged_skill_index', -1)
        charged_name = ''
        if charging and 0 <= charged_idx < len(s.skills):
            charged_name = s.skills[charged_idx].name
        return {
            "name": s.name,
            "element": s.species.attributes,
            "current_hp": s.current_hp,
            "max_hp": s.max_hp,
            "energy": s.energy,
            "is_fainted": s.is_fainted,
            "charging": charged_name if charging else '',
            "trait": s.species.ability or '',
            "energy_cost_mod": s.energy_cost_mod,
            "effects": [{"name": e.name, "category": e.category, "stacks": e.stacks, "steps": e.steps} for e in s.effects],
            "skills": [skill.name for skill in s.skills]
        }
        
    def serialize_player(p):
        return {
            "name": p.name,
            "active_index": p.active_index,
            "lives": p.lives,
            "team": [serialize_sprite(s) for s in p.team]
        }
        
    marks_a_pos, marks_a_neg = battle.globals.get_marks("A")
    marks_b_pos, marks_b_neg = battle.globals.get_marks("B")
    
    return {
        "session_id": session_id,
        "turn": battle.turn,
        "is_finished": battle.is_finished,
        "winner": battle.winner,
        "weather": battle.globals.weather,
        "weather_turns": battle.globals.weather_turns,
        "player_a": serialize_player(pa),
        "player_b": serialize_player(pb),
        "marks_a": [{"name": m.name, "stacks": m.stacks, "type": "positive"} for m in marks_a_pos] + [{"name": m.name, "stacks": m.stacks, "type": "negative"} for m in marks_a_neg],
        "marks_b": [{"name": m.name, "stacks": m.stacks, "type": "positive"} for m in marks_b_pos] + [{"name": m.name, "stacks": m.stacks, "type": "negative"} for m in marks_b_neg],
        "mark_energy_mod_a": battle.globals.mark_energy_mod("A"),
        "mark_energy_mod_b": battle.globals.mark_energy_mod("B"),
    }

# --- Endpoints ---

@app.get("/api/sprites")
def get_sprites():
    return {"sprites": SPRITE_ENTRIES}

@app.get("/api/skills")
def get_skills():
    return {"skills": load_skill_metadata()}

@app.get("/api/type-chart")
def get_type_chart():
    return {"chart": _TYPE_CHART}

@app.post("/api/battle/init")
def init_battle(req: InitRequest):
    if not req.team:
        raise HTTPException(status_code=400, detail="Team cannot be empty")
        
    team_a_specs = [{"name": s.name, "skills": s.skills} for s in req.team]
    
    if req.opponent_team:
        team_b_specs = [{"name": s.name, "skills": s.skills} for s in req.opponent_team]
    else:
        # Generate random opponent team
        team_b_specs = []
        pool = list(SPRITE_ENTRIES)
        random.shuffle(pool)
        for entry in pool[:len(team_a_specs)]:
            chosen_skills = random.sample(entry["skills"], min(6, len(entry["skills"])))
            team_b_specs.append({"name": entry["name"], "skills": chosen_skills})
            
    player_a = FACTORY.build_player("玩家", team_a_specs)
    player_b = FACTORY.build_player("AI", team_b_specs, style=PlayStyle(aggression=0.7))

    # 设置首发
    li = req.lead_index
    if 0 <= li < len(player_a.team):
        player_a.active_index = li

    battle = FACTORY.build_battle(player_a, player_b)

    # Initialize RuleAgent for AI
    agent_b = RuleAgent("B", player_b)
    # AI 首发选择
    player_b.active_index = agent_b.choose_lead(battle)
    
    session_id = str(uuid.uuid4())
    sessions[session_id] = {
        "battle": battle,
        "agent_b": agent_b
    }
    
    return serialize_battle_state(battle, session_id)

@app.post("/api/battle/action")
def battle_action(req: ActionRequest):
    if req.session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
        
    session = sessions[req.session_id]
    battle: Battle = session["battle"]
    agent_b: RuleAgent = session["agent_b"]
    
    if battle.is_finished:
        return {"state": serialize_battle_state(battle, req.session_id), "log": []}
        
    # Construct player action
    if req.action_type == "skill":
        if not req.skill_name:
            raise HTTPException(status_code=400, detail="skill_name required for skill action")
            
        skill_idx = -1
        for i, skill in enumerate(battle.player_a.active.skills):
            if skill.name == req.skill_name:
                skill_idx = i
                break
                
        if skill_idx == -1:
            raise HTTPException(status_code=400, detail="Skill not found")
            
        action_a = Action(kind="skill", skill_index=skill_idx)
    elif req.action_type == "switch":
        if req.switch_index is None:
            raise HTTPException(status_code=400, detail="switch_index required for switch action")
        action_a = Action(kind="switch", switch_index=req.switch_index)
    elif req.action_type == "gather":
        action_a = Action(kind="gather")
    elif req.action_type == "item":
        action_a = Action(kind="item")
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action type: {req.action_type}")
        
    # Create a dummy agent for player A
    class DummyAgent:
        def __init__(self, team: str, action: Action):
            self.team = team
            self.action = action
            
        def choose_lead(self, battle):
            return 0
            
        def choose_action(self, battle):
            return self.action
            
        def choose_replacement(self, battle):
            # Fallback if needed
            for i, s in enumerate(battle.get_player(self.team).team):
                if not s.is_fainted:
                    return i
            return 0
            
        def on_game_end(self, winner):
            pass

    agent_a = DummyAgent("A", action_a)
    
    # Execute turn
    battle.execute_turn(agent_a, agent_b)
    
    # Get the latest turn record
    turn_log = []
    if battle.log:
        latest_record = battle.log[-1]
        turn_log = latest_record.events
        
    return {
        "state": serialize_battle_state(battle, req.session_id),
        "log": turn_log
    }

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


def _make_dummy_agent(team: str, action: Action):
    """Create an agent that returns a fixed action. Replacement picks first alive."""

    class DummyAgent:
        def __init__(self, t: str, a: Action):
            self.team = t
            self.action = a

        def choose_lead(self, battle):
            return 0

        def choose_action(self, battle):
            return self.action

        def choose_replacement(self, battle):
            for i, sp in enumerate(battle.get_player(self.team).team):
                if not sp.is_fainted:
                    return i
            return 0

        def on_game_end(self, winner):
            pass

    return DummyAgent(team, action)


def _action_from_dict(action_data: dict, player, label: str) -> Action:
    """Parse action dict into Action object."""
    atype = action_data.get('type', 'gather')
    if atype == 'skill':
        skill_name = action_data.get('skill_name', '')
        for i, skill in enumerate(player.active.skills):
            if skill.name == skill_name:
                return Action(kind='skill', skill_index=i)
        raise HTTPException(status_code=400, detail=f'{label}: skill {skill_name} not found')
    elif atype == 'switch':
        idx = action_data.get('switch_index', 0)
        return Action(kind='switch', switch_index=idx)
    elif atype == 'gather':
        return Action(kind='gather')
    else:
        raise HTTPException(status_code=400, detail=f'{label}: unknown action type {atype}')


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
    for name in ('debug_attack', 'debug_defense', 'debug_status'):
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
def debug_action(req: DebugActionRequest):
    if req.session_id not in debug_sessions:
        raise HTTPException(status_code=404, detail='Debug session not found')

    session = debug_sessions[req.session_id]
    battle: Battle = session['battle']

    if battle.is_finished:
        return {'state': serialize_battle_state(battle, req.session_id), 'log': []}

    action_a = _action_from_dict(req.action_a, battle.player_a, 'Player A')
    action_b = _action_from_dict(req.action_b, battle.player_b, 'Player B')

    agent_a = _make_dummy_agent('A', action_a)
    agent_b = _make_dummy_agent('B', action_b)

    battle.execute_turn(agent_a, agent_b)

    turn_log = []
    if battle.log:
        turn_log = battle.log[-1].events

    return {
        'state': serialize_battle_state(battle, req.session_id),
        'log': turn_log,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("scripts.api.main:app", host="0.0.0.0", port=8000, reload=True)
