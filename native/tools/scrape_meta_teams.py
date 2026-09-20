# -*- coding: utf-8 -*-
"""native/tools/scrape_meta_teams.py — 爬取共享阵容 → 池子校验 → meta_teams.json。

数据源：rocopvp（洛克王国世界 PvP 助手，三个镜像域名同一部署）
  GET /api/popular/teams   → {"items": [...]}  每条 = 一支阵容（6 build）
  GET /api/popular/builds  → {"items": [...]}  每条 = 单个配置（含 tags/描述）

字段映射（站点 → 本项目 spec）：
  selectedSkillNames → skills；bloodline.attribute → bloodline（'首领血脉' → '首领'）
  statMarks.plusStats(3) → iv_fixed（本项目 IV 为 0-10 尺度，三项拉满）
  statMarks.extremeStat(加) + minusStat(减) → nature_fixed（NATURE_TABLE 查表）
  gameTeamCode 的「魔法：X」行 → item（进化之力 / 愿力）
  team.tags → archetype（比赛/排位/体系队/平衡队/科研/娱乐/情怀）

精灵解析优先级（站点名字可被玩家改，故不依赖名字）：
  1) 池内显示名精确命中（站点用「名字（外观）」写法，与池一致）
  2) SpriteDB 解析（含外观回退）
  3) creatureId 投票表（同 creatureId 在别处出现过池内名，等价于站点主键）
  4) 技能集合匹配（名字至少含 3 个该精灵技能，取最具体者）

技能合法性 = 池内技能 ∪ 所选血脉的血脉技能；不合法的技能被剔除并从池内同类型
技能补齐到 3 个（每处修补都记账，选队时优先低修补队）。

子命令（pwsh）:
  python native/tools/scrape_meta_teams.py fetch  [--cache PATH] [--refresh]
  python native/tools/scrape_meta_teams.py verify [--cache PATH] [--report PATH]
  python native/tools/scrape_meta_teams.py build  [--cache PATH] [--target 40]
                                                  [--out PATH] [--report PATH]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))
sys.stdout.reconfigure(encoding="utf-8")

ENDPOINTS = {
    "teams": "https://rocopvp.tzrain.wiki/api/popular/teams",
    "builds": "https://rocopvp.tzrain.wiki/api/popular/builds",
}
DEFAULT_CACHE = _PROJECT_ROOT / "backend" / "engine" / "ai" / "data" / "scraped_teams.json"
DEFAULT_OUT = _PROJECT_ROOT / "backend" / "engine" / "ai" / "data" / "meta_teams.json"
UA = "Roco-LittleNine-trainer/1.0 (+local training data collection)"

# 429/567 限流与 5xx 网关错误都退避重试（AGENTS.md 要求）
RETRY_CODES = {429, 567, 500, 502, 503, 504}

SITE_STAT = {
    "hp": "hp",
    "physicalAttack": "atk",
    "magicalAttack": "sp_atk",
    "physicalDefense": "def",
    "magicalDefense": "sp_def",
    "speed": "speed",
}
# 站点标签 → 训练价值权重（小 = 优先；比赛/排位是玩家实战阵，科研/娱乐偏整活）
TAG_RANK = {"比赛": 0, "排位": 0, "体系队": 1, "平衡队": 2, "科研": 3, "娱乐": 4, "情怀": 5}
# 站点「魔法」行 → 本项目道具名
SITE_ITEM = {"进化之力": "进化之力", "愿力强化": "愿力", "愿力": "愿力"}
# 改名 build 的常见修饰词（角色/属性词，剥离后提高名字命中率）
NAME_NOISE = ("物攻", "魔攻", "速度", "纯肉", "特攻", "肉", "极", "队-")


# ══════════════════════════════════════════════════
# 抓取（指数退避）
# ══════════════════════════════════════════════════
def fetch_json(url: str, *, attempts: int = 6, base_delay: float = 1.0,
               max_delay: float = 60.0, timeout: float = 30.0, log=print) -> dict:
    """GET JSON，429/567/5xx 与网络错误按指数退避重试。"""
    delay = base_delay
    last: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in RETRY_CODES:
                raise
            log(f"[fetch] HTTP {exc.code}（第 {i}/{attempts} 次），{delay:.1f}s 后重试")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            log(f"[fetch] {type(exc).__name__}: {exc}（第 {i}/{attempts} 次），{delay:.1f}s 后重试")
        if i < attempts:
            time.sleep(delay)
            delay = min(delay * 2, max_delay)
    raise RuntimeError(f"抓取失败（{attempts} 次）: {url}: {last}")


def cmd_fetch(args) -> int:
    cache = Path(args.cache)
    if cache.exists() and not args.refresh:
        print(f"缓存已存在: {cache}（加 --refresh 强制重抓）")
        return 0
    endpoints: dict[str, dict] = {}
    for key, url in ENDPOINTS.items():
        data = fetch_json(url, base_delay=args.base_delay)
        items = data.get("items", []) if isinstance(data, dict) else []
        if not items:
            print(f"{key} 抓取结果为空，保留旧缓存", file=sys.stderr)
            return 1
        endpoints[key] = {"source": url, "items": items}
        print(f"  {key}: {len(items)} 条")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(
        {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "endpoints": endpoints}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {cache}")
    return 0


def load_cache(path: str | Path) -> tuple[list[dict], list[dict], dict]:
    """→ (team_items, build_items, meta)。兼容裸 API 响应与旧版单端点缓存。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if "endpoints" in payload:
        eps = payload["endpoints"]
        meta = {"fetched_at": payload.get("fetched_at", ""), "source": ENDPOINTS["teams"]}
        return (eps.get("teams", {}).get("items", []),
                eps.get("builds", {}).get("items", []), meta)
    if "raw" in payload:  # 旧版：{source, fetched_at, raw:{items}}
        return payload["raw"].get("items", []), [], {
            "source": payload.get("source", ENDPOINTS["teams"]),
            "fetched_at": payload.get("fetched_at", "")}
    return payload.get("items", []), [], {"source": "bare", "fetched_at": ""}


