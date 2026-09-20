//! backend/engine/ai/core/encoder.py 的 Rust 镜像（阶段4-5）。
//!
//! 输出与 py `encode_battle_state` 键一一对应的 10 个定长数组。
//! 阶段4-5a：8 个实体数组（sprite_stats/elements/states、skill_*、
//! global_*）；`ast_tokens`/`ast_values` 在 4-5b（AST tokenizer + 词表）
//! 接入前恒为 0——对拍工具在 5a 阶段跳过这两个键。
//!
//! 镜像要点（与 py 逐行对齐，勿"顺手优化"）：
//! - 元素类别 ID = ELEMENT_ORDER 索引 + 1（0=PAD）；天气 ID = 索引（无 +1）。
//! - 印记特征 global_stats[4..8] 恒为 0：py `_classify_marks_global` 读
//!   `getattr(g, 'marks', {})` 而 Globals 无 `marks` 属性（正/负印记特征
//!   在 py 里就是死特征，训练出的权重基于恒 0），必须同样置 0。
//! - stages/abnormals/charging 由 active_effects 现算，语义 =
//!   py `_rebuild_effects_cache`（StatBuff 按 stat_key 求和、Abnormal 按
//!   名求和、State 的 state_type/name == "charging"/"charged"）。
//! - encoder 里 initial_stats 缺省是 0（py .get(key, 0)），不是属性缓存
//!   里的缺省 100——两处默认值不同，别混用。
//! - `int(modifier)` 截断用 `as i64`（py int() 同为向零截断）。

use crate::battle_state::{BattleSkill, BattleState, EffectKind, Sprite};

pub const SPRITE_STATES_BASE: usize = 25;
pub const SKILL_SUMMARY_PER_SKILL: usize = 8;
pub const SPRITE_STATES_DIM: usize = SPRITE_STATES_BASE + 10 * SKILL_SUMMARY_PER_SKILL; // 105
pub const MAX_SEQ_LEN: usize = 384;

pub const ELEMENT_ORDER: [&str; 18] = [
    "光", "冰", "地", "幻", "幽", "恶", "普通", "机械", "武", "毒", "水", "火", "电", "翼", "草",
    "萌", "虫", "龙",
];
const ABNORMAL_ORDER: [&str; 7] = ["灼烧", "冻结", "中毒", "寄生", "萌化", "晕眩", "眩晕"];
const ABNORMAL_MAX: [f64; 7] = [50.0, 20.0, 10.0, 10.0, 3.0, 1.0, 1.0];
const WEATHER_ORDER: [&str; 4] = ["none", "rain", "sand", "snow"];
const DEVOTION_ORDER: [&str; 5] = ["奉献1", "奉献2", "奉献3", "奉献4", "奉献5"];

fn skill_type_onehot(skill_type: &str) -> usize {
    match skill_type {
        "物攻" => 0,
        "魔攻" => 1,
        "动态攻击" => 2,
        "防御" => 3,
        "状态" => 4,
        _ => 0,
    }
}

fn element_cat_id(value: &str) -> i32 {
    ELEMENT_ORDER
        .iter()
        .position(|&e| e == value)
        .map(|i| i as i32 + 1)
        .unwrap_or(0)
}

/// 系别克制表（py encoder._TYPE_CHART，与 resolver._TYPE_CHART 同源）。
/// py _TYPE_CHART 的逐项镜像：(攻, 守) → 倍率；缺省 1.0。
pub fn type_advantage(atk: &str, def: &str) -> f64 {
    let pairs: &[(&str, &[&str], &[&str])] = &[
        // (攻系, 2.0 守系列表, 0.5 守系列表)
        ("光", &["幽", "恶"], &["冰", "翼"]),
        ("冰", &["地", "翼", "草", "龙"], &["冰", "机械", "火"]),
        ("地", &["冰", "毒", "火", "电"], &["武", "草"]),
        ("幻", &["武", "毒"], &["光", "幻", "机械"]),
        ("幽", &["光", "幻", "幽"], &["恶", "普通"]),
        ("恶", &["幽", "毒", "萌"], &["光", "恶", "武"]),
        ("普通", &[], &["地", "幽", "机械"]),
        ("机械", &["冰", "地", "萌"], &["机械", "水", "火", "电"]),
        (
            "武",
            &["冰", "地", "恶", "普通", "机械"],
            &["幻", "幽", "毒", "翼", "萌", "虫"],
        ),
        ("毒", &["草", "萌"], &["地", "幽", "机械", "毒"]),
        ("水", &["地", "机械", "火"], &["冰", "草", "龙"]),
        ("火", &["冰", "机械", "草", "虫"], &["地", "水", "龙"]),
        ("电", &["水", "翼"], &["地", "电", "草", "龙"]),
        ("翼", &["武", "草", "虫"], &["地", "机械", "电", "龙"]),
        (
            "草",
            &["光", "地", "水"],
            &["机械", "毒", "火", "翼", "萌", "虫", "龙"],
        ),
        ("萌", &["恶", "武", "龙"], &["机械", "毒", "火"]),
        (
            "虫",
            &["幻", "恶", "草"],
            &["幽", "机械", "武", "毒", "火", "翼", "萌"],
        ),
        ("龙", &["龙"], &["机械"]),
    ];
    for (atk_el, doubles, halves) in pairs {
        if *atk_el == atk {
            if doubles.contains(&def) {
                return 2.0;
            }
            if halves.contains(&def) {
                return 0.5;
            }
            return 1.0;
        }
    }
    1.0
}

