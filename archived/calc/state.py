#!/usr/bin/env python3
"""
scripts/calc/state.py — 对局状态快照计算器

从对局记录中提取回合操作，结合 wiki 技能数据，逐回合计算状态快照并插入文件。

用法：
  python scripts/calc/state.py <record_path> [--dry-run]

退出码：
  0  成功（全部技能数据来自 wiki）
  1  解析失败
  2  有技能未在 wiki 中找到（降级为默认耗能，仍完成处理）
"""

import re
import sys
import os
from pathlib import Path

# Windows 终端默认 GBK，强制 UTF-8 输出避免编码错误
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Optional

BASE = Path(__file__).resolve().parent.parent.parent  # project root
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from scripts.common import (
    is_team_mark_effect, is_persistent_abnormal,
)

INIT_ENERGY = 10   # 开局满能量（固定值，非假设）
MAX_ENERGY  = 10   # 能量上限
CHARGE_GAIN = 5    # 聚能 +5


# ═══════════════════════════════════════════════
# 1. SkillDB — 技能数据库
# ═══════════════════════════════════════════════

@dataclass
class SkillInfo:
    name: str
    energy_cost: int
    skill_type: str        # 物攻 / 魔攻 / 防御 / 状态 / …
    power: int
    counter: str           # 无 / 攻击 / 防御 / 状态
    self_buffs:   list[str] = field(default_factory=list)
    opp_debuffs:  list[str] = field(default_factory=list)
    self_debuffs: list[str] = field(default_factory=list)
    opp_buffs:    list[str] = field(default_factory=list)


def _parse_yaml_list(value: str) -> list[str]:
    """解析 frontmatter 中的内联 YAML 列表，如 '["物攻+100%", "蓄势印记×3"]'。"""
    value = value.strip()
    if not value.startswith('[') or not value.endswith(']'):
        return []
    inner = value[1:-1].strip()
    if not inner:
        return []
    items = []
    for part in inner.split(','):
        item = part.strip().strip('"').strip("'")
        if item:
            items.append(item)
    return items


class SkillDB:
    """
    技能数据库。两个数据来源（优先级由高到低）：
      1. wiki/技能图鉴/**/*.md（frontmatter，含 buff/debuff 效果字段）
      2. wiki/meta/skills_all.csv（488 条技能，提供耗能/类型/威力回退）
    """

    def __init__(self, wiki_root: Path):
        self._db: dict[str, SkillInfo] = {}
        self.missing: set[str] = set()
        self._load(wiki_root / "技能图鉴")
        self._load_csv(wiki_root / "meta" / "skills_all.csv")

    def _load(self, skill_dir: Path) -> None:
        if not skill_dir.is_dir():
            _err(f"[警告] 技能图鉴目录不存在: {skill_dir}")
            return
        count = 0
        for md in skill_dir.rglob("*.md"):
            if md.name.startswith("_"):
                continue
            try:
                text = md.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if not text.startswith("---"):
                continue
            end = text.find("\n---", 3)
            if end == -1:
                continue
            fm_raw = text[4:end]
            # 解析 frontmatter：需区分普通字段（单值）和列表字段
            data: dict[str, str] = {}
            for line in fm_raw.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    data[k.strip()] = v.strip()
            name = data.get("name", "").strip().strip('"').strip("'")
            if not name:
                continue
            try:
                self._db[name] = SkillInfo(
                    name=name,
                    energy_cost=int(data.get("energy_cost", "0").strip('"').strip("'")),
                    skill_type=data.get("type", "").strip('"').strip("'"),
                    power=int(data.get("power", "0").strip('"').strip("'")),
                    counter=data.get("counter", "无").strip('"').strip("'"),
                    self_buffs=_parse_yaml_list(data.get("self_buffs", "[]")),
                    opp_debuffs=_parse_yaml_list(data.get("opp_debuffs", "[]")),
                    self_debuffs=_parse_yaml_list(data.get("self_debuffs", "[]")),
                    opp_buffs=_parse_yaml_list(data.get("opp_buffs", "[]")),
                )
                count += 1
            except (ValueError, TypeError):
                pass
        _err(f"[SkillDB] wiki 加载 {count} 个技能")

    def _load_csv(self, csv_path: Path) -> None:
        """从 skills_all.csv 加载未被 wiki 覆盖的技能（仅补充耗能/类型/威力）。"""
        import csv as _csv
        if not csv_path.is_file():
            _err(f"[SkillDB] 未找到 CSV 回退: {csv_path}")
            return
        count = 0
        try:
            with open(csv_path, encoding='utf-8-sig', newline='') as f:
                for row in _csv.DictReader(f):
                    name = row.get('技能名', '').strip()
                    if not name or name in self._db:
                        continue
                    try:
                        self._db[name] = SkillInfo(
                            name=name,
                            energy_cost=int(row.get('耗能', '0') or '0'),
                            skill_type=row.get('类型', '').strip(),
                            power=int(row.get('威力', '0') or '0'),
                            counter='无',  # CSV 无应对数据
                        )
                        count += 1
                    except (ValueError, TypeError):
                        pass
        except OSError as e:
            _err(f"[SkillDB] 读取 CSV 失败: {e}")
            return
        _err(f"[SkillDB] CSV 回退补充 {count} 个技能")

    # 归一化技能名：剥除「（武系）」「（光系）」等属性后缀
    _RE_ATTR_SUFFIX = re.compile(r'（[^）]{1,6}）$')

    def _normalize(self, name: str) -> str:
        """'愿力冲击（武系）' → '愿力冲击'，其他名称不变。"""
        return self._RE_ATTR_SUFFIX.sub('', name).strip()

    def get(self, name: str) -> Optional[SkillInfo]:
        return self._db.get(self._normalize(name))

    def get_cost(self, name: str) -> tuple[int, str]:
        """返回 (cost, warn_flag)。warn_flag 非空表示使用了估算值。"""
        norm = self._normalize(name)
        info = self._db.get(norm)
        if info:
            return info.energy_cost, ""
        self.missing.add(name)
        default = 3
        return default, f"[技能未找到:{name},默认{default}费]"

    def is_defense(self, name: str) -> bool:
        info = self._db.get(self._normalize(name))
        return info is not None and "防御" in info.skill_type