def iter_builds(team_items: list[dict], build_items: list[dict]):
    """遍历所有 build 记录（队伍展开 + 单配置）。"""
    for it in team_items:
        for b in (it.get("snapshot") or {}).get("builds") or []:
            yield b
    for it in build_items:
        snap = it.get("snapshot") or {}
        if snap:
            yield snap


# ══════════════════════════════════════════════════
# 解析器
# ══════════════════════════════════════════════════
def _clean_name(raw: str) -> str:
    """剥离改名常见修饰词，返回候选名列表（原样优先）。"""
    cands = [raw]
    s = raw.strip()
    for noise in NAME_NOISE:
        if noise in s and noise != s:
            s = s.replace(noise, "").strip(" -_·")
    if s and s != raw:
        cands.append(s)
    return cands


class Resolver:
    """站点 build → 池内精灵名 / 合法技能集。"""

    def __init__(self, pool: dict[str, list[str]], team_items: list[dict], build_items: list[dict]):
        from backend.sim.factory import SimFactory
        from backend.common.skill_trait_ids import SKILL_ID_TO_NAME
        from backend.common.constants import BLOODLINES
        from backend.common.nature import NATURE_TABLE

        self.pool = pool
        self.pool_skill_sets = {n: set(s) for n, s in pool.items()}
        self.factory = SimFactory()
        self.db = self.factory.sprite_db
        self.id_to_name = SKILL_ID_TO_NAME
        self.element_bloodlines = BLOODLINES
        self.natures = NATURE_TABLE

        # 技能 → 持有它的池内精灵（改名 build 的技能匹配用）
        self.by_skill: dict[str, set[str]] = defaultdict(set)
        for name, skills in pool.items():
            for sk in skills:
                self.by_skill[sk].add(name)
        # creatureId → 池内名（站点主键；用能精确解析名字的 build 投票）
        self.cid_map = self._build_cid_map(team_items, build_items)
        self.stats = Counter()

    def _build_cid_map(self, team_items, build_items) -> dict[str, str]:
        votes: dict[str, Counter] = defaultdict(Counter)
        for b in iter_builds(team_items, build_items):
            cid = str(b.get("creatureId", "") or "")
            if not cid:
                continue
            hit = self._exact(b.get("name", ""))
            if hit:
                votes[cid][hit] += 1
        return {cid: c.most_common(1)[0][0] for cid, c in votes.items()}

    def _exact(self, raw: str) -> str | None:
        """池内名精确命中（含去修饰词、db 外观回退）。"""
        for cand in _clean_name(raw or ""):
            if cand in self.pool:
                return cand
            species = self.db.get(cand)
            if species is not None and species.display_name() in self.pool:
                return species.display_name()
        return None

    def resolve_species(self, raw: str, cid: str, skill_names: list[str]) -> tuple[str | None, str]:
        """返回 (池内显示名, 解析方式)。"""
        hit = self._exact(raw)
        if hit:
            self.stats["名字"] += 1
            return hit, "name"
        cid = str(cid or "")
        if cid in self.cid_map:
            self.stats["creatureId"] += 1
            return self.cid_map[cid], "cid"
        # 技能集合匹配：命中数最高且最具体（池内技能集更小）者胜
        scores: Counter = Counter()
        for sk in skill_names:
            for n in self.by_skill.get(sk, ()):
                scores[n] += 1
        if scores:
            best = max(scores.values())
            if best >= 3:
                winners = [n for n, c in scores.items() if c == best]
                # 名词包含优先（改名常带原名），再看技能集具体度，最后字典序
                flat = (raw or "").replace(" ", "")
                winners.sort(key=lambda n: (n.split("（")[0] not in flat,
                                            len(self.pool_skill_sets.get(n, ())), n))
                self.stats["技能匹配"] += 1
                return winners[0], "skills"
        self.stats["失败"] += 1
        return None, "fail"

    def bloodline_of(self, build: dict) -> str:
        raw = ((build.get("bloodline") or {}).get("name") or "").strip()
        stripped = raw[:-2] if raw.endswith("血脉") else raw
        if stripped in self.element_bloodlines:
            return stripped
        attr = ((build.get("bloodline") or {}).get("attribute") or "").strip()
        return attr if attr in self.element_bloodlines else ""

    def bloodline_skill_name(self, species_name: str, bloodline: str) -> str:
        species = self.db.get(species_name)
        if species is None or not bloodline:
            return ""
        sid = (species.bloodline_skills or {}).get(bloodline)
        return self.id_to_name.get(int(sid), "") if sid is not None else ""

    def allowed_skills(self, species_name: str, bloodline: str) -> set[str]:
        """池内技能 ∪ 所选血脉的血脉技能（站点阵容可携带血脉技能）。"""
        allowed = set(self.pool_skill_sets.get(species_name, set()))
        bl_name = self.bloodline_skill_name(species_name, bloodline)
        if bl_name:
            allowed.add(bl_name)
        return allowed

    def mark_iv_nature(self, marks: dict) -> tuple[list[str], str, str]:
        """statMarks → (iv 拉满项, 性格名, nature_plus 加项候选)。"""
        plus = [SITE_STAT[s] for s in (marks.get("plusStats") or []) if s in SITE_STAT]
        plus = list(dict.fromkeys(plus))[:3]
        up = SITE_STAT.get(marks.get("extremeStat") or "", "")
        down = SITE_STAT.get(marks.get("minusStat") or "", "")
        nature = ""
        if up and down and up != down:
            for nm, (u, d) in self.natures.items():
                if u == up and d == down:
                    nature = nm
                    break
        if not nature and up:
            # 网站未填减项：只固定加项，由 spec 生成时从加项候选里挑性格
            return plus, "", up
        return plus, nature, ""


