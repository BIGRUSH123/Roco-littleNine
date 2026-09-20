"""scripts/common/sprite_db.py — 精灵种族值数据库

直接操作 data/sprites/*.json 文件，按需读取。

数据模型（form / appearance 两维度）:
  form:       ''（基础阶段）或 '首领形态'（首领阶段）
  appearance: ''（默认外观）或「夏天的样子」「蜕皮时的样子」等外观名

旧数据兼容: 若 JSON 没有 appearance 字段、而 form 是外观名
（非 '' / '首领形态'），读取时自动规范化为 appearance=form、form=''。
"""

import json
import re
from pathlib import Path

from .models import SpeciesStats


def _normalize_form_appearance(data: dict) -> tuple[str, str]:
    """从原始 JSON 解析 (form, appearance)，兼容旧数据（外观写在 form 里）。"""
    form = str(data.get('form', '') or '').strip()
    appearance = str(data.get('appearance', '') or '').strip()
    if not appearance and form and '首领' not in form:
        # 旧数据：form 存的是外观名
        appearance, form = form, ''
    return form, appearance


class SpriteDB:
    """精灵种族值数据库。直接读写 data/sprites/ JSON 文件。"""

    _RE_FORM_SUFFIX = re.compile(r'（([^）]+)）$')

    def __init__(self, project_root: Path):
        self._dir = project_root / "data" / "sprites"
        self._by_display: dict[str, Path] = {}   # "name（appearance）" → filepath
        self._by_name: dict[str, list[Path]] = {} # name → [filepaths]
        self._by_number: dict[str, list[Path]] = {} # number → [filepaths]
        self._reload_index()

    def _reload_index(self) -> None:
        """扫描目录重建索引（轻量，不含文件内容）。"""
        self._by_display.clear()
        self._by_name.clear()
        self._by_number.clear()
        if not self._dir.is_dir():
            return
        for jf in self._dir.glob("*.json"):
            try:
                data = json.loads(jf.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                continue
            name = data.get('name', '').strip()
            if not name:
                continue
            _form, appearance = _normalize_form_appearance(data)
            number = str(data.get('number', '')).strip()
            display = f'{name}（{appearance}）' if appearance else name
            self._by_display[display] = jf
            self._by_name.setdefault(name, []).append(jf)
            if number:
                self._by_number.setdefault(number, []).append(jf)

    # ── 读取 ──

    def get(self, name: str, appearance: str = '') -> SpeciesStats | None:
        """精确查询：按 (name, appearance) 找到唯一形态。

        name 可写成 "名字（外观）"，也可 name + appearance 分开传。
        """
        m = self._RE_FORM_SUFFIX.search(name)
        if m:
            if not appearance:
                appearance = m.group(1)
            name = name[:m.start()].strip()

        display = f'{name}（{appearance}）' if appearance else name
        path = self._by_display.get(display)
        if path:
            return self._read_one(path)

        candidates = self._by_name.get(name, [])
        if len(candidates) == 1:
            return self._read_one(candidates[0])
        if candidates:
            # 多个外观：优先精确外观，其次默认外观，最后任意
            for p in candidates:
                s = self._read_one(p)
                if s and s.appearance == appearance:
                    return s
            for p in candidates:
                s = self._read_one(p)
                if s and not s.appearance:
                    return s
            return self._read_one(candidates[0])
        return None

    def get_by_stage(self, name: str, stage: str = '', appearance: str = '') -> SpeciesStats | None:
        """按 (名字, 阶段, 外观) 查询；stage='首领形态' 查首领阶段。"""
        candidates = self._by_name.get(name, [])
        exact, base = None, None
        for p in candidates:
            s = self._read_one(p)
            if s is None:
                continue
            if s.form == stage and s.appearance == appearance:
                return s
            if s.form == stage and exact is None and not s.appearance:
                exact = s
            if s.form == stage and base is None:
                base = s
        return exact or base

    def list_appearances(self, name: str) -> list[str]:
        """返回某个 base name 下的所有外观名（去重，含默认外观 ''）。"""
        return list(dict.fromkeys(
            s.appearance for p in self._by_name.get(name, [])
            if (s := self._read_one(p))
        ))

    # 兼容旧名（语义 = 外观）
    def list_forms(self, name: str) -> list[str]:
        return self.list_appearances(name)

    def leader_form_candidates(
        self, number: str, appearance: str = '',
    ) -> list[SpeciesStats]:
        """同编号首领形态候选列表（确定性排序，供玩家选择首领化目标）。

        排序规则：同外观优先 → 默认外观 → 其余（仅当前两类都为空时才用，
        即基础外观在首领形态里不存在时回退任意形态）。动作空间 17-21 需要
        「选哪个首领形态」（如圣光/圣水/圣火/圣草迪莫），故必须给出完整候选
        列表而非单个形态；调用方按序截断到 5 个槽位。
        """
        if not number:
            return []
        same: list[SpeciesStats] = []
        default: list[SpeciesStats] = []
        other: list[SpeciesStats] = []
        for p in self._by_number.get(number, []):
            s = self._read_one(p)
            if s is None or '首领' not in (s.form or ''):
                continue
            if appearance and s.appearance == appearance:
                same.append(s)
            elif not s.appearance:
                default.append(s)
            else:
                other.append(s)
        key = lambda s: (s.name, s.appearance)  # noqa: E731 — 确定性排序
        preferred = sorted(same, key=key) + sorted(default, key=key)
        return preferred if preferred else sorted(other, key=key)

    def get_alternate_species(
        self, species: SpeciesStats, appearance: str | None = None,
    ) -> SpeciesStats | None:
        """查找同一编号下的另一种形态（首领化目标）。

        appearance 指定时优先同外观；找不到则回退默认外观，再回退任意。
        """
        if not species.number:
            return None
        want = species.appearance if appearance is None else appearance
        same_appearance, default_look, any_other = None, None, None
        for p in self._by_number.get(species.number, []):
            s = self._read_one(p)
            if s is None or s.name == species.name:
                continue
            if s.appearance == want:
                same_appearance = same_appearance or s
            if not s.appearance:
                default_look = default_look or s
            any_other = any_other or s
        return same_appearance or default_look or any_other

    def lookup_by_number(self, number: str, appearance: str = '') -> SpeciesStats | None:
        """按精灵编号查找基础阶段形态（用于萌化退化查找 pre_species）。

        逐级回退：同外观基础形态 → 默认外观基础形态 → 任意基础形态；只有首领
        形态时返回 None（与 docstring 契约一致）。

        注意：首领形态的 pre_species 指向**自身编号**（语义「先退回同编号基础
        形态」），所以这里绝不能把首领形态当基础形态返回——否则调用方沿
        pre_species 向下走时会拿到同编号首领形态，循环原地打转。数据里大量
        家族的基础形态全都带外观（月相/季节/棋色/宝石…），没有 appearance=''
        的条目，正是旧实现「base_exact/base_any 皆空 → 兜底 boss_any」踩中的坑。
        """
        if not number:
            return None
        candidates = self._by_number.get(number, [])
        if not candidates:
            return None
        base_exact, base_default, base_any = None, None, None
        for p in candidates:
            s = self._read_one(p)
            if s is None:
                continue
            if '首领' in (s.form or ''):
                continue
            if s.appearance == appearance and base_exact is None:
                base_exact = s
            if not s.appearance and base_default is None:
                base_default = s
            base_any = base_any or s
        return base_exact or base_default or base_any

    # ── 写入 ──

    def save(self, species: SpeciesStats) -> None:
        """将 SpeciesStats 写回 JSON 文件，并更新索引。"""
        display = species.display_name()
        path = self._by_display.get(display)
        if not path:
            # 新文件
            filename = f'{species.number}_{species.name}'
            if species.appearance:
                filename += f'（{species.appearance}）'
            filename += '.json'
            path = self._dir / filename

        attr_list = [a.strip() for a in species.attributes.split(',') if a.strip()]
        data = {
            'number': species.number,
            'name': species.name,
            'form': species.form,
            'appearance': species.appearance,
            'attributes': attr_list,
            'hp': species.hp,
            'atk': species.atk,
            'sp_atk': species.sp_atk,
            'def': species.def_,
            'sp_def': species.sp_def,
            'speed': species.speed,
            'ability': species.ability,
            'ability_id': species.ability_id,
            'pre_species': species.pre_species,
            'bloodline_skills': species.bloodline_skills,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        self._by_display[display] = path
        self._by_name.setdefault(species.name, []).append(path)
        if species.number:
            self._by_number.setdefault(species.number, []).append(path)

    # ── 内部 ──

    @staticmethod
    def _read_one(path: Path) -> SpeciesStats | None:
        """从单个 JSON 文件读取 SpeciesStats（兼容旧 form=外观 数据）。"""
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return None
        name = data.get('name', '').strip()
        if not name:
            return None
        form, appearance = _normalize_form_appearance(data)
        attr_list = data.get('attributes', [])
        attr_str = ', '.join(attr_list) if isinstance(attr_list, list) else str(attr_list)
        bloodline = attr_list[0] if attr_list else ''
        bl_skills = data.get('bloodline_skills', {})
        if not isinstance(bl_skills, dict):
            bl_skills = {}
        try:
            return SpeciesStats(
                name=name, form=form, appearance=appearance,
                number=str(data.get('number', '')).strip(),
                hp=int(data.get('hp', 0)),
                atk=int(data.get('atk', 0)),
                sp_atk=int(data.get('sp_atk', 0)),
                def_=int(data.get('def', 0)),
                sp_def=int(data.get('sp_def', 0)),
                speed=int(data.get('speed', 0)),
                attributes=attr_str,
                bloodline=bloodline,
                ability=data.get('ability', '').strip(),
                ability_id=int(data.get('ability_id', 0)),
                pre_species=str(data.get('pre_species', '')).strip(),
                bloodline_skills=bl_skills,
            )
        except (ValueError, TypeError):
            return None