/// 编码输出（py encode_battle_state 返回 dict 的 Rust 形态）。
#[derive(Debug, Clone)]
pub struct EncodedState {
    pub sprite_stats: [[f32; 7]; 12],
    pub sprite_elements: [[i32; 2]; 12],
    pub sprite_states: [[f32; SPRITE_STATES_DIM]; 12],
    pub skill_stats: [[f32; 2]; 10],
    pub skill_elements: [[i32; 2]; 10],
    pub skill_states: [[f32; 9]; 10],
    pub global_stats: [f32; 15],
    pub global_elements: [i32; 1],
    pub ast_tokens: [i32; MAX_SEQ_LEN],
    pub ast_values: [f32; MAX_SEQ_LEN],
}

impl EncodedState {
    fn zeros() -> Self {
        EncodedState {
            sprite_stats: [[0.0; 7]; 12],
            sprite_elements: [[0; 2]; 12],
            sprite_states: [[0.0; SPRITE_STATES_DIM]; 12],
            skill_stats: [[0.0; 2]; 10],
            skill_elements: [[0; 2]; 10],
            skill_states: [[0.0; 9]; 10],
            global_stats: [0.0; 15],
            global_elements: [0],
            ast_tokens: [0; MAX_SEQ_LEN],
            ast_values: [0.0; MAX_SEQ_LEN],
        }
    }
}

/// py _active_at_index：active_index 越界返回 None。
fn active_at_index(state: &BattleState, pi: usize) -> Option<&Sprite> {
    let p = &state.players[pi];
    p.team.get(p.active_index)
}

/// py _sprite_primary_element。
fn sprite_primary_element(sprite: Option<&Sprite>) -> String {
    match sprite {
        Some(s) => {
            let els = s.species.elements();
            els.into_iter().next().unwrap_or_else(|| "普通".to_string())
        }
        None => String::new(),
    }
}

/// 效果快照（py _rebuild_effects_cache 的现算版）。
struct EffectSnapshot {
    stages: Vec<(String, i64)>,
    abnormals: Vec<(String, i64)>,
    charging: bool,
}

fn effects_snapshot(sprite: &Sprite) -> EffectSnapshot {
    let mut stages: Vec<(String, i64)> = Vec::new();
    let mut abnormals: Vec<(String, i64)> = Vec::new();
    let mut charging = false;
    for e in &sprite.active_effects {
        match &e.kind {
            EffectKind::StatBuff { stat_key, steps, .. } => {
                if let Some(entry) = stages.iter_mut().find(|(k, _)| k == stat_key) {
                    entry.1 += steps;
                } else {
                    stages.push((stat_key.clone(), *steps));
                }
            }
            EffectKind::Abnormal { stacks, .. } => {
                if let Some(entry) = abnormals.iter_mut().find(|(k, _)| *k == e.name) {
                    entry.1 += stacks;
                } else {
                    abnormals.push((e.name.clone(), *stacks));
                }
            }
            EffectKind::State { state_type, .. } => {
                if state_type == "charging" || e.name == "charging" {
                    charging = true;
                }
            }
            _ => {}
        }
    }
    EffectSnapshot {
        stages,
        abnormals,
        charging,
    }
}

fn stage_steps<'a>(stages: &'a [(String, i64)], key: &str) -> i64 {
    stages
        .iter()
        .find(|(k, _)| k == key)
        .map(|(_, v)| *v)
        .unwrap_or(0)
}

fn clamp1(v: f64) -> f64 {
    v.max(-1.0).min(1.0)
}