def parse_item_hint(team_raw: dict, sprites: list[dict]) -> tuple[str, str]:
    """从 gameTeamCode 的「魔法：X」行取道具；无该行时按血脉推断。

    首领血脉队 → 进化之力（首领化的前提）；其余 → 愿力（非首领血脉用不了进化之力）。
    """
    code = team_raw.get("gameTeamCode") or ""
    m = re.search(r"魔法[：:]\s*([^\n#]*)", code)
    if m:
        val = m.group(1).strip()
        for site_name, our_name in SITE_ITEM.items():
            if site_name in val:
                return our_name, "code"
    if any(s.get("bloodline") == "首领" for s in sprites):
        return "进化之力", "inferred"
    return "愿力", "default"


# ══════════════════════════════════════════════════
# 归一化 + 校验
# ══════════════════════════════════════════════════
def normalize_team(item: dict, res: Resolver, roles: "RoleTable | None" = None,
                   fill: bool = True) -> tuple[dict | None, list[str]]:
    """单条站点 item → meta 队伍 dict；返回 (队伍, 修补/问题记录)。"""
    snap = item.get("snapshot") or {}
    team_raw = snap.get("team") or {}
    builds = snap.get("builds") or []
    tname = team_raw.get("name") or item.get("id", "?")
    notes: list[str] = []

    if len(builds) != 6:
        return None, [f"[{tname}] 精灵数 {len(builds)} != 6"]

    sprites: list[dict] = []
    seen: set[str] = set()
    for idx, b in enumerate(builds):
        raw_name = (b.get("name") or "").strip()
        skills = [s for s in (b.get("selectedSkillNames") or []) if s]
        skills = list(dict.fromkeys(skills))
        name, how = res.resolve_species(raw_name, b.get("creatureId", ""), skills)
        if name is None:
            return None, [f"[{tname}] 精灵无法解析: {raw_name}"]
        if name in seen:
            return None, [f"[{tname}] 重复精灵: {name}"]
        seen.add(name)

        bloodline = res.bloodline_of(b)
        allowed = res.allowed_skills(name, bloodline)
        illegal = [s for s in skills if s not in allowed]
        legal = [s for s in skills if s in allowed]
        if illegal:
            notes.append(f"[{tname}] {name} 剔除不在池/血脉技能内: {illegal}")
        if fill and len(legal) < 3:
            added = _fill_skills(name, legal, allowed, roles, res)
            if len(legal) + len(added) < 3:
                return None, notes + [f"[{tname}] {name} 合法技能不足 3 个"]
            legal += added
            notes.append(f"[{tname}] {name} 补齐技能: {added}")
        if not legal:
            return None, notes + [f"[{tname}] {name} 无合法技能"]

        plus, nature, nature_plus = res.mark_iv_nature(b.get("statMarks") or {})
        entry = {
            "name": name,
            "skills": legal[:4],
            "iv_fixed": plus,
            "bloodline": bloodline,
            "_order": idx,
        }
        if nature:
            entry["nature_fixed"] = nature
        elif nature_plus:
            entry["nature_plus"] = [nature_plus]
        if how != "name":
            entry["source_name"] = raw_name
        sprites.append(entry)

    item_name, item_src = parse_item_hint(team_raw, sprites)
    tags = [t for t in (team_raw.get("tags") or []) if t]
    team = {
        "name": tname,
        "tags": tags,
        "archetype": _archetype_of(tags, sprites),
        "source_id": item.get("id", ""),
        "like_count": int(item.get("likeCount") or 0),
        "sprites": sprites,
    }
    if item_name:
        team["item"] = item_name
        team["item_source"] = item_src
    return team, notes


