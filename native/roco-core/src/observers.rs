//! observers — 观察者注册表与触发（移植自 backend/engine/observer.py）。
//!
//! owner 用 (team, sprite_index) 定位（Rust 状态层中精灵身份即槽位），
//! owner_skill 用 BattleSkill 的 skill_id。

use crate::vm_ctx::Ctx;
use serde_json::Value as J;

#[derive(Debug, Clone)]
pub struct Observer {
    /// 条件树（原始 JSON，经 cond::eval_one 求值）
    pub cond: J,
    /// 效果树（原始 JSON，经 vm_exec::process_effects 执行）
    pub then: Vec<J>,
    pub scope: String,
    pub name: String,
    pub source: String,
    /// 触发点列表；空 = 全部触发
    pub listen: Vec<String>,
    pub threshold: i64,
    pub reset_on_fire: bool,
    /// owner：(team_key, sprite_index)；None = 无主（全局）
    pub owner: Option<(&'static str, usize)>,
    pub owner_skill_id: Option<u64>,
    pub hit_count: i64,
    /// 注册时 source 已注入 then 的效果树（_bake_inject_source 语义）
    pub source_baked: bool,
}

impl Observer {
    pub fn eval_cond(&self, ctx: &Ctx) -> Result<bool, ()> {
        crate::cond::eval_one(ctx, &self.cond)
    }
}

/// 向 then 效果树注入 source（_bake_inject_source：仅缺 source 的 dict op）。
fn bake_inject_source(effects: &mut [J], source: &str) {
    for eff in effects.iter_mut() {
        let Some(obj) = eff.as_object_mut() else { continue };
        if obj.contains_key("op") && !obj.contains_key("source") {
            obj.insert("source".into(), J::String(source.to_string()));
        }
        if let Some(J::Array(then)) = obj.get_mut("then") {
            bake_inject_source(then, source);
        }
        if let Some(J::Array(else_)) = obj.get_mut("else") {
            bake_inject_source(else_, source);
        }
    }
}

/// 向 then 效果树注入 scope（_bake_inject_scope：仅缺 scope 的 dict op）。
fn bake_inject_scope(effects: &mut [J], scope: &str) {
    for eff in effects.iter_mut() {
        let Some(obj) = eff.as_object_mut() else { continue };
        if obj.contains_key("op") && !obj.contains_key("scope") {
            obj.insert("scope".into(), J::String(scope.to_string()));
        }
        if let Some(J::Array(then)) = obj.get_mut("then") {
            bake_inject_scope(then, scope);
        }
        if let Some(J::Array(else_)) = obj.get_mut("else") {
            bake_inject_scope(else_, scope);
        }
    }
}

const POST_EVENT_TRIGGERS: &[&str] = &[
    "post_skill", "post_damage", "post_ko", "post_switch", "post_entry",
    "post_leave", "post_enemy_leave", "post_counter", "post_abnormal_tick",
    "post_abnormal_change", "post_abnormal_apply", "post_energy_change",
    "post_heal", "post_positive_change", "turn_end",
];

/// 注册前烘焙 then：注入 source、post 事件注入 scope（仅缺失字段）。
/// register 与去重比较必须使用同一烘焙结果。
pub fn prepare_then(then: &[J], source: &str, scope: &str, listen: &[String]) -> Vec<J> {
    let mut out = then.to_vec();
    bake_inject_source(&mut out, source);
    let is_post = listen.iter().any(|t| POST_EVENT_TRIGGERS.contains(&t.as_str()));
    if is_post {
        bake_inject_scope(&mut out, scope);
    }
    out
}

#[derive(Debug, Clone, Default)]
pub struct ObserverRegistry {
    pub observers: Vec<Observer>,
}

impl ObserverRegistry {
    pub fn new() -> Self {
        ObserverRegistry { observers: Vec::new() }
    }

    pub fn has_candidates(&self, trigger: &str) -> bool {
        self.observers
            .iter()
            .any(|o| o.listen.is_empty() || o.listen.iter().any(|t| t == trigger))
    }

    /// py `has_candidates(trigger, owner_sprite_id)`（observer.py:363）：按精灵
    /// 判断是否有该触发的候选（自有 或 无主）。turn_end 循环里用它做
    /// continue 门——urgent pending escape 的结算也在门内（battle.py:1836-1843），
    /// 双方都无候选时 pending 不结算（spec_0164 sim19 第二次脱离的来源）。
    pub fn has_candidates_for(&self, trigger: &str, owner: (&'static str, usize)) -> bool {
        !self.candidates(trigger, Some(owner)).is_empty()
    }

    /// 注册观察者：注入 source、post 事件 bake scope、去重。
    pub fn register(&mut self, mut obs: Observer) {
        if !obs.source.is_empty() && !obs.then.is_empty() && !obs.source_baked {
            bake_inject_source(&mut obs.then, &obs.source);
            obs.source_baked = true;
        }
        // post 事件观察者：scope 烘焙进 then 效果树（缺失时）
        let is_post = obs.listen.iter().any(|t| POST_EVENT_TRIGGERS.contains(&t.as_str()));
        if is_post && !obs.then.is_empty() {
            bake_inject_scope(&mut obs.then, &obs.scope);
        }
        self.observers.push(obs);
    }

    /// 候选：listen 匹配 + owner 过滤（owner=None 时返回全部候选，
    /// 由调用方的过滤逻辑决定——与 Python 一致：owner 过滤在循环内）。
    pub fn candidates(&self, trigger: &str, owner: Option<(&'static str, usize)>) -> Vec<&Observer> {
        self.observers
            .iter()
            .filter(|o| o.listen.is_empty() || o.listen.iter().any(|t| t == trigger))
            .filter(|o| match (o.owner, owner) {
                (Some(own), Some(cur)) => own == cur,
                _ => true,
            })
            .collect()
    }

    /// owner 为精灵的观察者列表（trait unload 用）。
    pub fn unregister_by_owner(&mut self, owner: (&'static str, usize), reason: &str) {
        if std::env::var("ROCO_DEBUG_DMG").is_ok() {
            let doomed: Vec<String> = self
                .observers
                .iter()
                .filter(|o| {
                    o.owner == Some(owner)
                        && match reason {
                            "reload" => true,
                            _ => match o.scope.as_str() {
                                "turn" => reason == "turn_end",
                                "battlefield" => reason == "leave" || reason == "faint",
                                "persistent" => reason == "faint",
                                _ => false,
                            },
                        }
                })
                .map(|o| format!("{}(scope={})", o.source, o.scope))
                .collect();
            if std::env::var("ROCO_DEBUG_DMG").is_ok() {
                eprintln!(
                    "[rust unregister-call] owner={:?} reason={} doomed={}",
                    owner,
                    reason,
                    doomed.len()
                );
            }
            if !doomed.is_empty() {
                eprintln!(
                    "[rust unregister] owner={:?} reason={} removed={:?}",
                    owner, reason, doomed
                );
            }
        }
        // py Observer.should_clear：reload → 全清；battlefield → 任何移除都清；
        // persistent → 仅 faint；其余（turn/permanent）→ 永不清
        self.observers.retain(|o| {
            if o.owner == Some(owner) {
                let should_clear = match reason {
                    "reload" => true,
                    _ => match o.scope.as_str() {
                        "battlefield" => true,
                        "persistent" => reason == "faint",
                        _ => false,
                    },
                };
                !should_clear
            } else {
                true
            }
        });
    }
}