/// py _fill_sprite_entity。
fn fill_sprite_entity(
    state: &BattleState,
    idx: usize,
    sprite: &Sprite,
    out: &mut EncodedState,
) {
    out.sprite_stats[idx][0] = sprite.current_hp as f32;
    out.sprite_stats[idx][1] = sprite.max_hp.max(1) as f32;

    let snap = effects_snapshot(sprite);
    let g = |k: &str| sprite.initial_stats.get(k).copied().unwrap_or(0);
    let atk_base = g("atk");
    let def_base = g("def");
    let sp_atk_base = g("sp_atk");
    let sp_def_base = g("sp_def");
    let speed_base = g("speed");
    let atk_steps = stage_steps(&snap.stages, "atk");
    let def_steps = stage_steps(&snap.stages, "def");
    let sp_atk_steps = stage_steps(&snap.stages, "sp_atk");
    let sp_def_steps = stage_steps(&snap.stages, "sp_def");
    let speed_steps = stage_steps(&snap.stages, "speed");
    out.sprite_stats[idx][2] =
        crate::damage::py_round(atk_base as f64 * (1.0 + atk_steps as f64 / 10.0))
            .max(0) as f32;
    out.sprite_stats[idx][3] =
        crate::damage::py_round(def_base as f64 * (1.0 + def_steps as f64 / 10.0))
            .max(0) as f32;
    out.sprite_stats[idx][4] =
        crate::damage::py_round(sp_atk_base as f64 * (1.0 + sp_atk_steps as f64 / 10.0))
            .max(0) as f32;
    out.sprite_stats[idx][5] =
        crate::damage::py_round(sp_def_base as f64 * (1.0 + sp_def_steps as f64 / 10.0))
            .max(0) as f32;
    out.sprite_stats[idx][6] = (speed_base + speed_steps * 10).max(0) as f32;

    // 元素：主/副（空 attributes → 主=普通）
    let els = sprite.species.elements();
    out.sprite_elements[idx][0] = match els.first() {
        Some(e) => element_cat_id(e),
        None => element_cat_id("普通"),
    };
    out.sprite_elements[idx][1] = match els.get(1) {
        Some(e) => element_cat_id(e),
        None => 0,
    };

    // states 前 25 维
    let s = &mut out.sprite_states[idx];
    s[0] = sprite.energy as f32 / sprite.max_energy().max(1) as f32;
    s[1] = if sprite.is_fainted() { 1.0 } else { 0.0 };
    s[2] = sprite.current_hp as f32 / sprite.max_hp.max(1) as f32;
    s[3] = (sprite.locked_turns as f32 / 3.0).min(1.0);
    s[4] = if snap.charging { 1.0 } else { 0.0 };
    s[5] = if sprite.pending_return { 1.0 } else { 0.0 };
    s[6] = if sprite.first_action { 1.0 } else { 0.0 };
    s[7] = if sprite.extra_skill_use { 1.0 } else { 0.0 };
    s[8] = if sprite.interrupted { 1.0 } else { 0.0 };
    s[9] = ((state.turn - sprite.entry_turn) as f32 / 20.0).min(1.0);
    s[10] = if sprite.trait_suppressed { 1.0 } else { 0.0 };

    // 12-18: 异常层数 / 上限，封顶 1
    for (i, an) in ABNORMAL_ORDER.iter().enumerate() {
        let stacks = snap
            .abnormals
            .iter()
            .find(|(k, _)| k == an)
            .map(|(_, v)| *v)
            .unwrap_or(0);
        s[11 + i] = ((stacks as f64) / ABNORMAL_MAX[i]).min(1.0) as f32;
    }

    // 19-25: buff 步数（含 power/energy_cost 修正截断）
    s[18] = clamp1(atk_steps as f64 / 10.0) as f32;
    s[19] = clamp1(def_steps as f64 / 10.0) as f32;
    s[20] = clamp1(sp_atk_steps as f64 / 10.0) as f32;
    s[21] = clamp1(sp_def_steps as f64 / 10.0) as f32;
    s[22] = clamp1(speed_steps as f64 / 10.0) as f32;
    let pow_mod = sprite.modifier("power", 0.0) as i64;
    let ec_mod = sprite.modifier("energy_cost", 0.0) as i64;
    s[23] = clamp1(pow_mod as f64 / 10.0) as f32;
    s[24] = clamp1(ec_mod as f64 / 10.0) as f32;
}

/// py _fill_bench_skill_summary：场下精灵技能摘要（row 的 25..105 维）。
fn fill_bench_skill_summary(
    sprite: &Sprite,
    opp_active: Option<&Sprite>,
    row: &mut [f32; SPRITE_STATES_DIM],
) {
    let def_element = sprite_primary_element(opp_active);
    let skills: &[BattleSkill] = &sprite.skills;
    for i in 0..10 {
        let offset = SPRITE_STATES_BASE + i * SKILL_SUMMARY_PER_SKILL;
        if i >= skills.len() {
            continue;
        }
        let sk = &skills[i];
        if sk.sealed {
            continue;
        }
        row[offset] = sk.power() as f32 / 300.0;
        row[offset + 1] = sk.energy_cost() as f32 / 15.0;
        let type_idx = skill_type_onehot(&sk.skill_type());
        for t in 0..5 {
            row[offset + 2 + t] = if t == type_idx { 1.0 } else { 0.0 };
        }
        let sk_element = sk.element();
        if !def_element.is_empty() && !sk_element.is_empty() {
            let adv = type_advantage(&sk_element, &def_element);
            row[offset + 7] = ((adv - 0.5) / 1.5) as f32;
        } else {
            row[offset + 7] = 0.0;
        }
    }
}