def _fill_skills(name: str, current: list[str], allowed: set[str],
                 roles: "RoleTable | None", res: Resolver) -> list[str]:
    """从池内补技能：优先与已有技能同类型，再按名字确定性排序。"""
    pool = [s for s in res.pool.get(name, []) if s not in current]
    if not pool:
        return []
    want = _dominant_type(current, res)
    ranked = sorted(pool, key=lambda s: (_skill_type(res, s) != want, s))
    return ranked[: max(0, 3 - len(current))]


def _dominant_type(skills: list[str], res: Resolver) -> str:
    counts = Counter(_skill_type(res, s) for s in skills if _skill_type(res, s))
    if not counts:
        return "物攻"
    offline = "魔攻" if "魔攻" in counts else "物攻"
    return counts.most_common(1)[0][0] if counts.most_common(1)[0][1] > 1 else offline


def _skill_type(res: Resolver, skill_name: str) -> str:
    """技能类型（物攻/魔攻/状态）——读技能 JSON，带进程级缓存。"""
    cache = getattr(_skill_type, "_cache", None)
    if cache is None:
        cache = _skill_type._cache = {}  # type: ignore[attr-defined]
    if skill_name in cache:
        return cache[skill_name]
    stype = ""
    path = _PROJECT_ROOT / "data" / "skills" / f"{skill_name}.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            stype = (data.get("skill_type") or data.get("type") or "").strip()
        except (json.JSONDecodeError, OSError):
            stype = ""
    cache[skill_name] = stype
    return stype