# ═══════════════════════════════════════════════
# 2. PetState — 精灵状态
# ═══════════════════════════════════════════════

@dataclass
class PetState:
    name: str
    owner: str                                   # 完整玩家名
    energy: int = INIT_ENERGY
    energy_assumed: bool = False                 # 开局固定10，不需要假设标注
    hp_str: str = "?"
    buffs: list[str] = field(default_factory=list)
    debuffs: list[str] = field(default_factory=list)
    defense_cd: bool = False
    fainted: bool = False

    def clone(self) -> "PetState":
        return deepcopy(self)

    def energy_display(self) -> str:
        if self.energy_assumed:
            return f"{self.energy}[假设]"
        return str(self.energy)


# ═══════════════════════════════════════════════
# 3. Operation — 单次回合操作
# ═══════════════════════════════════════════════

@dataclass
class Operation:
    kind: str           # skill | switch | charge | wish | leader | unknown
    agent: str = ""     # 操作方（精灵名或玩家名前缀）
    skill_name: str = ""
    switch_from: str = ""
    switch_to: str = ""
    raw_line: str = ""


# ═══════════════════════════════════════════════
# 4. RecordParser — 对局记录解析器
# ═══════════════════════════════════════════════

class RecordParser:

    RE_ROUND_HEAD   = re.compile(r'^#### 回合\s+(\d+)\s*[—–-]?\s*(.*)', re.MULTILINE)
    RE_ON_FIELD     = re.compile(r'\*\*场上\*\*[：:]\s*(.+)')
    RE_FIELD_ENTRY  = re.compile(r'(\S+)\[([^\]→\n]+?)(?:\s*→\s*([^\]\n]+))?\]')
    RE_OP_SECTION   = re.compile(r'\*\*操作\*\*[：:](.*?)(?=\n-\s*\*\*|\Z)', re.DOTALL)
    RE_SWITCH       = re.compile(r'换宠\s+`([^→`\n]+?)\s*→\s*([^`\n]+)`')
    RE_SKILL        = re.compile(r'`([^`→\n]{1,25})`')
    RE_CHARGE       = re.compile(r'聚能')
    RE_WISH         = re.compile(r'愿力强化')
    RE_LEADER       = re.compile(r'首领化')
    RE_HAS_SNAP     = re.compile(r'<details>\s*\n\s*<summary><b>状态快照</b>')
    RE_LINEUP_TABLE = re.compile(r'### 双方阵容\s*\n((?:\|[^\n]+\n)+)')
    RE_PAREN_SUFFIX = re.compile(r'（[^）]*）')

    def parse(self, path: Path) -> tuple[dict, list[dict], str]:
        """
        Returns:
          lineup : {player_full_name: {pet_name: [skills]}}
          rounds : list[dict] — 回合解析结果
          text   : 原始文件文本
        """
        text = path.read_text(encoding="utf-8")
        lineup = self._parse_lineup(text)
        rounds = self._parse_rounds(text)
        return lineup, rounds, text

    # ── 阵容解析 ──────────────────────────────────

    def _parse_lineup(self, text: str) -> dict:
        lineup: dict[str, dict[str, list[str]]] = {}
        m = self.RE_LINEUP_TABLE.search(text)
        if not m:
            return lineup

        rows = [r for r in m.group(1).split('\n')
                if r.startswith('|') and '---' not in r and r.strip()]
        if len(rows) < 2:
            return lineup

        headers = [h.strip() for h in rows[0].split('|')[1:-1]]
        # Expected layout: 位置 | 玩家A | 技能配置 | 玩家B | 技能配置
        if len(headers) < 5:
            return lineup

        player_a, player_b = headers[1], headers[3]
        lineup[player_a] = {}
        lineup[player_b] = {}

        for row in rows[1:]:
            cols = [c.strip() for c in row.split('|')[1:-1]]
            if len(cols) < 5:
                continue
            pet_a = self.RE_PAREN_SUFFIX.sub('', cols[1]).strip()
            pet_b = self.RE_PAREN_SUFFIX.sub('', cols[3]).strip()
            skills_a = [s.strip() for s in re.split(r'\s*/\s*', cols[2])
                        if s.strip() not in ('', '—') and '未出场' not in s]
            skills_b = [s.strip() for s in re.split(r'\s*/\s*', cols[4])
                        if s.strip() not in ('', '—') and '未出场' not in s]
            if pet_a and pet_a != '—':
                lineup[player_a][pet_a] = skills_a
            if pet_b and pet_b != '—':
                lineup[player_b][pet_b] = skills_b

        return lineup

    # ── 回合解析 ──────────────────────────────────

    def _parse_rounds(self, text: str) -> list[dict]:
        rounds: list[dict] = []
        heads = list(self.RE_ROUND_HEAD.finditer(text))
        for i, m in enumerate(heads):
            num        = int(m.group(1))
            title      = m.group(2).strip()
            block_start = m.start()
            block_end   = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            block       = text[block_start:block_end]

            has_snap = bool(self.RE_HAS_SNAP.search(block))

            on_field   = self._parse_on_field(block)
            operations = self._parse_ops(block)

            rounds.append({
                'number':      num,
                'title':       title,
                'block_start': block_start,
                'block_end':   block_end,
                'has_snapshot': has_snap,
                'on_field':    on_field,
                'operations':  operations,
            })
        return rounds

    def _parse_on_field(self, block: str) -> dict:
        """返回 {owner_raw: {'from': pet_name, 'to': pet_name}}"""
        on_field: dict = {}
        m = self.RE_ON_FIELD.search(block)
        if not m:
            return on_field
        for pm in self.RE_FIELD_ENTRY.finditer(m.group(1)):
            owner     = pm.group(1).strip()
            pet_start = self.RE_PAREN_SUFFIX.sub('', pm.group(2)).strip()
            pet_end   = self.RE_PAREN_SUFFIX.sub('', pm.group(3) or pm.group(2)).strip()
            on_field[owner] = {'from': pet_start, 'to': pet_end}
        return on_field

    def _parse_ops(self, block: str) -> list[Operation]:
        ops: list[Operation] = []
        m = self.RE_OP_SECTION.search(block)
        if not m:
            return ops

        for line in m.group(0).split('\n'):
            stripped = line.strip()
            if not stripped.startswith('-') and not stripped.startswith('*'):
                continue
            clean = re.sub(r'^[-*•]\s+', '', stripped)

            # 1. 换宠
            sw = self.RE_SWITCH.search(clean)
            if sw:
                sw_from = self.RE_PAREN_SUFFIX.sub('', sw.group(1)).strip()
                sw_to   = self.RE_PAREN_SUFFIX.sub('', sw.group(2)).strip()
                ops.append(Operation(kind='switch', switch_from=sw_from,
                                     switch_to=sw_to, raw_line=clean))
                continue

            # 2. 聚能
            if self.RE_CHARGE.search(clean) and '消耗' not in clean:
                pm = re.match(r'^(\S+)\s+聚能', clean)
                agent = pm.group(1) if pm else ''
                ops.append(Operation(kind='charge', agent=agent, raw_line=clean))
                continue

            # 3. 愿力强化（0费特殊操作，可能同行紧跟愿力冲击）
            if self.RE_WISH.search(clean):
                pm = re.match(r'^(\S+)', clean)
                agent = pm.group(1) if pm else ''
                ops.append(Operation(kind='wish', agent=agent, raw_line=clean))
                # 若同行出现第二个技能（如 `愿力强化` → `愿力冲击`），单独补一个 skill 操作
                skills_after = [s for s in self.RE_SKILL.findall(clean)
                                if s != '愿力强化' and '→' not in s and len(s) <= 20]
                if skills_after:
                    ops.append(Operation(kind='skill', agent=agent,
                                         skill_name=skills_after[0], raw_line=clean))
                continue

            # 4. 首领化（0费特殊操作，最多使用一次）
            if self.RE_LEADER.search(clean):
                pm = re.match(r'^(\S+)', clean)
                agent = pm.group(1) if pm else ''
                ops.append(Operation(kind='leader', agent=agent, raw_line=clean))
                continue

            # 5. 技能（反引号内容）
            skills_found = [s for s in self.RE_SKILL.findall(clean)
                            if '→' not in s and len(s) <= 20]
            if skills_found:
                pm = re.match(r'^(\S+)\s+(?:选择\s+)?`', clean)
                agent = pm.group(1) if pm else ''
                # 取第一个技能（通常一行只有一个技能）
                ops.append(Operation(kind='skill', agent=agent,
                                     skill_name=skills_found[0], raw_line=clean))

        return ops


