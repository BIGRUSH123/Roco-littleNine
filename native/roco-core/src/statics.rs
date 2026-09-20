//! statics — 静态数据表（印记/异常模板 + 属性克制表）。
//!
//! 由 native/tools/dump_static_tables.py 从 Python oracle 导出，
//! 编译期经 include_str! 内嵌，加载语义与 Python 一致。

use crate::battle_state::{Effect, EffectKind, Mark};
use serde_json::Value as J;
use std::collections::BTreeMap;
use std::sync::OnceLock;

const TABLES_JSON: &str = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/../static_tables.json"));

#[derive(Debug, Clone)]
pub struct AbnormalTemplate {
    pub effect: Effect,
}

#[derive(Debug, Clone)]
pub struct Tables {
    pub marks: BTreeMap<String, Mark>,
    pub abnormals: BTreeMap<String, Effect>,
    pub type_chart: BTreeMap<String, BTreeMap<String, f64>>,
    pub positive_marks: Vec<String>,
    pub negative_marks: Vec<String>,
    pub devotion_types: Vec<String>,
    pub devotion_config: BTreeMap<String, J>,
}

fn load() -> &'static Tables {
    static TABLES: OnceLock<Tables> = OnceLock::new();
    TABLES.get_or_init(|| {
        let root: J = serde_json::from_str(TABLES_JSON).expect("static_tables.json 解析失败");

        let mut marks = BTreeMap::new();
        if let Some(obj) = root.get("mark_templates").and_then(|x| x.as_object()) {
            for (k, v) in obj {
                if let Ok(mark) = serde_json::from_value::<Mark>(v.clone()) {
                    marks.insert(k.clone(), mark);
                }
            }
        }

        let mut abnormals = BTreeMap::new();
        if let Some(obj) = root.get("abnormal_templates").and_then(|x| x.as_object()) {
            for (k, v) in obj {
                // dump 的是 AbnormalEffect dataclass 平铺字段 → 手工构造 Effect
                let g_i64 = |key: &str, d: i64| v.get(key).and_then(|x| x.as_i64()).unwrap_or(d);
                let g_f64 = |key: &str, d: f64| v.get(key).and_then(|x| x.as_f64()).unwrap_or(d);
                let g_bool = |key: &str, d: bool| v.get(key).and_then(|x| x.as_bool()).unwrap_or(d);
                let eff = Effect {
                    name: v.get("name").and_then(|x| x.as_str()).unwrap_or(k).to_string(),
                    source: v.get("source").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                    scope: v.get("scope").and_then(|x| x.as_str()).unwrap_or("battlefield").to_string(),
                    ttl: g_i64("ttl", 0),
                    cooldown: g_i64("cooldown", 0),
                    kind: EffectKind::Abnormal {
                        stacks: g_i64("stacks", 0),
                        tick_damage_pct: g_f64("tick_damage_pct", 0.0),
                        tick_element: v.get("tick_element").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                        decay_on_tick: g_bool("decay_on_tick", false),
                        max_stacks: g_i64("max_stacks", 0),
                        tick_per_stack: g_bool("tick_per_stack", true),
                    },
                };
                abnormals.insert(k.clone(), eff);
            }
        }

        let mut type_chart = BTreeMap::new();
        if let Some(obj) = root.get("type_chart").and_then(|x| x.as_object()) {
            for (atk, defs) in obj {
                let mut inner = BTreeMap::new();
                if let Some(do_) = defs.as_object() {
                    for (d, m) in do_ {
                        if let Some(mf) = m.as_f64() {
                            inner.insert(d.clone(), mf);
                        }
                    }
                }
                type_chart.insert(atk.clone(), inner);
            }
        }

        let positive_marks = root
            .get("positive_marks")
            .and_then(|x| x.as_array())
            .map(|a| a.iter().filter_map(|s| s.as_str().map(String::from)).collect())
            .unwrap_or_default();
        let negative_marks = root
            .get("negative_marks")
            .and_then(|x| x.as_array())
            .map(|a| a.iter().filter_map(|s| s.as_str().map(String::from)).collect())
            .unwrap_or_default();
        let devotion_types = root
            .get("devotion_types")
            .and_then(|x| x.as_array())
            .map(|a| a.iter().filter_map(|s| s.as_str().map(String::from)).collect())
            .unwrap_or_default();
        let mut devotion_config = BTreeMap::new();
        if let Some(obj) = root.get("devotion_config").and_then(|x| x.as_object()) {
            for (k, v) in obj {
                devotion_config.insert(k.clone(), v.clone());
            }
        }

        Tables { marks, abnormals, type_chart, positive_marks, negative_marks, devotion_types, devotion_config }
    })
}

/// 奉献类型配置（combo/energy_cost/power/life_drain/abnormal 参数）。
pub fn devotion_effect(name: &str) -> Option<&'static J> {
    load().devotion_config.get(name)
}

/// classify_mark：根据名称判断印记正负（默认 negative）。
pub fn classify_mark(name: &str) -> &'static str {
    let t = load();
    if t.positive_marks.iter().any(|s| s == name) {
        "positive"
    } else {
        "negative"
    }
}

pub fn devotion_types() -> &'static [String] {
    &load().devotion_types
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn poison_template_loads() {
        let t = abnormal_template("中毒");
        assert!(t.is_some(), "中毒模板未加载");
        let e = t.unwrap();
        match &e.kind {
            EffectKind::Abnormal { tick_damage_pct, .. } => {
                assert!((tick_damage_pct - 0.03).abs() < 1e-9, "pct={tick_damage_pct}");
            }
            _ => panic!("kind 错误"),
        }
    }

    #[test]
    fn type_chart_loads() {
        let mult = element_mult("火", &["草".to_string()]);
        assert!((mult - 2.0).abs() < 1e-9);
    }
}

pub fn mark_template(name: &str) -> Option<Mark> {
    load().marks.get(name).cloned()
}

/// 返回模板效果（stacks 已置 0，由调用方设置）。
pub fn abnormal_template(name: &str) -> Option<Effect> {
    load().abnormals.get(name).cloned()
}

/// 属性克制：攻击元素 × 防守方元素列表连乘。
pub fn element_mult(element: &str, def_attrs: &[String]) -> f64 {
    if element.is_empty() {
        return 1.0;
    }
    let tables = load();
    let chart = tables.type_chart.get(element);
    let mut mult = 1.0;
    if let Some(chart) = chart {
        for attr in def_attrs {
            mult *= chart.get(attr).copied().unwrap_or(1.0);
        }
    }
    mult
}