def _archetype_of(tags: list[str], sprites: list[dict]) -> str:
    for t in ("比赛", "排位", "体系队", "平衡队", "科研", "娱乐", "情怀"):
        if t in tags:
            return t
    if any(s.get("bloodline") == "首领" for s in sprites):
        return "首领进化流"
    return "未标注"


# ══════════════════════════════════════════════════
# 角色/首发/必保 标注
# ══════════════════════════════════════════════════
class RoleTable:
    """与 train._sprite_roles 同口径的角色分桶（攻击手前 40% / 坦克前 30%）。"""

    def __init__(self, pool: dict[str, list[str]], res: Resolver):
        from backend.common.formulas import StatsCalc

        calc = StatsCalc()
        self.info: dict[str, dict] = {}
        for name, skills in pool.items():
            species = res.db.get(name)
            if species is None:
                continue
            fs = calc.compute(species).final_stats
            n_atk = sum(1 for s in skills if _skill_type(res, s) in ("物攻", "魔攻"))
            self.info[name] = {
                "offense": max(fs["atk"], fs["sp_atk"]),
                "bulk": fs["hp"] + fs["def"] + fs["sp_def"],
                "speed": fs["speed"],
                "util_frac": 1.0 - n_atk / max(1, len(skills)),
            }
        n = len(self.info)
        by_off = sorted(self.info, key=lambda k: -self.info[k]["offense"])
        by_bulk = sorted(self.info, key=lambda k: -self.info[k]["bulk"])
        self.attackers = set(by_off[: int(n * 0.4)])
        self.tanks = set(by_bulk[: int(n * 0.3)])

    def role_of(self, name: str, skills: list[str], res: Resolver) -> str:
        if name not in self.info:
            return "attack"
        n_atk = sum(1 for s in skills if _skill_type(res, s) in ("物攻", "魔攻"))
        if n_atk <= 1:
            return "support"
        if name in self.tanks and name not in self.attackers:
            return "tank"
        return "attack"

    def speed_of(self, name: str) -> int:
        return int(self.info.get(name, {}).get("speed", 0))


def annotate(team: dict, roles: RoleTable, res: Resolver) -> None:
    """就地写入 role / lead / preserve / energy_hold。"""
    sprites = team["sprites"]
    for sp in sprites:
        sp["role"] = roles.role_of(sp["name"], sp["skills"], res)
        sp["lead"] = False
        sp["preserve"] = False
        sp["energy_hold"] = 0
    # 首发：队伍顺序第一位（站点 build 顺序 = 游戏内编队顺序）；若首位是工具人，
    # 追加最快的攻击手作为第二首发候选，供 agent 按对位评分选择。
    sprites[0]["lead"] = True
    if sprites[0]["role"] != "attack":
        atk = [s for s in sprites[1:] if s["role"] == "attack"]
        if atk:
            max(atk, key=lambda s: roles.speed_of(s["name"]))["lead"] = True
    # 必保：末位攻击手（终结手）
    if sprites[-1]["role"] == "attack":
        sprites[-1]["preserve"] = True


