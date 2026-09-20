"""backend/engine/ai/tests/test_bc_pipeline.py — BC 管线端到端烟雾测试。

不硬编码精灵名：运行时从精灵池按角色桶取精灵组队，池内容变更（用户
正在扩充）不影响测试有效性。覆盖：
  - meta 队伍校验 / spec 扰动构建
  - TeamStrategy 首发限制与动作→索引映射
  - 录制对局样本的掩码一致性
  - 整队留出切分
  - gen_bc_data → bc_pretrain → 权重可加载 的完整小规模闭环
"""
from __future__ import annotations

import importlib.util
import random
import sys
from pathlib import Path

import numpy as np
import pytest
import torch

from backend.common.constants import ITEM_VARIANT_SLOTS
from backend.engine.ai.bc_record import action_to_index, run_recorded_battle
from backend.engine.ai.bc_pretrain import holdout_split, load_dataset
from backend.engine.ai.data.meta_teams import (
    item_from_team,
    load_meta_teams,
    meta_teams_path,
    spec_from_team,
    strategy_from_team,
    validate_meta_teams,
)
from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL
from backend.engine.ai.train import _random_teams, _role_skills, _sprite_roles
from backend.sim.agent import _switch_action
from backend.sim.agent_v2 import RuleAgentV2, SpriteStrategy, TeamStrategy
from backend.sim.factory import SimFactory

_PROJECT_ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def factory():
    return SimFactory()


@pytest.fixture(scope="module")
def pool_buckets():
    """运行时角色分桶：输出前 6 + 坦度前 6（互不重复）。"""
    roles = _sprite_roles(SimFactory(), dict(SPRITE_RANDOM_POOL))
    info = roles["info"]
    atk = sorted(roles["attackers"], key=lambda n: -info[n]["offense"])[:6]
    tank = [n for n in sorted(roles["tanks"], key=lambda n: -info[n]["bulk"])
            if n not in atk][:6]
    if len(atk) < 6 or len(tank) < 6:
        pytest.skip("精灵池不足 12 只，跳过 BC 管线测试")
    return {"attack": atk, "tank": tank}


def _meta_team(role: str, names: list[str]) -> dict:
    sprites = []
    for name in names:
        skills = _role_skills(role, SPRITE_RANDOM_POOL[name], 4)
        sprites.append({
            "name": name,
            "role": role,
            "lead": True,
            "skills": skills,
            "iv_fixed": ["atk", "speed"] if role == "attack" else ["hp", "def"],
            "nature_plus": ["atk", "speed"] if role == "attack" else ["hp", "def"],
        })
    return {"name": f"smoke_{role}", "archetype": role, "sprites": sprites}


def test_meta_team_validate_and_spec(pool_buckets):
    team = _meta_team("attack", pool_buckets["attack"])
    assert validate_meta_teams([team], SPRITE_RANDOM_POOL) == []
    specs, names = spec_from_team(team, random.Random(0))
    assert len(specs) == 6 and len(names) == 6
    for spec in specs:
        assert spec["name"] in pool_buckets["attack"]
        assert sum(1 for v in spec["iv"].values() if v == 10) == 3
        assert spec["nature"]


def test_meta_team_exact_iv_nature_and_bloodline(pool_buckets):
    """爬取阵容语义：iv_fixed 3 项精确拉满 + nature_fixed 精确 + 血脉透传。"""
    name = pool_buckets["attack"][0]
    team = {
        "name": "scraped_smoke",
        "item": "进化之力",
        "sprites": [{
            "name": name,
            "role": "attack",
            "skills": SPRITE_RANDOM_POOL[name][:3],
            "iv_fixed": ["hp", "def", "speed"],
            "nature_fixed": "开朗",
            "bloodline": "火",
        }] + [{
            "name": n, "role": "attack", "skills": SPRITE_RANDOM_POOL[n][:3],
            "iv_fixed": ["atk", "speed"], "nature_plus": ["atk"],
        } for n in pool_buckets["attack"][1:6]],
    }
    assert validate_meta_teams([team], SPRITE_RANDOM_POOL) == []
    specs, _ = spec_from_team(team, random.Random(0))
    first = specs[0]
    assert sorted(k for k, v in first["iv"].items() if v == 10) == ["def", "hp", "speed"]
    assert first["nature"] == "开朗"
    assert first["bloodline"] == "火"
    # 2 项 iv_fixed → 第三项随机（手写队伍的多样性）
    assert sum(1 for v in specs[1]["iv"].values() if v == 10) == 3
    assert item_from_team(team).name == "进化之力"
    assert item_from_team({"name": "x"}) is None