/// py _fill_bench_entities：5 个板凳槽位（固定索引，跳 active 不跳力竭）。
fn fill_bench_entities(
    state: &BattleState,
    pi: usize,
    start_idx: usize,
    mask_unknown: bool,
    opp_active: Option<&Sprite>,
    out: &mut EncodedState,
) {
    if mask_unknown {
        return;
    }
    let player = &state.players[pi];
    let mut slot = 0usize;
    for (team_idx, sprite) in player.team.iter().enumerate() {
        if team_idx == player.active_index {
            continue;
        }
        if slot >= 5 {
            break;
        }
        fill_sprite_entity(state, start_idx + slot, sprite, out);
        let row_copy = out.sprite_states[start_idx + slot];
        let mut row = row_copy;
        fill_bench_skill_summary(sprite, opp_active, &mut row);
        out.sprite_states[start_idx + slot] = row;
        slot += 1;
    }
}

/// py _fill_skill_entities：10 个技能槽实体（己方场上精灵）。
fn fill_skill_entities(skills: &[BattleSkill], out: &mut EncodedState) {
    for i in 0..10 {
        if i >= skills.len() {
            continue;
        }
        let sk = &skills[i];
        out.skill_stats[i][0] = sk.power() as f32;
        out.skill_stats[i][1] = sk.energy_cost() as f32;
        out.skill_elements[i][0] = element_cat_id(&sk.element());
        out.skill_elements[i][1] = 0;
        let s = &mut out.skill_states[i];
        s[0] = if sk.sealed { 1.0 } else { 0.0 };
        s[1] = sk.cooldown as f32;
        let type_idx = skill_type_onehot(&sk.skill_type());
        for t in 0..5 {
            s[2 + t] = if t == type_idx { 1.0 } else { 0.0 };
        }
        s[7] = sk.combo() as f32;
        s[8] = sk.transmission as f32;
    }
}

/// py _fill_global_entity。印记特征恒 0（py `g.marks` 属性不存在，见模块注释）。
fn fill_global_entity(
    state: &BattleState,
    own_team: usize,
    out: &mut EncodedState,
) {
    let own = &state.players[own_team];
    let opp = &state.players[1 - own_team];
    let g = &state.globals;

    out.global_stats[0] = state.turn as f32 / 150.0;
    out.global_stats[1] = (g.weather_turns as f32 / 8.0).min(1.0);
    out.global_stats[2] = own.lives as f32 / 6.0;
    out.global_stats[3] = opp.lives as f32 / 6.0;
    // stats[4..8] 印记特征：恒 0（py 死特征，勿"修复"）
    out.global_stats[4] = 0.0;
    out.global_stats[5] = 0.0;
    out.global_stats[6] = 0.0;
    out.global_stats[7] = 0.0;

    for (base, p) in [(8usize, own), (11usize, opp)] {
        match &p.item {
            Some(item) => {
                out.global_stats[base] = 1.0;
                out.global_stats[base + 1] =
                    item.uses as f32 / item.max_uses.max(1) as f32;
                out.global_stats[base + 2] =
                    if item.uses >= item.max_uses { 1.0 } else { 0.0 };
            }
            None => {
                out.global_stats[base] = 0.0;
                out.global_stats[base + 1] = 0.0;
                out.global_stats[base + 2] = 0.0;
            }
        }
    }

    let max_dev = DEVOTION_ORDER
        .iter()
        .filter_map(|dn| own.devotion.get(*dn).copied())
        .max()
        .unwrap_or(0);
    out.global_stats[14] = max_dev as f32 / 5.0;

    let w = if g.weather.is_empty() { "none" } else { g.weather.as_str() };
    out.global_elements[0] = WEATHER_ORDER
        .iter()
        .position(|&x| x == w)
        .map(|i| i as i32)
        .unwrap_or(0);
}

/// py encode_battle_state（4-5a：实体数组；ast 恒 0 待 4-5b）。
pub fn encode_battle_state(
    state: &BattleState,
    perspective: usize,
    mask_opp_bench: bool,
) -> EncodedState {
    let own_pi = perspective;
    let opp_pi = 1 - perspective;

    let mut out = EncodedState::zeros();
    let own_active = active_at_index(state, own_pi);
    let opp_active = active_at_index(state, opp_pi);

    if let Some(sa) = own_active {
        fill_sprite_entity(state, 0, sa, &mut out);
    }
    fill_bench_entities(state, own_pi, 1, false, opp_active, &mut out);
    if let Some(sb) = opp_active {
        fill_sprite_entity(state, 6, sb, &mut out);
    }
    fill_bench_entities(state, opp_pi, 7, mask_opp_bench, own_active, &mut out);

    if let Some(sa) = own_active {
        fill_skill_entities(&sa.skills, &mut out);
    }

    fill_global_entity(state, own_pi, &mut out);

    // ── AST 序列（4-5b）──
    let mut ids: Vec<i32> = Vec::with_capacity(256);
    let mut vals: Vec<f64> = Vec::with_capacity(256);
    collect_ast_token_ids(own_active, &mut ids, &mut vals);
    for i in 0..MAX_SEQ_LEN {
        if i >= ids.len() {
            break;
        }
        out.ast_tokens[i] = ids[i];
        out.ast_values[i] = vals[i] as f32;
    }
    out
}