# ══════════════════════════════════════════════════
# 选队（原型分层 + 精灵多样性）
# ══════════════════════════════════════════════════
def select_teams(teams: list[dict], notes_by_team: dict[str, int], *, target: int,
                 sprite_cap: int, boss_teams: int, flex_per_team: int,
                 all_teams: list[dict]) -> tuple[list[dict], list[str]]:
    """按（修补数, 标签优先级, 点赞数）贪心选队，并限制单精灵重复出场。"""
    notes: list[str] = []

    def rank(t: dict) -> tuple:
        tag_rank = min((TAG_RANK.get(g, 9) for g in t["tags"]), default=9)
        return (notes_by_team.get(t["name"], 0), tag_rank, -t["like_count"], t["name"])

    def has_boss(t: dict) -> bool:
        return any(s.get("bloodline") == "首领" for s in t["sprites"])

    ordered = sorted(teams, key=rank)
    chosen: list[dict] = []
    usage: Counter[str] = Counter()
    seen_comps: set[frozenset] = set()

    def try_add(t: dict, cap: int) -> bool:
        comp = frozenset(s["name"] for s in t["sprites"])
        if comp in seen_comps or any(usage[s["name"]] >= cap for s in t["sprites"]):
            return False
        chosen.append(t)
        seen_comps.add(comp)
        for s in t["sprites"]:
            usage[s["name"]] += 1
        return True

    # 第一轮：先补足「首领血脉进化流」样本（自博弈要学首领化时机）
    for t in [x for x in ordered if has_boss(x)]:
        if sum(1 for c in chosen if has_boss(c)) >= boss_teams:
            break
        try_add(t, sprite_cap)
    notes.append(f"首领血脉队 {sum(1 for c in chosen if has_boss(c))}/{boss_teams}")

    for t in ordered:
        if len(chosen) >= target:
            break
        try_add(t, sprite_cap)
    if len(chosen) < target:
        notes.append(f"精灵上限 {sprite_cap} 下仅 {len(chosen)} 支，放宽到 {sprite_cap + 1}")
        for t in ordered:
            if len(chosen) >= target:
                break
            try_add(t, sprite_cap + 1)
    if len(chosen) < target:
        notes.append(f"放宽后仍只有 {len(chosen)} 支（候选 {len(teams)}）")

    if flex_per_team:
        notes.append(f"flex 替补位 {_attach_flex(chosen, all_teams, flex_per_team)} 个")
    return chosen, notes


def _attach_flex(chosen: list[dict], all_teams: list[dict], per_team: int) -> int:
    """给每队最「模板化」的槽位挂同角色替补，降低阵容固定性。"""
    by_role: dict[str, list[dict]] = defaultdict(list)
    for t in all_teams:
        for sp in t["sprites"]:
            by_role[sp.get("role", "attack")].append(sp)

    alt_usage: Counter[str] = Counter()  # 避免同一替补被反复挂到多队
    added = 0
    for team in chosen:
        names = {s["name"] for s in team["sprites"]}
        cand_slots = sorted(team["sprites"], key=lambda s: (s["role"] != "attack", s["name"]))
        for slot in cand_slots[:per_team]:
            pool = [a for a in by_role.get(slot["role"], []) if a["name"] not in names]
            if not pool:
                continue
            # 确定性挑：先看替补被用过几次（越少越好），再看技能数（越全越好）
            alt = sorted(pool, key=lambda a: (alt_usage[a["name"]], -len(a["skills"]), a["name"]))[0]
            alt = {k: v for k, v in alt.items()
                   if k in ("name", "skills", "iv_fixed", "nature_fixed", "nature_plus",
                            "bloodline", "role")}
            slot.setdefault("alts", []).append(alt)
            names.add(alt["name"])
            alt_usage[alt["name"]] += 1
            added += 1
    return added


