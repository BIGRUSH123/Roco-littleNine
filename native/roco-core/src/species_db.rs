//! species_db — 精灵种族值数据库（镜像 backend/common/sprite_db.py +
//! backend/common/formulas.py StatsCalc）。
//!
//! 数据由 native/tools/dump_species.py 从 data/sprites/*.json 导出，
//! 编译期 include_str! 内嵌。用于形态变换（transform/萌化退化）。

use crate::battle_state::Species;
use serde::Deserialize;
use std::collections::BTreeMap;
use std::sync::OnceLock;

#[derive(Debug, Clone, Deserialize)]
pub struct SpeciesEntry {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub form: String,
    #[serde(default)]
    pub number: String,
    #[serde(default)]
    pub hp: i64,
    #[serde(default)]
    pub atk: i64,
    #[serde(default)]
    pub sp_atk: i64,
    #[serde(default)]
    pub def: i64,
    #[serde(default)]
    pub sp_def: i64,
    #[serde(default)]
    pub speed: i64,
    #[serde(default)]
    pub attributes: Vec<String>,
    #[serde(default)]
    pub ability: String,
    #[serde(default)]
    pub ability_id: i64,
    #[serde(default)]
    pub pre_species: String,
    #[serde(default)]
    pub bloodline_skills: BTreeMap<String, i64>,
}

impl SpeciesEntry {
    /// py SpeciesStats.display_name()：form 非空 → "name（form）"。
    pub fn display(&self) -> String {
        if self.form.is_empty() {
            self.name.clone()
        } else {
            format!("{}（{}）", self.name, self.form)
        }
    }

    pub fn base_dict(&self) -> BTreeMap<String, i64> {
        let mut m = BTreeMap::new();
        m.insert("hp".into(), self.hp);
        m.insert("atk".into(), self.atk);
        m.insert("sp_atk".into(), self.sp_atk);
        m.insert("def".into(), self.def);
        m.insert("sp_def".into(), self.sp_def);
        m.insert("speed".into(), self.speed);
        m
    }

    pub fn to_species(&self) -> Species {
        Species {
            name: self.name.clone(),
            number: self.number.clone(),
            attributes: self.attributes.join(","),
            bloodline: self.attributes.first().cloned().unwrap_or_default(),
            ability: self.ability.clone(),
            ability_id: self.ability_id,
            pre_species: self.pre_species.clone(),
            bloodline_skills: self.bloodline_skills.clone(),
            base: self.base_dict(),
        }
    }
}

#[derive(Debug, Deserialize)]
struct DbJson {
    #[serde(default)]
    by_display: BTreeMap<String, SpeciesEntry>,
    #[serde(default)]
    by_number: BTreeMap<String, Vec<SpeciesEntry>>,
}

struct Db {
    by_display: BTreeMap<String, SpeciesEntry>,
    by_number: BTreeMap<String, Vec<SpeciesEntry>>,
}

fn db() -> &'static Db {
    static DB: OnceLock<Db> = OnceLock::new();
    DB.get_or_init(|| {
        let raw = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/../species_db.json"));
        let parsed: DbJson = serde_json::from_str(raw).expect("species_db.json 解析失败");
        Db {
            by_display: parsed.by_display,
            by_number: parsed.by_number,
        }
    })
}

fn strip_form_suffix(name: &str) -> (String, Option<String>) {
    // py：_RE_FORM_SUFFIX = r'（([^）]+)）$'
    if let Some(pos) = name.rfind('（') {
        if name.ends_with('）') && pos + '（'.len_utf8() < name.len() - '）'.len_utf8() {
            let inner = &name[pos + '（'.len_utf8()..name.len() - '）'.len_utf8()];
            if !inner.contains('）') && !inner.is_empty() {
                return (name[..pos].to_string(), Some(inner.to_string()));
            }
        }
    }
    (name.to_string(), None)
}

/// py SpriteDB.get(name, form)：精确 display → 唯一名字 → form 匹配 →
/// 基础形态（空 form）→ 第一个可用形态。
pub fn get(name: &str, form: &str) -> Option<&'static SpeciesEntry> {
    let d = db();
    let (mut base_name, suffix_form) = strip_form_suffix(name);
    let mut the_form = form.to_string();
    if the_form.is_empty() {
        if let Some(f) = suffix_form {
            the_form = f;
        }
    } else {
        base_name = name.to_string();
    }
    let display = if the_form.is_empty() {
        base_name.clone()
    } else {
        format!("{base_name}（{the_form}）")
    };
    if let Some(e) = d.by_display.get(&display) {
        return Some(e);
    }
    let candidates: Vec<&SpeciesEntry> = d
        .by_display
        .values()
        .filter(|e| e.name == base_name)
        .collect();
    if candidates.len() == 1 {
        return candidates.first().copied();
    }
    if candidates.is_empty() {
        return None;
    }
    if let Some(e) = candidates.iter().find(|e| e.form == the_form) {
        return Some(e);
    }
    if let Some(e) = candidates.iter().find(|e| e.form.is_empty()) {
        return Some(e);
    }
    candidates.first().copied()
}