# ═══════════════════════════════════════════════
# 5. StateEngine — 状态推进引擎
# ═══════════════════════════════════════════════

class StateEngine:
    """
    状态存储：self._pool[owner][pet_name] = PetState
    支持双方拥有同名精灵（如双方都有翠顶夫人）。
    """

    def __init__(self, skill_db: SkillDB):
        self.skill_db = skill_db
        # 主状态池：{owner: {pet_name: PetState}}
        self._pool: dict[str, dict[str, PetState]] = {}
        # 当前场上：{owner: pet_name}
        self.field: dict[str, str] = {}
        # owner 短名到全名映射（"鬼叔" → "鬼叔黍"）
        self._owner_alias: dict[str, str] = {}
        # 队伍级别印记：{owner: [mark_str, ...]}，换宠后不清除
        self._team_marks: dict[str, list[str]] = {}
        # 愿力强化追踪（上限2次，两次间隔≥4回合）
        self._wish_uses: dict[str, int] = {}       # owner → 已使用次数
        self._wish_last_round: dict[str, int] = {} # owner → 最后使用的回合号（0=未使用）
        # 首领化追踪（全场上限1次）
        self._leader_used: dict[str, bool] = {}    # owner → 是否已使用

    # ── 初始化 ────────────────────────────────────

    def init_from_lineup(self, lineup: dict) -> None:
        self._pool.clear()
        self.field.clear()
        self._owner_alias.clear()
        self._team_marks = {player: [] for player in lineup}
        self._wish_uses = {player: 0 for player in lineup}
        self._wish_last_round = {player: 0 for player in lineup}
        self._leader_used = {player: False for player in lineup}

        for player, pets in lineup.items():
            self._pool[player] = {}
            # 构建短名别名（所有前缀）
            for l in range(1, len(player) + 1):
                prefix = player[:l]
                if prefix not in self._owner_alias:
                    self._owner_alias[prefix] = player

            first = True
            for pet_name in pets:
                state = PetState(
                    name=pet_name,
                    owner=player,
                    energy=INIT_ENERGY,
                    energy_assumed=False,   # 开局固定10，无假设
                )
                self._pool[player][pet_name] = state
                if first:
                    self.field[player] = pet_name
                    first = False

    # ── 辅助：owner 解析 ──────────────────────────

    def _resolve_owner(self, raw: str) -> str:
        """raw 可能是短名（"鬼叔"）或全名（"鬼叔黍"），返回 lineup 中的全名。"""
        if raw in self._pool:
            return raw
        if raw in self._owner_alias:
            return self._owner_alias[raw]
        return raw  # fallback

    # ── 辅助：状态查找 ────────────────────────────

    def _get_state(self, owner: str, pet_name: str) -> Optional[PetState]:
        """按 (owner, pet_name) 精确查找状态。owner 允许为短名。"""
        full_owner = self._resolve_owner(owner)
        return self._pool.get(full_owner, {}).get(pet_name)

    def _get_field_state(self, owner: str) -> Optional[PetState]:
        """返回 owner 当前场上精灵的状态。"""
        pet = self.field.get(owner)
        if pet is None:
            return None
        return self._pool.get(owner, {}).get(pet)

    def _get_field_states(self) -> list[PetState]:
        result = []
        for owner, pet_name in self.field.items():
            s = self._pool.get(owner, {}).get(pet_name)
            if s:
                result.append(s)
        return result

    def snapshot(self) -> list[PetState]:
        return [s.clone() for s in self._get_field_states()]

    def _resolve_pet_agent(self, agent: str) -> Optional[tuple[str, str]]:
        """
        将 agent 字符串解析为 (owner, pet_name)。
        优先匹配当前场上精灵，再匹配全部精灵。
        支持：精灵名、玩家短名、"玩家名+精灵名"拼接。
        """
        if not agent:
            return None
        clean = RecordParser.RE_PAREN_SUFFIX.sub('', agent).strip()

        # 1. 直接检查场上精灵（最常见情况）
        for owner, pet_name in self.field.items():
            if pet_name == clean or pet_name in clean:
                return (owner, pet_name)

        # 2. agent 可能是玩家短名 → 返回其场上精灵
        full_owner = self._resolve_owner(clean)
        if full_owner in self.field:
            pet = self.field[full_owner]
            return (full_owner, pet)

        # 3. 扫描全部精灵池（包含换下的精灵）
        for owner, pets in self._pool.items():
            for pet_name in pets:
                if pet_name == clean or pet_name in clean or clean in pet_name:
                    return (owner, pet_name)

        return None

    def _get_opponent_field_state(self, owner: str) -> Optional[PetState]:
        """找到 owner 的对手（场上精灵）状态。"""
        for other_owner, pet_name in self.field.items():
            if other_owner != owner:
                return self._pool.get(other_owner, {}).get(pet_name)
        return None

    def _get_opponent_owner(self, owner: str) -> Optional[str]:
        """找到 owner 的对手玩家名。"""
        for other_owner in self.field:
            if other_owner != owner:
                return other_owner
        return None

    def get_team_marks(self) -> dict[str, list[str]]:
        """返回当前所有队伍印记（供快照格式化使用）。"""
        return self._team_marks

    @staticmethod
    def _apply_effect(effect_list: list[str], effect: str) -> None:
        """
        将效果字符串追加到列表，处理印记叠层（X×N）的累加。
        格式：
          - "星陨印记×3"  → 与已有的"星陨印记×M"合并，变为"星陨印记×(M+3)"
          - "物攻+100%"   → 直接追加（不合并，可能存在多个同名 buff）
          - "能耗+2"      → 直接追加
        """
        # 检查是否是可叠层格式 "名称×N"
        m = re.match(r'^(.+?)×(\d+)$', effect)
        if m:
            mark_name, add_str = m.group(1), m.group(2)
            add_n = int(add_str)
            for i, existing in enumerate(effect_list):
                em = re.match(r'^(.+?)×(\d+)$', existing)
                if em and em.group(1) == mark_name:
                    effect_list[i] = f"{mark_name}×{int(em.group(2)) + add_n}"
                    return
        effect_list.append(effect)

    def _find_pet_owner(self, pet_name: str) -> Optional[str]:
        """在全部精灵池中按名称查找 owner。若同名，优先返回当前场上的那个。"""
        # 先查场上
        for owner, field_pet in self.field.items():
            if field_pet == pet_name:
                return owner
        # 再查全池
        for owner, pets in self._pool.items():
            if pet_name in pets:
                return owner
        # 模糊匹配
        for owner, pets in self._pool.items():
            for p in pets:
                if pet_name in p or p in pet_name:
                    return owner
        return None

    # ── 回合推进 ──────────────────────────────────

    def sync_field_from_on_field(self, on_field: dict) -> None:
        """根据 场上 信息把本回合起始精灵（'from'）同步到 field。"""
        for raw_owner, info in on_field.items():
            owner = self._resolve_owner(raw_owner)
            pet = info['from']
            if owner in self._pool and pet in self._pool[owner]:
                self.field[owner] = pet
            else:
                # 模糊匹配
                for o, pets in self._pool.items():
                    if o == owner or self._resolve_owner(raw_owner) == o:
                        for p in pets:
                            if pet in p or p in pet:
                                self.field[o] = p
                                break
                        break

    def begin_round(self) -> list[PetState]:
        """
        回合开始快照。
        能量不自动恢复，不做任何修改，直接返回当前状态快照。
        """
        return self.snapshot()

    def apply_operations(
        self, ops: list[Operation], on_field: dict, round_num: int = 0
    ) -> list[str]:
        """
        执行回合操作，更新状态。返回能量变化注释列表（含警告）。
        round_num  : 当前回合号，用于愿力强化 CD 检查。
        on_field   : 回合末同步换宠后的 field 状态。
        防御CD规则 : 使用防御技能后，下一回合不可用；换宠入场时 CD 重置。
        愿力强化   : 全场≤2次，两次间隔≥4回合（即中间隔3个回合）；与首领化互斥。
        首领化     : 全场≤1次；与愿力强化互斥。
        """
        notes:    list[str] = []
        warnings: list[str] = []

        # 记录本回合使用了防御技能的精灵，用于回合末更新 defense_cd
        used_defense: set[tuple[str, str]] = set()   # (owner, pet_name)

        for op in ops:

            if op.kind == 'switch':
                new_pet = op.switch_to
                # 找到换入方
                owner = self._find_pet_owner(new_pet)
                if owner and new_pet in self._pool.get(owner, {}):
                    self.field[owner] = new_pet
                    s = self._pool[owner][new_pet]
                    s.defense_cd = False
                    # 换宠：清除场上增益/减益，但保留异常状态（中毒/灼烧/冻结等）
                    s.buffs.clear()
                    s.debuffs = [d for d in s.debuffs if is_persistent_abnormal(d)]
                    notes.append(f"换入{new_pet}(E={s.energy_display()})")
                else:
                    # 模糊匹配
                    found = False
                    for o, pets in self._pool.items():
                        for p in pets:
                            if new_pet in p or p in new_pet:
                                self.field[o] = p
                                s = pets[p]
                                s.defense_cd = False
                                s.buffs.clear()
                                s.debuffs = [d for d in s.debuffs if is_persistent_abnormal(d)]
                                notes.append(f"换入{p}(E={s.energy_display()})")
                                found = True
                                break
                        if found:
                            break
                    if not found:
                        notes.append(f"[换入未知精灵:{new_pet}]")

            elif op.kind == 'charge':
                result = self._resolve_pet_agent(op.agent)
                if result:
                    owner, pet_name = result
                    s = self._pool[owner][pet_name]
                    old_e = s.energy
                    s.energy = min(MAX_ENERGY, s.energy + CHARGE_GAIN)
                    notes.append(f"{pet_name} 聚能+{CHARGE_GAIN}: {old_e}→{s.energy}")
                else:
                    notes.append(f"[聚能:找不到精灵 agent={op.agent!r}]")

            elif op.kind == 'skill':
                result = self._resolve_pet_agent(op.agent)
                skill  = op.skill_name
                if not result:
                    notes.append(f"[技能{skill!r}:找不到精灵 agent={op.agent!r}]")
                    continue

                owner, pet_name = result
                s = self._pool[owner][pet_name]
                cost, warn = self.skill_db.get_cost(skill)
                if warn:
                    warnings.append(warn)

                # 防御 CD 检测（使用上一回合遗留的 defense_cd 标志）
                is_def = self.skill_db.is_defense(skill)
                if is_def and s.defense_cd:
                    warnings.append(f"[CD冲突:{pet_name} 防御CD中使用{skill}]")
                if is_def:
                    used_defense.add((owner, pet_name))

                # 能量不足检测
                if s.energy < cost:
                    warnings.append(f"[能量冲突:{pet_name} E={s.energy} < {skill} 费{cost}]")

                old_e = s.energy
                s.energy = max(0, s.energy - cost)

                flag = "[假设耗能]" if warn else ""
                notes.append(f"{pet_name} -{cost}{flag}({skill}): {old_e}→{s.energy}")

                # 应用技能 buff/debuff 效果
                info = self.skill_db.get(skill)
                if info:
                    # 自身效果：队伍印记→_team_marks，其余→精灵状态
                    for eff in info.self_buffs:
                        if is_team_mark_effect(eff):
                            self._apply_effect(
                                self._team_marks.setdefault(owner, []), eff)
                        else:
                            self._apply_effect(s.buffs, eff)
                    for eff in info.self_debuffs:
                        if is_team_mark_effect(eff):
                            self._apply_effect(
                                self._team_marks.setdefault(owner, []), eff)
                        else:
                            self._apply_effect(s.debuffs, eff)
                    # 对手效果：队伍印记→对手_team_marks，其余→对手精灵状态
                    if info.opp_debuffs or info.opp_buffs:
                        opp_owner = self._get_opponent_owner(owner)
                        opp_state = self._get_opponent_field_state(owner)
                        for eff in info.opp_debuffs:
                            if is_team_mark_effect(eff) and opp_owner:
                                self._apply_effect(
                                    self._team_marks.setdefault(opp_owner, []), eff)
                            elif opp_state:
                                self._apply_effect(opp_state.debuffs, eff)
                        for eff in info.opp_buffs:
                            if is_team_mark_effect(eff) and opp_owner:
                                self._apply_effect(
                                    self._team_marks.setdefault(opp_owner, []), eff)
                            elif opp_state:
                                self._apply_effect(opp_state.buffs, eff)

            elif op.kind == 'wish':
                # 愿力强化：0费，约束验证
                result = self._resolve_pet_agent(op.agent)
                if result:
                    owner, pet_name = result
                else:
                    owner = self._resolve_owner(op.agent)
                    pet_name = self.field.get(owner, op.agent)

                uses     = self._wish_uses.get(owner, 0)
                last_r   = self._wish_last_round.get(owner, 0)
                # 互斥检验
                if self._leader_used.get(owner, False):
                    warnings.append(
                        f"[愿力冲突:{owner}] 已使用首领化，不能再用愿力强化")
                elif uses >= 2:
                    warnings.append(
                        f"[愿力冲突:{owner}] 愿力强化已用{uses}次(上限2次)")
                elif last_r > 0 and round_num - last_r < 4:
                    gap = round_num - last_r
                    warnings.append(
                        f"[愿力冲突:{owner}] 间隔不足(已{gap}回合,需≥4回合间隔)")
                else:
                    self._wish_uses[owner] = uses + 1
                    self._wish_last_round[owner] = round_num
                    notes.append(
                        f"[愿力强化] {pet_name}(第{uses + 1}次,回合{round_num})")

            elif op.kind == 'leader':
                # 首领化：0费，约束验证
                result = self._resolve_pet_agent(op.agent)
                if result:
                    owner, pet_name = result
                else:
                    owner = self._resolve_owner(op.agent)
                    pet_name = self.field.get(owner, op.agent)

                if self._wish_uses.get(owner, 0) > 0:
                    warnings.append(
                        f"[首领化冲突:{owner}] 已使用愿力强化，不能再用首领化")
                elif self._leader_used.get(owner, False):
                    warnings.append(
                        f"[首领化冲突:{owner}] 已使用过首领化(上限1次)")
                else:
                    self._leader_used[owner] = True
                    notes.append(f"[首领化] {pet_name}")

        # 回合末：根据 on_field 'to' 同步换宠结果
        for raw_owner, info in on_field.items():
            pet_end   = info['to']
            pet_start = info['from']
            if pet_end == pet_start:
                continue
            owner = self._resolve_owner(raw_owner)
            if owner in self._pool and pet_end in self._pool[owner]:
                self.field[owner] = pet_end
                s_end = self._pool[owner][pet_end]
                s_end.defense_cd = False
                s_end.buffs.clear()
                s_end.debuffs = [d for d in s_end.debuffs if is_persistent_abnormal(d)]

        # 回合末：更新所有场上精灵的 defense_cd
        # 使用了防御技能的精灵 → True（下回合不可用）
        # 未使用的精灵 → False（冷却解除）
        for owner, pet_name in self.field.items():
            s = self._pool.get(owner, {}).get(pet_name)
            if s:
                s.defense_cd = (owner, pet_name) in used_defense

        if warnings:
            notes.extend(warnings)
        return notes