# ══════════════════════════════════════════════════
# 子命令
# ══════════════════════════════════════════════════
def _load_ready(args):
    """公共前置：加载池子 + 解析器 + 缓存阵营。"""
    from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL

    team_items, build_items, meta = load_cache(args.cache)
    res = Resolver(SPRITE_RANDOM_POOL, team_items, build_items)
    return team_items, build_items, meta, res, SPRITE_RANDOM_POOL


def _normalize_all(team_items, res) -> tuple[list[dict], dict[str, int], list[str]]:
    ready, notes_by_team, all_notes = [], {}, []
    for item in team_items:
        team, notes = normalize_team(item, res)
        if team is None:
            all_notes.extend(notes)
            continue
        ready.append(team)
        notes_by_team[team["name"]] = len(notes)
        all_notes.extend(notes)
    return ready, notes_by_team, all_notes


def cmd_verify(args) -> int:
    team_items, build_items, meta, res, pool = _load_ready(args)
    ready, notes_by_team, all_notes = _normalize_all(team_items, res)
    failed = {n.get("snapshot", {}).get("team", {}).get("name", "?")
              for n in team_items} - {t["name"] for t in ready}

    lines = [
        f"源: {meta.get('source')}  抓取时间: {meta.get('fetched_at', '-')}",
        f"原始队伍 {len(team_items)} 支 → 可用 {len(ready)} 支，不可用 {len(failed)} 支",
        f"池子 {len(pool)} 只；精灵解析用到的途径: {dict(res.stats)}",
        f"creatureId 投票表: {len(res.cid_map)} 个映射",
        "",
        "== 不可用队伍及原因 ==",
    ]
    lines += [n for n in all_notes if any(f"[{f}]" in n for f in failed)]
    lines += ["", "== 修补记录（技能剔除/补齐）=="]
    lines += [n for n in all_notes if "剔除" in n or "补齐" in n]
    lines += ["", "== 可用队伍 =="]
    for t in sorted(ready, key=lambda x: (notes_by_team.get(x["name"], 0), -x["like_count"])):
        boss = "首领" if any(s.get("bloodline") == "首领" for s in t["sprites"]) else "    "
        it = f" 魔法={t.get('item', '-')}({t.get('item_source', '-')})" if t.get("item") else ""
        lines.append(f"{boss} 修补{notes_by_team.get(t['name'], 0)} [{t['archetype']:<5}] "
                     f"♥{t['like_count']} {t['name']}{it}: "
                     + " ".join(s["name"] for s in t["sprites"]))

    text = "\n".join(lines)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
        print(f"报告 → {args.report}")
    print(f"可用 {len(ready)}/{len(team_items)} 支；解析途径 {dict(res.stats)}")
    return 0


