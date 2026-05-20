"""scripts/common/sprite_db.py — 精灵种族值数据库

直接操作 data/sprites/*.json 文件，按需读取。
"""

import json
import re
from pathlib import Path

from .models import SpeciesStats


class SpriteDB:
    """精灵种族值数据库。直接读写 data/sprites/ JSON 文件。"""

    _RE_FORM_SUFFIX = re.compile(r'（([^）]+)）$')

    def __init__(self, project_root: Path):
        self._dir = project_root / "data" / "sprites"
        self._by_display: dict[str, Path] = {}   # "name（form）" → filepath
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
            form = data.get('form', '').strip()
            number = str(data.get('number', '')).strip()
            display = f'{name}（{form}）' if form else name
            self._by_display[display] = jf
            self._by_name.setdefault(name, []).append(jf)
            if number:
                self._by_number.setdefault(number, []).append(jf)

    # ── 读取 ──

    def get(self, name: str, form: str = '') -> SpeciesStats | None:
        """精确查询：按 (name, form) 找到唯一形态。"""
        m = self._RE_FORM_SUFFIX.search(name)
        if m:
            if not form:
                form = m.group(1)
            name = name[:m.start()].strip()

        display = f'{name}（{form}）' if form else name
        path = self._by_display.get(display)
        if path:
            return self._read_one(path)

        candidates = self._by_name.get(name, [])
        if len(candidates) == 1:
            return self._read_one(candidates[0])
        if candidates:
            # Multiple forms exist. Try exact form match first.
            for p in candidates:
                s = self._read_one(p)
                if s and s.form == form:
                    return s
            # Then try base form (empty form).
            for p in candidates:
                s = self._read_one(p)
                if s and s.form == '':
                    return s
            # Fallback: return first available form.
            return self._read_one(candidates[0])
        return None

    def list_forms(self, name: str) -> list[str]:
        """返回某个 base name 下的所有形态名（去重）。"""
        return list(dict.fromkeys(
            s.form for p in self._by_name.get(name, [])
            if (s := self._read_one(p))
        ))

    def get_alternate_species(self, species: SpeciesStats) -> SpeciesStats | None:
        """查找同一编号下的另一种形态（首领化目标）。"""
        if not species.number:
            return None
        for p in self._by_number.get(species.number, []):
            s = self._read_one(p)
            if s and s.name != species.name:
                return s
        return None

    def lookup_by_number(self, number: str, form: str = '') -> SpeciesStats | None:
        """按精灵编号查找基础形态（用于萌化退化查找 pre_species）。"""
        if not number:
            return None
        candidates = self._by_number.get(number, [])
        if not candidates:
            return None
        # 优先匹配 form（空字符串=基础形态）
        for p in candidates:
            s = self._read_one(p)
            if s and s.form == form:
                return s
        # 回退：返回第一个
        return self._read_one(candidates[0])

    # ── 写入 ──

    def save(self, species: SpeciesStats) -> None:
        """将 SpeciesStats 写回 JSON 文件，并更新索引。"""
        display = species.display_name()
        path = self._by_display.get(display)
        if not path:
            # 新文件
            filename = f'{species.number}_{species.name}'
            if species.form:
                filename += f'（{species.form}）'
            filename += '.json'
            path = self._dir / filename

        attr_list = [a.strip() for a in species.attributes.split(',') if a.strip()]
        data = {
            'number': species.number,
            'name': species.name,
            'form': species.form,
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
        """从单个 JSON 文件读取 SpeciesStats。"""
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return None
        name = data.get('name', '').strip()
        if not name:
            return None
        form = data.get('form', '').strip()
        attr_list = data.get('attributes', [])
        attr_str = ', '.join(attr_list) if isinstance(attr_list, list) else str(attr_list)
        bloodline = attr_list[0] if attr_list else ''
        bl_skills = data.get('bloodline_skills', {})
        if not isinstance(bl_skills, dict):
            bl_skills = {}
        try:
            return SpeciesStats(
                name=name, form=form,
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