def test_leader_form_entries_are_fielded_as_base_form():
    """首领形态不能直接上场：站点阵容若写变身后的形态，须改写为基础形态 + 首领血脉。"""
    from backend.engine.ai.data.meta_teams import resolve_entry_name, spec_from_entry

    for raw, base in (("深渊罗隐", "罗隐"), ("恶魔狼王", "恶魔狼"), ("祭礼巨像", "仪式巨像")):
        spec_name, engine_name, rewritten = resolve_entry_name(raw)
        assert spec_name == base, f"{raw} 应改写为 {base}，实际 {spec_name}"
        assert engine_name == base, "策略表键要用引擎可见名（基础名）"
        assert rewritten is True

    # 未指定的血脉由改写补成「首领」（否则局内无法变身）
    team = {
        "name": "boss_smoke",
        "item": "进化之力",
        "sprites": [{
            "name": "深渊罗隐", "role": "attack",
            "skills": SPRITE_RANDOM_POOL["罗隐"][:4],
            "iv_fixed": ["atk", "speed"],
        }] + [{
            "name": n, "role": "attack", "skills": SPRITE_RANDOM_POOL[n][:3],
            "iv_fixed": ["atk", "speed"],
        } for n in list(SPRITE_RANDOM_POOL)[:5]],
    }
    problems = validate_meta_teams([team], SPRITE_RANDOM_POOL)
    assert problems == [], "\n".join(problems)
    specs, names = spec_from_team(team, random.Random(0))
    first = specs[0]
    assert first["name"] == "罗隐"
    assert first.get("bloodline") == "首领"

    # 策略表用引擎可见名做键（外观变体曾是静默失配的坑）
    from backend.engine.ai.data.meta_teams import strategy_from_team
    strat = strategy_from_team(team, random.Random(0))
    assert "罗隐" in strat.sprites


def test_shipped_meta_teams_validate():
    """随仓库交付的 meta_teams.json 必须能通过池子校验（数据完整性门禁）。"""
    path = meta_teams_path()
    if not path.exists():
        pytest.skip(f"未生成 {path}（native/tools/scrape_meta_teams.py build）")
    teams = load_meta_teams()
    assert teams, "meta_teams.json 存在但为空"
    problems = validate_meta_teams(teams, SPRITE_RANDOM_POOL)
    assert problems == [], "\n".join(problems[:20])
    # 首领进化流必须被覆盖（否则自博弈学不到首领化时机）
    boss = [t for t in teams
            if any(sp.get("bloodline") == "首领" for sp in t["sprites"])]
    assert boss, "没有任何首领血脉队伍"
    assert all(item_from_team(t) is not None for t in teams), "有队伍缺道具（魔法）"


def test_strategy_lead_restriction(factory, pool_buckets):
    names = pool_buckets["attack"][:3] + pool_buckets["tank"][:3]
    specs = []
    for i, name in enumerate(names):
        skills = SPRITE_RANDOM_POOL[name][:4] or ["撞击"]
        specs.append({"name": name, "skills": skills,
                      "nature": "开朗", "iv": {}})
    p1 = factory.build_player("A", specs)
    p2 = factory.build_player("B", specs)
    battle = factory.build_battle(p1, p2)

    # 全队 lead=True → 仍走评分；只留 tank 首位为候选 → 必须选它
    strat_all = TeamStrategy(sprites={
        n: SpriteStrategy(lead=True) for n in names})
    agent = RuleAgentV2("A", p1, strategy=strat_all)
    assert agent.choose_lead(battle) in range(6)

    lead_only = names[3]  # tank 桶第一位
    strat_one = TeamStrategy(sprites={
        lead_only: SpriteStrategy(lead=True)})
    agent2 = RuleAgentV2("A", p1, strategy=strat_one)
    assert agent2.choose_lead(battle) == 3