// ═══════════════════════════════════════════════════════════════════
// AST tokenizer（py encoder.py 527-1080 的镜像）
// ═══════════════════════════════════════════════════════════════════

use std::collections::HashMap;
use std::sync::{Arc, Mutex, OnceLock};

/// VOCAB_TO_ID（py vocab.ALL_TOKENS 静态表的导出，见 dump 脚本）。
fn vocab() -> &'static HashMap<String, i32> {
    static VOCAB: OnceLock<HashMap<String, i32>> = OnceLock::new();
    VOCAB.get_or_init(|| {
        serde_json::from_str(include_str!("vocab_ids.json")).expect("vocab_ids.json 解析失败")
    })
}

fn unk_id() -> i32 {
    static UNK: OnceLock<i32> = OnceLock::new();
    *UNK.get_or_init(|| vocab()["<UNK>"])
}

fn tok_id(token: &str) -> i32 {
    vocab().get(token).copied().unwrap_or_else(unk_id)
}

/// py _ALIAS_MAP（JSON 值 → vocab 枚举 token）。
const ALIAS_MAP: &[(&str, &str)] = &[
    ("self", "TGT_SPRITE_SELF"),
    ("sprite_self", "TGT_SPRITE_SELF"),
    ("opp", "TGT_SPRITE_OPP"),
    ("sprite_opp", "TGT_SPRITE_OPP"),
    ("own_team", "TGT_TEAM_OWN"),
    ("opp_team", "TGT_TEAM_OPP"),
    ("owner", "TGT_SPRITE_OWNER"),
    ("battlefield", "SCOPE_BATTLEFIELD"),
    ("turn", "SCOPE_TURN"),
    ("persistent", "SCOPE_PERSISTENT"),
    ("permanent", "SCOPE_PERMANENT"),
    ("物攻", "TYPE_PHYSICAL"),
    ("魔攻", "TYPE_SPECIAL"),
    ("动态攻击", "TYPE_DYNAMIC"),
    ("防御", "TYPE_DEFENSE"),
    ("状态", "TYPE_STATUS"),
    ("无", "CTR_NONE"),
    ("攻击", "CTR_ATTACK"),
    ("atk", "ATTR_ATK"),
    ("def", "ATTR_DEF"),
    ("sp_atk", "ATTR_SP_ATK"),
    ("sp_def", "ATTR_SP_DEF"),
    ("speed", "ATTR_SPEED"),
    ("hp", "ATTR_HP"),
    ("energy", "ATTR_ENERGY"),
    ("power", "ATTR_POWER"),
    ("energy_cost", "ATTR_ENERGY_COST"),
    ("priority", "ATTR_PRIORITY"),
    ("combo", "ATTR_COMBO"),
    ("stacks", "ATTR_STACKS"),
    ("ratio", "ATTR_RATIO"),
    ("cooldown", "ATTR_COOLDOWN"),
    ("value", "ATTR_VALUE"),
    ("accuracy", "ATTR_ACCURACY"),
    ("rain", "WTH_RAIN"),
    ("sand", "WTH_SAND"),
    ("snow", "WTH_SNOW"),
    ("灼烧", "ABN_BURN"),
    ("冻结", "ABN_FREEZE"),
    ("中毒", "ABN_POISON"),
    ("寄生", "ABN_PARASITE"),
    ("萌化", "ABN_MOE"),
    ("晕眩", "ABN_DIZZY"),
    ("眩晕", "ABN_STUN"),
    ("光", "ELEM_LIGHT"),
    ("冰", "ELEM_ICE"),
    ("地", "ELEM_EARTH"),
    ("幻", "ELEM_ILLUSION"),
    ("幽", "ELEM_GHOST"),
    ("恶", "ELEM_DARK"),
    ("普通", "ELEM_NORMAL"),
    ("机械", "ELEM_MACHINE"),
    ("武", "ELEM_FIGHT"),
    ("毒", "ELEM_POISON"),
    ("水", "ELEM_WATER"),
    ("火", "ELEM_FIRE"),
    ("电", "ELEM_ELECTRIC"),
    ("翼", "ELEM_WING"),
    ("草", "ELEM_GRASS"),
    ("萌", "ELEM_CUTE"),
    ("虫", "ELEM_BUG"),
    ("龙", "ELEM_DRAGON"),
];

fn alias_map() -> &'static HashMap<&'static str, &'static str> {
    static M: OnceLock<HashMap<&'static str, &'static str>> = OnceLock::new();
    M.get_or_init(|| ALIAS_MAP.iter().copied().collect())
}

fn alias_upper_map() -> &'static HashMap<String, &'static str> {
    static M: OnceLock<HashMap<String, &'static str>> = OnceLock::new();
    M.get_or_init(|| {
        ALIAS_MAP
            .iter()
            .map(|(k, v)| (k.to_uppercase(), *v))
            .collect()
    })
}