# ═══════════════════════════════════════════════
# 6. SnapshotFormatter — 快照格式化
# ═══════════════════════════════════════════════

class SnapshotFormatter:

    def format(
        self,
        turn: int,
        start_snap: list[PetState],
        end_snap:   list[PetState],
        notes: list[str],
        team_marks: dict[str, list[str]] | None = None,
    ) -> str:
        lines = ["<details>", "<summary><b>状态快照</b></summary>", ""]

        # 回合开始快照
        lines.append(f"**T{turn} 回合开始**")
        lines.append("| 精灵 | 方 | HP | 能量 | 增益 | 减益 |")
        lines.append("|------|----|----|------|------|------|")
        for s in start_snap:
            buffs   = "、".join(s.buffs)   if s.buffs   else "—"
            debuffs = "、".join(s.debuffs) if s.debuffs else "—"
            lines.append(f"| {s.name} | {s.owner} | {s.hp_str} | "
                         f"{s.energy_display()} | {buffs} | {debuffs} |")

        lines.append("")

        # 回合结束快照
        lines.append(f"**T{turn} 回合结束**")
        lines.append("| 精灵 | 方 | HP | 能量 | 增益 | 减益 | 防御CD |")
        lines.append("|------|----|----|------|------|------|--------|")
        for s in end_snap:
            buffs   = "、".join(s.buffs)   if s.buffs   else "—"
            debuffs = "、".join(s.debuffs) if s.debuffs else "—"
            cd      = "是" if s.defense_cd else "—"
            lines.append(f"| {s.name} | {s.owner} | {s.hp_str} | "
                         f"{s.energy_display()} | {buffs} | {debuffs} | {cd} |")

        lines.append("")

        # 队伍级别印记（不依附于具体精灵，换宠后保留）
        if team_marks:
            has_any = any(v for v in team_marks.values())
            if has_any:
                lines.append("**队伍印记**")
                lines.append("| 玩家 | 印记 |")
                lines.append("|------|------|")
                for owner, marks in team_marks.items():
                    mark_str = "、".join(marks) if marks else "—"
                    if marks:   # 只展示有印记的
                        lines.append(f"| {owner} | {mark_str} |")
                lines.append("")

        # 能量注释和警告
        regular  = [n for n in notes if not n.startswith("[") or
                    ("冲突" not in n and "未找到" not in n and "找不到" not in n)]
        warnings = [n for n in notes if n not in regular]
        if regular:
            lines.append(f"> **能量变化**：{'; '.join(regular)}")
        for w in warnings:
                lines.append(f"> [!] `{w}`")

        # 技能耗能估算声明（仅在有 [假设耗能] 时显示）
        if any("[假设耗能]" in n for n in notes):
            lines.append("> `[假设耗能]`：部分技能耗能来自估算（CSV/wiki 均未找到），请结合视频核实。")

        lines.append("")
        lines.append("</details>")

        return "\n".join(lines)