/// py SpriteDB.lookup_by_number(number, form)：编号下优先匹配 form，
/// 否则第一个。
pub fn lookup_by_number(number: &str, form: &str) -> Option<&'static SpeciesEntry> {
    let d = db();
    let list = d.by_number.get(number)?;
    if let Some(e) = list.iter().find(|e| e.form == form) {
        return Some(e);
    }
    list.first()
}

// ── StatsCalc（backend/common/formulas.py）──

/// 正向四舍五入（避开 Python 银行家舍入）。
fn half_round(x: f64) -> i64 {
    if x >= 0.0 {
        (x + 0.5) as i64
    } else {
        -((-x + 0.5) as i64)
    }
}

const STAT_KEYS: &[&str] = &["hp", "atk", "sp_atk", "def", "sp_def", "speed"];

/// 性格表（backend/common/nature.py NATURE_TABLE）：(plus, minus)。
fn nature_coeff(nature: Option<&str>) -> Option<BTreeMap<String, f64>> {
    const TABLE: &[(&str, &str, &str)] = &[
        ("聪明", "sp_atk", "atk"), ("专注", "sp_atk", "def"),
        ("偏执", "sp_atk", "sp_def"), ("冷静", "sp_atk", "speed"),
        ("理性", "sp_atk", "hp"),
        ("固执", "atk", "sp_atk"), ("大胆", "atk", "def"),
        ("调皮", "atk", "sp_def"), ("勇敢", "atk", "speed"),
        ("逞强", "atk", "hp"),
        ("警惕", "sp_def", "atk"), ("害羞", "sp_def", "sp_atk"),
        ("温顺", "sp_def", "def"), ("慎重", "sp_def", "speed"),
        ("焦虑", "sp_def", "hp"),
        ("稳重", "def", "atk"), ("天真", "def", "sp_atk"),
        ("悠闲", "def", "speed"), ("懒散", "def", "sp_def"),
        ("坦率", "def", "hp"),
        ("胆小", "speed", "atk"), ("开朗", "speed", "sp_atk"),
        ("急躁", "speed", "def"), ("莽撞", "speed", "sp_def"),
        ("热情", "speed", "hp"),
        ("沉默", "hp", "atk"), ("平和", "hp", "sp_atk"),
        ("忧郁", "hp", "def"), ("粗心", "hp", "sp_def"),
        ("踏实", "hp", "speed"),
    ];
    let mut coeffs: BTreeMap<String, f64> =
        STAT_KEYS.iter().map(|k| (k.to_string(), 1.0)).collect();
    let n = nature?;
    let (_, plus, minus) = TABLE.iter().find(|(name, _, _)| *name == n)?;
    coeffs.insert((*plus).to_string(), 1.20);
    coeffs.insert((*minus).to_string(), 0.90);
    Some(coeffs)
}

/// py StatsCalc.compute(...).final_stats（mods 为空，跳过 apply_mods）。
pub fn compute_final_stats(
    base: &BTreeMap<String, i64>,
    nature: Option<&str>,
    iv: &BTreeMap<String, i64>,
) -> BTreeMap<String, i64> {
    let coeffs = nature_coeff(nature).unwrap_or_else(|| {
        STAT_KEYS.iter().map(|k| (k.to_string(), 1.0)).collect()
    });
    let mut out = BTreeMap::new();
    for k in STAT_KEYS {
        let b = base.get(*k).copied().unwrap_or(0);
        let i = iv.get(*k).copied().unwrap_or(0);
        let l = (b as f64 + i as f64 * 3.0) / 100.0;
        let initial = if *k == "hp" {
            half_round(170.0 * l + 70.0)
        } else {
            (110.0 * l + 10.0).floor() as i64
        };
        let c = coeffs.get(*k).copied().unwrap_or(1.0);
        let final_v = if *k == "hp" {
            half_round(initial as f64 * c + 100.0)
        } else {
            half_round(initial as f64 * c + 50.0)
        };
        out.insert((*k).to_string(), final_v);
    }
    out
}