/// py _encode_value 的枚举前缀组（顺序即尝试顺序）。
const ENUM_PREFIX_GROUPS: &[&str] = &[
    "TGT_", "ATTR_", "ELEM_", "WTH_", "ABN_", "MARK_", "SCOPE_", "SKTYPE_", "TYPE_", "CTR_",
    "COND_", "CMP_", "WHAT_", "OF_", "Q_", "FROM_", "AT_", "ACT_", "SRC_", "BLD_", "TAG_",
];

/// py _try_enum_token 的前缀匹配段（别名已在调用方处理）。
fn try_enum_token(v: &str) -> Option<String> {
    let v_upper = v.to_uppercase().replace(' ', "_").replace('-', "_");
    for prefix in ENUM_PREFIX_GROUPS {
        let candidate = format!("{prefix}{v_upper}");
        if vocab().contains_key(&candidate) {
            return Some(candidate);
        }
    }
    None
}

/// token 流写入器（py _add_tok：tokens/values 同步增长）。
struct TokOut {
    ids: Vec<i32>,
    values: Vec<f64>,
}

impl TokOut {
    fn add(&mut self, token: &str, val: f64) {
        self.ids.push(tok_id(token));
        self.values.push(val);
    }
}

/// py _parse_query：Query 寄存器查询。
fn parse_query(query: &serde_json::Map<String, serde_json::Value>, out: &mut TokOut) {
    out.add("<B_QUERY>", 0.0);
    for (k, v) in query {
        let key_str = format!("KEY_{}", k.to_uppercase());
        out.add(&key_str, 0.0);
        encode_value(v, out);
    }
    out.add("<E_QUERY>", 0.0);
}

fn is_and_or_not(v: &serde_json::Value) -> bool {
    matches!(v.as_str(), Some(s) if s == "and" || s == "or" || s == "not")
}

/// py _parse_cond：递归条件表达式。
fn parse_cond(cond: &serde_json::Value, out: &mut TokOut) {
    if let Some(s) = cond.as_str() {
        let cond_token = format!("COND_{}", s.to_uppercase());
        out.add(&cond_token, 0.0);
        return;
    }
    let obj = match cond.as_object() {
        Some(o) => o,
        None => return,
    };
    out.add("<B_COND>", 0.0);

    let cond_type = obj
        .get("cond")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    out.add("KEY_COND", 0.0);
    out.add(&format!("COND_{}", cond_type.to_uppercase()), 0.0);

    match cond_type.as_str() {
        "and" | "or" => {
            if let Some(conds) = obj.get("conditions").and_then(|v| v.as_array()) {
                for sub in conds {
                    parse_cond(sub, out);
                }
            }
        }
        "not" => {
            if let Some(sub) = obj.get("condition") {
                if sub.is_object() {
                    parse_cond(sub, out);
                }
            }
        }
        _ => {
            for (k, v) in obj {
                if k == "cond" {
                    continue;
                }
                let key_str = format!("KEY_{}", k.to_uppercase());
                out.add(&key_str, 0.0);
                encode_value(v, out);
            }
        }
    }
    out.add("<E_COND>", 0.0);
}

/// py _encode_value：递归编码任意 IR 值节点。
fn encode_value(v: &serde_json::Value, out: &mut TokOut) {
    match v {
        serde_json::Value::Null => out.add("<PAD>", 0.0),
        serde_json::Value::Bool(b) => out.add("VAL_BOOL", if *b { 1.0 } else { 0.0 }),
        serde_json::Value::Number(n) => {
            let f = n.as_f64().unwrap_or(0.0);
            out.add("VAL_NUMERIC", f);
        }
        serde_json::Value::String(s) => {
            if let Some(alias) = alias_map().get(s.as_str()) {
                out.add(alias, 0.0);
                return;
            }
            if let Some(alias) = alias_upper_map().get(&s.to_uppercase()) {
                out.add(alias, 0.0);
                return;
            }
            if let Some(tok) = try_enum_token(s) {
                out.add(&tok, 0.0);
                return;
            }
            out.add("VAL_STRING", 0.0);
        }
        serde_json::Value::Object(obj) => {
            if obj.contains_key("q") {
                parse_query(obj, out);
            } else if let Some(cv) = obj.get("cond") {
                if is_and_or_not(cv) {
                    parse_cond(v, out);
                } else {
                    // 简单内联条件：仅提取 cond/op/then/else（py 只搬这四个键）
                    let mut sub = serde_json::Map::new();
                    if let Some(c) = obj.get("cond") {
                        sub.insert("cond".to_string(), c.clone());
                    }
                    if let Some(o) = obj.get("op") {
                        sub.insert("op".to_string(), o.clone());
                    }
                    if let Some(t) = obj.get("then") {
                        sub.insert("then".to_string(), t.clone());
                    }
                    if let Some(e) = obj.get("else") {
                        sub.insert("else".to_string(), e.clone());
                    }
                    tokenize_effect_dfs(&serde_json::Value::Object(sub), out);
                }
            } else {
                for (k, sv) in obj {
                    let key_str = format!("KEY_{}", k.to_uppercase());
                    out.add(&key_str, 0.0);
                    encode_value(sv, out);
                }
            }
        }
        // py: isinstance(v, (list, tuple)) → pass（列表在值位置整体丢弃）
        serde_json::Value::Array(_) => {}
    }
}