# ═══════════════════════════════════════════════
# 7. 插入位置计算
# ═══════════════════════════════════════════════

def find_insert_pos(block: str) -> int:
    """
    在回合块内找快照应插入的字符偏移量。
    规则：插入到回合内容末尾（紧接最后一个非空行之后），`---` 分隔符之前。
    """
    # 查找结尾的 \n\n---（回合间分隔符）
    trail = re.search(r'\n\n---\s*$', block.rstrip('\n '))
    if trail:
        return trail.start()
    # 无分隔符：追加到块末
    return len(block.rstrip('\n'))


# ═══════════════════════════════════════════════
# 8. 主流程
# ═══════════════════════════════════════════════

def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def process_record(record_path: Path, dry_run: bool = False) -> int:
    wiki_root = BASE / "wiki"
    skill_db  = SkillDB(wiki_root)

    parser = RecordParser()
    try:
        lineup, rounds, text = parser.parse(record_path)
    except Exception as e:
        _err(f"[错误] 解析失败: {e}")
        return 1

    if not lineup:
        _err("[错误] 无法解析双方阵容表格，请确认记录文件包含 '### 双方阵容' 节")
        return 1

    players = list(lineup.keys())
    _err(f"[解析] {len(rounds)} 个回合 | 玩家: {players}")

    engine    = StateEngine(skill_db)
    engine.init_from_lineup(lineup)
    formatter = SnapshotFormatter()

    # 收集需要插入的 (绝对偏移, markdown文本)，最后倒序插入以保持偏移正确
    insertions: list[tuple[int, str]] = []
    skipped = processed = 0

    for rnd in rounds:
        # 每回合开始前先同步 场上 首发精灵（确保引擎 field 与记录一致）
        engine.sync_field_from_on_field(rnd['on_field'])

        if rnd['has_snapshot']:
            # 已有快照：仍推进引擎状态保持连续性
            engine.begin_round()
            engine.apply_operations(rnd['operations'], rnd['on_field'],
                                    round_num=rnd['number'])
            skipped += 1
            continue

        start_snap = engine.begin_round()
        notes      = engine.apply_operations(rnd['operations'], rnd['on_field'],
                                             round_num=rnd['number'])
        end_snap   = engine.snapshot()

        snap_md = formatter.format(
            rnd['number'], start_snap, end_snap, notes,
            team_marks=engine.get_team_marks(),
        )

        block   = text[rnd['block_start']:rnd['block_end']]
        rel_pos = find_insert_pos(block)
        abs_pos = rnd['block_start'] + rel_pos

        insertions.append((abs_pos, "\n\n" + snap_md))
        processed += 1

    if not insertions:
        print(f"[完成] 无新增快照（{skipped} 回合已有快照）")
        return 0

    # 倒序插入，保持偏移不变
    insertions.sort(key=lambda x: x[0], reverse=True)
    result = text
    for pos, md in insertions:
        result = result[:pos] + md + result[pos:]

    conflict_count = sum(
        1 for _, md in insertions
        if "[能量冲突" in md or "[CD冲突" in md
    )

    if dry_run:
        print(result)
    else:
        record_path.write_text(result, encoding="utf-8")
        _err(f"[写入] {record_path}")

    missing_info = f"，技能缺失: {sorted(skill_db.missing)}" if skill_db.missing else ""
    print(
        f"[完成] 已处理 {processed} 回合，跳过 {skipped} 回合"
        f"，发现 {conflict_count} 处冲突{missing_info}"
    )

    return 2 if skill_db.missing else 0


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ('-h', '--help'):
        print(__doc__)
        sys.exit(0)

    dry_run   = '--dry-run' in args
    path_args = [a for a in args if not a.startswith('--')]

    if not path_args:
        _err("[错误] 未提供对局记录路径")
        sys.exit(1)

    record_path = Path(path_args[0])
    if not record_path.is_absolute():
        record_path = BASE / record_path

    if not record_path.exists():
        _err(f"[错误] 文件不存在: {record_path}")
        sys.exit(1)

    sys.exit(process_record(record_path, dry_run=dry_run))


if __name__ == "__main__":
    main()