def cmd_build(args) -> int:
    team_items, build_items, meta, res, pool = _load_ready(args)
    ready, notes_by_team, all_notes = _normalize_all(team_items, res)

    roles = RoleTable(pool, res)
    for t in ready:
        annotate(t, roles, res)

    chosen, sel_notes = select_teams(
        ready, notes_by_team, target=args.target, sprite_cap=args.sprite_cap,
        boss_teams=args.boss_teams, flex_per_team=args.flex_per_team, all_teams=ready)

    for t in chosen:  # 清理内部字段
        t["sprites"] = [{k: v for k, v in sp.items() if not k.startswith("_")}
                        for sp in t["sprites"]]

    payload = {
        "source": meta.get("source", ENDPOINTS["teams"]),
        "fetched_at": meta.get("fetched_at", ""),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "raw_count": len(team_items),
        "usable_count": len(ready),
        "selected_count": len(chosen),
        "selection_notes": sel_notes,
        "teams": chosen,
    }
    out = Path(args.out)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── 报告：多样性 + 清单 ──
    slots = [s["name"] for t in chosen for s in t["sprites"]]
    lines = [
        f"候选 {len(ready)} 支 → 选中 {len(chosen)} 支",
        *[f"  - {n}" for n in sel_notes],
        "",
        f"精灵去重数: {len(set(slots))}  槽位总数: {len(slots)}  每只平均出场: {len(slots) / max(1, len(set(slots))):.2f}",
        "元素分布: " + ", ".join(f"{k}:{v}" for k, v in Counter(
            _element_of_name(n) for n in slots).most_common()),
        "原型分布: " + ", ".join(f"{k}:{v}" for k, v in Counter(t["archetype"] for t in chosen).most_common()),
        "道具分布: " + ", ".join(f"{k}:{v}" for k, v in Counter(t.get("item", "-") for t in chosen).most_common()),
        "",
        "出场最多的精灵:",
    ]
    lines += [f"  {n}× {c}" for n, c in Counter(slots).most_common(15)]
    lines += ["", "== 选中队伍 =="]
    for t in chosen:
        boss = "首领" if any(s.get("bloodline") == "首领" for s in t["sprites"]) else "    "
        it = f" 魔法={t.get('item', '-')}" if t.get("item") else ""
        lines.append(f"{boss} 修补{notes_by_team.get(t['name'], 0)} [{t['archetype']:<5}] ♥{t['like_count']} {t['name']}{it}")
        for sp in t["sprites"]:
            bl = f" 血脉={sp['bloodline']}" if sp.get("bloodline") else ""
            mark = "首发" if sp.get("lead") else ("必保" if sp.get("preserve") else "  ")
            alts = f" (+替补 {sp['alts'][0]['name']})" if sp.get("alts") else ""
            lines.append(f"    {mark} {sp['role']:<7} {sp['name']:<18} "
                         f"{'、'.join(sp['skills'])}{bl}{alts}")
    text = "\n".join(lines)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    print(f"选中 {len(chosen)} 支 → {out}" + (f"；报告 → {args.report}" if args.report else ""))
    return 0


def _element_of_name(name: str) -> str:
    cache = getattr(_element_of_name, "_db", None)
    if cache is None:
        from backend.sim.factory import SimFactory
        cache = _element_of_name._db = SimFactory().sprite_db  # type: ignore[attr-defined]
    sp = cache.get(name)
    return "+".join(sp.elements) if sp is not None and sp.elements else "?"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="爬取共享阵容 → 池子校验 → meta_teams.json")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_fetch = sub.add_parser("fetch", help="抓取原始阵容 JSON（指数退避）")
    p_fetch.add_argument("--cache", default=str(DEFAULT_CACHE))
    p_fetch.add_argument("--refresh", action="store_true")
    p_fetch.add_argument("--base-delay", type=float, default=1.0)
    p_fetch.set_defaults(func=cmd_fetch)

    p_verify = sub.add_parser("verify", help="池子校验并输出报告")
    p_verify.add_argument("--cache", default=str(DEFAULT_CACHE))
    p_verify.add_argument("--report", default="")
    p_verify.set_defaults(func=cmd_verify)

    p_build = sub.add_parser("build", help="选队并写出 meta_teams.json")
    p_build.add_argument("--cache", default=str(DEFAULT_CACHE))
    p_build.add_argument("--out", default=str(DEFAULT_OUT))
    p_build.add_argument("--report", default="")
    p_build.add_argument("--target", type=int, default=40)
    p_build.add_argument("--sprite-cap", type=int, default=4, help="同一精灵最多出现在几支选中队")
    p_build.add_argument("--boss-teams", type=int, default=8, help="首领血脉进化流保底队数")
    p_build.add_argument("--flex-per-team", type=int, default=1, help="每队挂几个同角色替补")
    p_build.set_defaults(func=cmd_build)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