/// py tokenize_effect_dfs：深度优先展平单个 effect dict。
fn tokenize_effect_dfs(effect: &serde_json::Value, out: &mut TokOut) {
    let obj = match effect.as_object() {
        Some(o) => o,
        None => return,
    };
    out.add("<B_EFFECT>", 0.0);

    // ── 1. 控制流 when-then-else ──
    if let Some(when_block) = obj.get("when") {
        out.add("<B_WHEN>", 0.0);
        parse_cond(when_block, out);
        out.add("<E_WHEN>", 0.0);

        // py：then 用 <B_THEN>/<E_THEN>；else_if 与 else 都用 <B_ELSE>/<E_ELSE>
        for (branch, begin, end) in [
            ("then", "<B_THEN>", "<E_THEN>"),
            ("else_if", "<B_ELSE>", "<E_ELSE>"),
            ("else", "<B_ELSE>", "<E_ELSE>"),
        ] {
            if let Some(subs) = obj.get(branch).and_then(|v| v.as_array()) {
                out.add(begin, 0.0);
                for sub in subs {
                    if sub.is_object() {
                        tokenize_effect_dfs(sub, out);
                    }
                }
                out.add(end, 0.0);
            }
        }

        out.add("<E_EFFECT>", 0.0);
        return;
    }

    // ── 2. 标准操作 op ──
    if let Some(op) = obj.get("op") {
        let op_str = match op.as_str() {
            Some(s) => format!("OP_{}", s.to_uppercase()),
            None => "OP_".to_string(),
        };
        out.add("KEY_OP", 0.0);
        out.add(&op_str, 0.0);

        let is_observer = op.as_str() == Some("observer");

        for (k, v) in obj {
            if k == "op" {
                continue;
            }
            if is_observer && (k == "cond" || k == "then") {
                continue;
            }
            let key_str = format!("KEY_{}", k.to_uppercase());
            out.add(&key_str, 0.0);
            if let Some(items) = v.as_array() {
                for item in items {
                    encode_value(item, out);
                }
            } else {
                encode_value(v, out);
            }
        }

        if is_observer {
            if let Some(cond) = obj.get("cond") {
                parse_cond(cond, out);
            }
            if let Some(then_list) = obj.get("then").and_then(|v| v.as_array()) {
                out.add("<B_THEN>", 0.0);
                for sub in then_list {
                    if sub.is_object() {
                        tokenize_effect_dfs(sub, out);
                    }
                }
                out.add("<E_THEN>", 0.0);
            }
        }
    }

    out.add("<E_EFFECT>", 0.0);
}

/// py _get_skill_effects：data/skills/{name}.json 的 effects（文件级缓存）。
fn skill_effect_ids(
    name: &str,
    cache: &mut HashMap<String, Arc<(Vec<i32>, Vec<f64>)>>,
) -> Arc<(Vec<i32>, Vec<f64>)> {
    if let Some(c) = cache.get(name) {
        return Arc::clone(c);
    }
    let mut out = TokOut {
        ids: Vec::new(),
        values: Vec::new(),
    };
    let path = std::path::Path::new("data")
        .join("skills")
        .join(format!("{name}.json"));
    if let Ok(text) = std::fs::read_to_string(path) {
        if let Ok(data) = serde_json::from_str::<serde_json::Value>(&text) {
            if let Some(effects) = data.get("effects").and_then(|v| v.as_array()) {
                for eff in effects {
                    tokenize_effect_dfs(eff, &mut out);
                }
            }
        }
    }
    let arc = Arc::new((out.ids, out.values));
    cache.insert(name.to_string(), Arc::clone(&arc));
    arc
}

/// py 真值语义（`if eff.cond:`）：Null/空容器/空串/0/False 为假。
fn cond_truthy(v: &serde_json::Value) -> bool {
    match v {
        serde_json::Value::Null => false,
        serde_json::Value::Bool(b) => *b,
        serde_json::Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(false),
        serde_json::Value::String(s) => !s.is_empty(),
        serde_json::Value::Array(a) => !a.is_empty(),
        serde_json::Value::Object(m) => !m.is_empty(),
    }
}

/// py observer.py 的 post 系触发点集合（scope 烘焙条件）。
const POST_EVENT_TRIGGERS: &[&str] = &[
    "post_skill", "post_damage", "post_ko", "post_switch", "post_entry", "post_leave",
    "post_enemy_leave", "post_counter", "post_abnormal_tick", "post_abnormal_change",
    "post_abnormal_apply", "post_energy_change", "post_heal", "post_positive_change",
    "turn_end",
];