def test_action_to_index_switch_mapping(factory):
    specs = []
    for name in list(SPRITE_RANDOM_POOL)[:6]:
        specs.append({"name": name, "skills": SPRITE_RANDOM_POOL[name][:1] or ["撞击"],
                      "nature": "开朗", "iv": {}})
    p = factory.build_player("A", specs)
    p.active_index = 2
    assert action_to_index(p, _switch_action(0)) == 10
    assert action_to_index(p, _switch_action(1)) == 11
    assert action_to_index(p, _switch_action(3)) == 12
    assert action_to_index(p, _switch_action(2)) is None


def test_recorded_game_samples(factory, pool_buckets):
    rng = random.Random(3)
    team_a, _ = spec_from_team(_meta_team("attack", pool_buckets["attack"]), rng)
    team_b, _ = spec_from_team(_meta_team("tank", pool_buckets["tank"]), rng)
    strat_a = strategy_from_team(_meta_team("attack", pool_buckets["attack"]))
    strat_b = strategy_from_team(_meta_team("tank", pool_buckets["tank"]))
    samples, outcome_a, end_reason, turns = run_recorded_battle(
        factory, team_a, team_b,
        lambda tag, player: RuleAgentV2(tag, player, strategy=strat_a),
        lambda tag, player: RuleAgentV2(tag, player, strategy=strat_b),
        max_turns=30, game_id=0,
    )
    assert samples and outcome_a in (-1.0, 0.0, 1.0) and turns <= 30
    sides = {s[4] for s in samples}
    assert sides == {"A", "B"}
    from backend.engine.ai.core.mcts import NUM_ACTIONS
    for state, action_idx, mask, game_id, side in samples:
        assert 0 <= action_idx < NUM_ACTIONS and mask[action_idx] > 0
        assert mask.shape == (NUM_ACTIONS,) and mask.sum() >= 1
        assert state["sprite_stats"].shape == (12, 7)
        assert state["form_elements"].shape == (ITEM_VARIANT_SLOTS, 2)
        assert state["form_avail"].shape == (ITEM_VARIANT_SLOTS,)


def test_holdout_split():
    ds = {
        "is_meta": np.array([1] * 6 + [0] * 4, dtype=np.int8),
        "team_id": np.array([0, 0, 1, 1, 2, 2, -1, -1, -1, -1], dtype=np.int64),
        "game_id": np.array([0, 0, 1, 1, 2, 2, 3, 4, 5, 6], dtype=np.int64),
        "outcome": np.zeros(10, dtype=np.float32),
    }
    train_idx, val_idx, holdout = holdout_split(ds, holdout_teams=1,
                                                random_val_frac=0.5, seed=0)
    assert holdout == [2]
    assert set(val_idx) >= {4, 5}          # 整队留出：team 2 的全部样本
    assert set(train_idx) & set(val_idx) == set()
    assert len(train_idx) + len(val_idx) == 10


