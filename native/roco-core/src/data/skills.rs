//! skills — 技能 JSON（data/skills/*.json）→ 强类型结构。
//! 字段与 backend/sim/skill.py::Skill.load 对齐。

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct SkillJson {
    #[serde(default)]
    pub id: i64,
    pub name: String,
    #[serde(default)]
    pub element: String,
    #[serde(default)]
    pub skill_type: String,
    #[serde(default)]
    pub power: i64,
    #[serde(default)]
    pub energy_cost: i64,
    #[serde(default)]
    pub counter: String,
    #[serde(default)]
    pub priority: i64,
    /// 连击数：-1=不参与连击（JSON 显式写），1=单次，2+=多次。
    /// 缺省 1=单次，与 py SimFactory._build_skill_list 一致（Skill.load
    /// 曾缺省 -1 造成双加载器不一致，已统一为 1）。
    #[serde(default = "default_combo")]
    pub combo: i64,
    #[serde(default)]
    pub effects: Vec<serde_json::Value>,
    #[serde(default)]
    pub exclusive_to: String,
    #[serde(default)]
    pub transmission: i64,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub usable_while_charging: bool,
    #[serde(default)]
    pub use_devotion: bool,
}

fn default_combo() -> i64 {
    1
}

/// 从项目 data/skills 目录加载全部技能（文件名 = 技能名）。
pub fn load_skill_dir(dir: &std::path::Path) -> std::io::Result<Vec<SkillJson>> {    let mut out = Vec::new();
    for entry in std::fs::read_dir(dir)? {
        let entry = entry?;
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) == Some("json") {
            if path.file_name().and_then(|n| n.to_str()) == Some("_ids.json") {
                continue;
            }
            let text = std::fs::read_to_string(&path)?;
            match serde_json::from_str::<SkillJson>(&text) {
                Ok(skill) => out.push(skill),
                Err(e) => eprintln!("skip {}: {e}", path.display()),
            }
        }
    }
    Ok(out)
}

/// 按名查技能（形态变换 build_skills / 借用等按名构建场景）。
/// 相对 CWD 的 data/skills 惰性加载一次（与 VmEngine::new("data") 同约定）。
pub fn skill_by_name(name: &str) -> Option<SkillJson> {
    static CACHE: std::sync::OnceLock<std::collections::BTreeMap<String, SkillJson>> =
        std::sync::OnceLock::new();
    let cache = CACHE.get_or_init(|| {
        let mut m = std::collections::BTreeMap::new();
        if let Ok(list) = load_skill_dir(std::path::Path::new("data/skills")) {
            for s in list {
                m.insert(s.name.clone(), s);
            }
        }
        m
    });
    cache.get(name).cloned()
}

/// 按名批量构建 BattleSkill（py battle.build_skills）。
pub fn build_skills(names: &[String]) -> Vec<crate::battle_state::BattleSkill> {
    names
        .iter()
        .filter_map(|n| skill_by_name(n))
        .map(crate::battle_state::BattleSkill::from_base)
        .collect()
}