/// py observer.py _bake_inject_source/_bake_inject_scope：在 effects 树中
/// 注入 source/scope（缺键才注入、带 "op" 的 dict 才注入、then/else 递归）。
fn bake_inject(effects: &mut [serde_json::Value], key: &str, val: &str) {
    for eff in effects.iter_mut() {
        let Some(obj) = eff.as_object_mut() else { continue };
        if obj.contains_key("op") && !obj.contains_key(key) {
            obj.insert(key.to_string(), serde_json::Value::String(val.to_string()));
        }
        if let Some(then_list) = obj.get_mut("then").and_then(|v| v.as_array_mut()) {
            bake_inject(then_list, key, val);
        }
        if let Some(else_list) = obj.get_mut("else").and_then(|v| v.as_array_mut()) {
            bake_inject(else_list, key, val);
        }
    }
}

/// py _collect_ast_token_ids：己方场上精灵 10 技能槽 + 特性 observer。
pub fn collect_ast_token_ids(active: Option<&Sprite>, ids: &mut Vec<i32>, values: &mut Vec<f64>) {
    const SAFE_MARGIN: usize = 50;
    let Some(sprite) = active else { return };

    let mut out = TokOut {
        ids: Vec::new(),
        values: Vec::new(),
    };
    let mut skill_cache: HashMap<String, Arc<(Vec<i32>, Vec<f64>)>> = HashMap::new();

    let skills: &[BattleSkill] = &sprite.skills;
    for i in 0..10 {
        if out.ids.len() > MAX_SEQ_LEN - SAFE_MARGIN {
            break;
        }
        out.add("<SEP>", 0.0);
        if i >= skills.len() {
            out.add("<EMPTY_SKILL>", 0.0);
            continue;
        }
        let sk = &skills[i];
        if sk.sealed || sk.cooldown > 0 {
            out.add("<SEALED_SKILL>", if sk.sealed { 1.0 } else { 0.5 });
        } else {
            out.add("<ACTIVE_SKILL>", 1.0);
        }
        let (t, v) = {
            let arc = skill_effect_ids(&sk.name(), &mut skill_cache);
            (arc.0.clone(), arc.1.clone())
        };
        out.ids.extend(t);
        out.values.extend(v);
    }

    for e in &sprite.active_effects {
        if out.ids.len() > MAX_SEQ_LEN - SAFE_MARGIN {
            break;
        }
        let EffectKind::Observer {
            cond,
            then,
            listen,
            ..
        } = &e.kind
        else {
            continue;
        };
        // py observer_dict 插入序：op, cond?, then?, listen?, scope?（py 真值判空）
        let mut od = serde_json::Map::new();
        od.insert("op".to_string(), serde_json::Value::String("observer".into()));
        if cond_truthy(cond) {
            od.insert("cond".to_string(), cond.clone());
        }
        if !then.is_empty() {
            // py observer.py _index：注册时对共享 then 树原地烘焙（编码器经
            // 共享 dict 看到烘焙后形态）——source 恒注入；scope 仅当 listen
            // ∩ POST_EVENT_TRIGGERS 非空且 then[0] 为 dict 时注入
            // （post 系触发点才烘 scope，pre 系保持 parser 默认）。
            let mut then_clone = then.clone();
            if !e.source.is_empty() {
                bake_inject(&mut then_clone, "source", &e.source);
            }
            // py 注册期 listen = 显式 listen，缺省时由 cond 推断
            //（trait_loader/compiler 语义，rust load_traits 同源 infer_triggers）
            let effective_listen: Vec<String> = if !listen.is_empty() {
                listen.clone()
            } else {
                crate::cond::infer_triggers(cond).into_iter().collect()
            };
            let listen_is_post = effective_listen
                .iter()
                .any(|l| POST_EVENT_TRIGGERS.contains(&l.as_str()));
            if listen_is_post && then_clone.first().is_some_and(|v| v.is_object()) {
                bake_inject(&mut then_clone, "scope", &e.scope);
            }
            od.insert("then".to_string(), serde_json::Value::Array(then_clone));
        }
        if !listen.is_empty() {
            // py 侧 listen 是 frozenset，list(frozenset) 为哈希序（跨进程
            // 不稳定）——已修 py 编码器改用 sorted()；rust 同步按字典序输出
            let mut ls: Vec<&String> = listen.iter().collect();
            ls.sort();
            od.insert(
                "listen".to_string(),
                serde_json::Value::Array(
                    ls.into_iter().map(|s| serde_json::Value::String(s.clone())).collect(),
                ),
            );
        }
        if !e.scope.is_empty() {
            od.insert("scope".to_string(), serde_json::Value::String(e.scope.clone()));
        }
        out.add("<SEP>", 0.0);
        tokenize_effect_dfs(&serde_json::Value::Object(od), &mut out);
    }

    ids.extend(out.ids);
    values.extend(out.values);
}