def _load_tool(name: str):
    """按路径加载 native/tools 下的脚本（该目录不是 python 包）。"""
    path = _PROJECT_ROOT / "native" / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_bc_end_to_end(factory, pool_buckets, tmp_path, monkeypatch):
    """gen_bc_data → bc_pretrain → 权重加载 的完整小规模闭环。"""
    teams = [_meta_team("attack", pool_buckets["attack"]),
             _meta_team("tank", pool_buckets["tank"])]
    teams_json = tmp_path / "teams.json"
    teams_json.write_text(
        '{"teams": ' + _dumps_safe(teams) + "}", encoding="utf-8")
    out_npz = tmp_path / "bc.npz"
    out_ckpt = tmp_path / "bc_init.pt"

    gen_main = _load_tool("gen_bc_data").main
    monkeypatch.setattr(sys, "argv", [
        "gen_bc_data", "--games", "6", "--meta-frac", "0.75",
        "--meta-file", str(teams_json), "--out", str(out_npz),
        "--max-turns", "25", "--seed", "11",
    ])
    gen_main()

    from backend.engine.ai.bc_pretrain import main as bc_main
    monkeypatch.setattr(sys, "argv", [
        "bc_pretrain", "--data", str(out_npz), "--out", str(out_ckpt),
        "--epochs", "2", "--batch-size", "32", "--holdout-teams", "1",
        "--random-val-frac", "0.5", "--device", "cpu", "--seed", "1",
    ])
    bc_main()

    ds = load_dataset(str(out_npz))
    assert len(ds["outcome"]) > 10
    from backend.engine.ai.core.model import ModularBattleNet
    from backend.engine.ai.core.vocab import VOCAB_SIZE
    # 必须走标准加载器（bc_pretrain 存的就是它认的格式）——裸 state_dict 会让
    # 评估/部署路径 KeyError（2026-09-20 踩过）
    model = ModularBattleNet.load(str(out_ckpt), device="cpu")
    assert model.num_params > 0

    # meta 混合分布接入 _random_teams
    monkeypatch.setenv("ROCO_META_TEAMS", str(teams_json))
    monkeypatch.setattr("backend.engine.ai.train._META_FRAC", 1.0)
    team_a, team_b, item_a, item_b = _random_teams(factory, dict(SPRITE_RANDOM_POOL))
    spec_names = {sp["name"] for sp in team_a} | {sp["name"] for sp in team_b}
    assert spec_names <= {sp["name"] for t in teams for sp in t["sprites"]}
    # 队伍自带魔法 → 道具由队伍决定（smoke 队未声明 item，回退随机）
    assert item_a.name in ("进化之力", "愿力") and item_b.name in ("进化之力", "愿力")


def _dumps_safe(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


def test_bc_outcome_labels_are_side_perspective(factory, pool_buckets, tmp_path, monkeypatch):
    """胜负标签必须按视角取反：同一决定性对局里 A/B 样本标签异号。

    旧实现把 +outcome_a 发给了所有样本（漏 `-outcome_a`），value 头等于被喂了
    一半反号标签；而汇总统计（正负样本总数）完全看不出异常——实测 2500 局里
    2294 局全同号却「总量正常」。故必须按局检查符号集合。
    """
    import json

    teams = [_meta_team("attack", pool_buckets["attack"]),
             _meta_team("tank", pool_buckets["tank"])]
    teams_json = tmp_path / "teams.json"
    teams_json.write_text(
        '{"teams": ' + _dumps_safe(teams) + "}", encoding="utf-8")
    out_npz = tmp_path / "bc_sign.npz"

    gen_main = _load_tool("gen_bc_data").main
    monkeypatch.setattr(sys, "argv", [
        "gen_bc_data", "--games", "8", "--meta-frac", "0.5",
        "--meta-file", str(teams_json), "--out", str(out_npz),
        "--max-turns", "20", "--seed", "3",
    ])
    gen_main()

    data = np.load(out_npz)
    outcome, game_id = data["outcome"], data["game_id"]
    decisive = 0
    for gid in np.unique(game_id):
        nz = outcome[game_id == gid]
        nz = nz[nz != 0]
        if nz.size == 0:
            continue
        decisive += 1
        assert (nz > 0).any() and (nz < 0).any(), (
            f"局 {gid} 的标签只出现单侧符号 {sorted(set(nz.tolist()))}："
            "B 视角样本漏了取反")

    sidecar = json.loads(Path(out_npz).with_suffix(".json").read_text(encoding="utf-8"))
    assert sidecar["perspective_sign_ok_rate"] == 1.0, sidecar["perspective_sign_ok_rate"]
    if decisive == 0:
        pytest.skip("本轮没有决定性对局，符号自检无意义")
