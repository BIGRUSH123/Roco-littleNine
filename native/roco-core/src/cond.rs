//! cond — 条件 DSL 求值器（移植自 backend/vm/cond.py）。
//!
//! eval_one 返回 Result<bool, ()>：Err 对应 Python 异常（如 KeyError/
//! TypeError），调用方（观察者触发点）据此跳过该观察者——与返回 false
//! 语义不同（or 短路场景下 Python 的异常会向上传播）。

use crate::resolve::{resolve_dict_query, Val};
use crate::vm_ctx::{Ctx, StrSet};
use serde_json::Value as J;

const ATTACK_TYPES: &[&str] = &["物攻", "魔攻", "动态攻击"];

// ── compare_op（通用比较，含容器成员）──

pub fn compare_op(a: &Val, op: &str, b: &Val) -> Result<bool, ()> {
    match op {
        "lt" => py_cmp(a, b).map(|o| o == std::cmp::Ordering::Less),
        "le" | "lte" => py_cmp(a, b).map(|o| o != std::cmp::Ordering::Greater),
        "eq" => Ok(py_eq(a, b)),
        "ne" | "neq" => Ok(!py_eq(a, b)),
        "ge" | "gte" => py_cmp(a, b).map(|o| o != std::cmp::Ordering::Less),
        "gt" => py_cmp(a, b).map(|o| o == std::cmp::Ordering::Greater),
        "contains" => py_contains(a, b),
        "in" => py_contains(b, a),
        "not_in" => py_contains(b, a).map(|v| !v),
        _ => Ok(false),
    }
}

/// Python `==`：跨类型为 False（不报错）；bool 与数值按数值比较。
fn py_eq(a: &Val, b: &Val) -> bool {
    use Val::*;
    match (a, b) {
        (Null, Null) => true,
        (Null, _) | (_, Null) => false,
        (S(x), S(y)) => x == y,
        (S(_), _) | (_, S(_)) => false,
        (Set(x), Set(y)) => x == y,
        (Set(_), _) | (_, Set(_)) => false,
        (Map(_), _) | (_, Map(_)) => false,
        (L(_), _) | (_, L(_)) => false,
        _ => match (a.as_num(), b.as_num()) {
            (Some(x), Some(y)) => x == y,
            _ => false,
        },
    }
}

/// Python `<` 系比较：不兼容类型 → Err（TypeError）。
fn py_cmp(a: &Val, b: &Val) -> Result<std::cmp::Ordering, ()> {
    use Val::*;
    match (a, b) {
        (S(x), S(y)) => Ok(x.cmp(y)),
        (Null, _) | (_, Null) => Err(()), // None < x → TypeError
        (L(_), _) | (_, L(_)) => Err(()),
        (Set(_), _) | (_, Set(_)) => Err(()),
        (Map(_), _) | (_, Map(_)) => Err(()),
        _ => {
            let (x, y) = (a.as_num().ok_or(())?, b.as_num().ok_or(())?);
            x.partial_cmp(&y).ok_or(())
        }
    }
}

/// `b in a`（contains）：str 子串 / 容器成员 / 标量 → str(b) in str(a)。
fn py_contains(a: &Val, b: &Val) -> Result<bool, ()> {
    use Val::*;
    match a {
        S(s) => Ok(match b {
            S(bs) => s.contains(bs.as_str()),
            _ => false, // str.__contains__ 非字符串 → TypeError → 上层按 false 处理
        }),
        Set(xs) => Ok(match b {
            S(bs) => xs.contains(bs),
            _ => xs.iter().any(|x| py_eq(&S(x.clone()), b)),
        }),
        L(xs) => Ok(xs.iter().any(|x| py_eq(x, b))),
        Map(m) => match b {
            S(k) => Ok(m.contains_key(k)),
            _ => Ok(false),
        },
        _ => {
            // 标量：str(b) in str(a) —— 引擎数据不依赖此路径，保守 Err
            Err(())
        }
    }
}

// ── _SpriteView / _team_of ──

struct SpriteView<'a> {
    energy: i64,
    abnormal_stacks: &'a crate::vm_ctx::IntMap,
    stat_stages: &'a crate::vm_ctx::IntMap,
    hp_ratio: f64,
    prev_damage_taken: bool,
    skill_elements: &'a StrSet,
    just_entered: bool,
    just_acted: bool,
    is_charging: bool,
    charged: bool,
}

fn sprite_of<'a>(ctx: &'a Ctx, of: &str) -> SpriteView<'a> {
    if of == "sprite_self" {
        SpriteView {
            energy: ctx.energy_self,
            abnormal_stacks: &ctx.abnormal_stacks_self,
            stat_stages: &ctx.stat_stages_self,
            hp_ratio: ctx.hp_self_ratio,
            prev_damage_taken: ctx.prev_damage_taken_self,
            skill_elements: &ctx.skill_elements_self,
            just_entered: ctx.just_entered,
            just_acted: ctx.just_acted_self,
            is_charging: ctx.is_charging_self,
            charged: ctx.charged_self,
        }
    } else {
        SpriteView {
            energy: ctx.energy_opp,
            abnormal_stacks: &ctx.abnormal_stacks_opp,
            stat_stages: &ctx.stat_stages_opp,
            hp_ratio: ctx.hp_opp_ratio,
            prev_damage_taken: ctx.prev_damage_taken_opp,
            skill_elements: &ctx.skill_elements_opp,
            just_entered: false,
            just_acted: false,
            is_charging: false,
            charged: ctx.charged_opp,
        }
    }
}

// ── skill_use 过滤（_skill_use_matches）──

fn skill_use_matches(ctx: &Ctx, cond: &J) -> Result<bool, ()> {
    if let Some(el) = cond.get("element") {
        let element = if el.is_object() {
            crate::resolve::resolve(ctx, el).ok_or(())?
        } else {
            crate::resolve::scalar_of(el).ok_or(())?
        };
        match element {
            Val::S(s) => {
                if ctx.element_self != s {
                    return Ok(false);
                }
            }
            _ => return Err(()),
        }
    }
    if let Some(st) = cond.get("skill_type").and_then(|x| x.as_str()) {
        if ctx.skill_type_self != st {
            return Ok(false);
        }
    }
    if let Some(tag) = cond.get("tag").and_then(|x| x.as_str()) {
        if ctx.skill_tag_self != tag {
            return Ok(false);
        }
    }
    if let Some(ec) = cond.get("energy_cost") {
        let ec = ec.as_i64().ok_or(())?;
        if ctx.energy_cost_self != ec {
            return Ok(false);
        }
    }
    Ok(true)
}

// ── COND_EVAL ──

/// COND_EVAL 的全部键。裸字符串条件：未知 → False（不报错）；
/// dict 条件：未知 → KeyError（Err）。
fn is_known_cond_key(key: &str) -> bool {
    matches!(key,
        "counter_succeeded" | "self_was_countered" | "prev_counter_succeeded"
        | "charged" | "is_charging" | "burst" | "first_action" | "first_action_battle"
        | "on_ko" | "on_self_ko"
        | "on_damage_taken" | "damage_restraint" | "prev_damage_taken"
        | "opp_switched" | "self_switched" | "sprite_left"
        | "opp_is_attack" | "prev_skill_is"
        | "is_first" | "is_second"
        | "hp_below" | "energy_le" | "energy_eq" | "energy_depleted"
        | "weather_is"
        | "skill_at" | "skill_position_changed"
        | "skill_use" | "have_skill_of"
        | "sprite_entered" | "sprite_acted"
        | "on_abnormal_tick" | "on_abnormal_changed" | "on_abnormal_applied"
        | "on_skills_energy_changed" | "on_positive_changed" | "on_energy_changed" | "on_heal"
        | "turn_end" | "turn_start" | "always"
        | "compare" | "devotion_triggered" | "team_has_element"
        | "and" | "or" | "not" | "have" | "trait_path"
    )
}

/// eval_one：dict / 字符串条件求值。Err = Python 异常。
pub fn eval_one(ctx: &Ctx, cond: &J) -> Result<bool, ()> {
    match cond {
        J::String(s) => {
            if is_known_cond_key(s) {
                dispatch(ctx, s, &J::Null)
            } else {
                Ok(false) // 未知字符串条件 → False（Python eval_one 行为）
            }
        }
        J::Object(obj) => {
            let key = obj.get("cond").and_then(|x| x.as_str()).ok_or(())?;
            match key {
                "and" => {
                    let conds = obj.get("conditions").and_then(|x| x.as_array()).ok_or(())?;
                    for c in conds {
                        if !eval_one(ctx, c)? {
                            return Ok(false);
                        }
                    }
                    Ok(true)
                }
                "or" => {
                    let conds = obj.get("conditions").and_then(|x| x.as_array()).ok_or(())?;
                    for c in conds {
                        if eval_one(ctx, c)? {
                            return Ok(true);
                        }
                    }
                    Ok(false)
                }
                "not" => {
                    let inner = obj.get("condition").ok_or(())?;
                    Ok(!eval_one(ctx, inner)?)
                }
                other => dispatch(ctx, other, cond),
            }
        }
        _ => Err(()),
    }
}

fn get_str<'a>(cond: &'a J, key: &str) -> Option<&'a str> {
    cond.get(key).and_then(|x| x.as_str())
}

fn get_str_or<'a>(cond: &'a J, key: &str, default: &'a str) -> &'a str {
    get_str(cond, key).unwrap_or(default)
}

fn dispatch(ctx: &Ctx, key: &str, cond: &J) -> Result<bool, ()> {
    use Val::*;
    let ev = &ctx.event;
    let r = match key {
        // ── Counter / response ──
        "counter_succeeded" => ev.counter_succeeded,
        "self_was_countered" => ev.was_countered,
        "prev_counter_succeeded" => ev.prev_counter_succeeded,

        // ── Charge / action state ──
        "charged" => ctx.charged_self,
        "is_charging" => ctx.is_charging_self,
        "burst" | "first_action" => ctx.first_action_self,
        "first_action_battle" => ctx.first_action_battle_self,

        // ── KO ──
        "on_ko" => ev.target_fainted,
        "on_self_ko" => ev.self_koed,

        // ── Damage ──
        "on_damage_taken" => {
            if ctx.damage_taken_this_turn <= 0 {
                false
            } else if let Some(of) = get_str(cond, "of") {
                ev.damage_taken_of == of
            } else {
                true
            }
        }
        "damage_restraint" => ctx.element_advantage >= 2.0,
        "prev_damage_taken" => {
            sprite_of(ctx, get_str_or(cond, "of", "sprite_self")).prev_damage_taken
        }

        // ── Switch ──
        "opp_switched" => ev.opp_switched,
        "self_switched" => ev.self_switched,
        "sprite_left" => {
            let of = get_str_or(cond, "of", "sprite_self");
            (of == "sprite_self" && ev.self_switched)
                || (of == "sprite_opp" && ev.opp_switched)
        }

        // ── Skill type checks ──
        "opp_is_attack" => ATTACK_TYPES.contains(&ctx.skill_type_opp.as_str()),
        "prev_skill_is" => {
            if get_str(cond, "what") == Some("attack") {
                ATTACK_TYPES.contains(&ctx.prev_skill_type.as_str())
            } else {
                let st = get_str(cond, "skill_type").ok_or(())?;
                ctx.prev_skill_type == st
            }
        }

        // ── Turn order ──
        "is_first" => ctx.is_first,
        "is_second" => !ctx.is_first,

        // ── HP / Energy threshold ──
        "hp_below" => {
            let view = sprite_of(ctx, get_str_or(cond, "of", "sprite_self"));
            let ratio = cond.get("ratio").and_then(|x| x.as_f64()).ok_or(())?;
            view.hp_ratio < ratio
        }
        "energy_le" => {
            let view = sprite_of(ctx, get_str_or(cond, "of", "sprite_self"));
            let value = cond.get("value").and_then(|x| x.as_i64()).ok_or(())?;
            view.energy <= value
        }
        "energy_eq" => {
            let view = sprite_of(ctx, get_str_or(cond, "of", "sprite_self"));
            let value = cond.get("value").and_then(|x| x.as_i64()).ok_or(())?;
            view.energy == value
        }
        "energy_depleted" => {
            let view = sprite_of(ctx, get_str_or(cond, "of", "sprite_self"));
            view.energy == ctx.energy_cost_self
        }

        // ── Weather ──
        "weather_is" => {
            let w = cond.get("weather").ok_or(())?;
            let expected = if w.is_object() {
                crate::resolve::resolve(ctx, w).ok_or(())?
            } else {
                crate::resolve::scalar_of(w).ok_or(())?
            };
            let ws = match expected {
                S(s) => s,
                _ => return Err(()),
            };
            ctx.weather == ws
        }

        // ── Skill position ──
        "skill_at" => {
            let pos = cond.get("position").and_then(|x| x.as_i64()).ok_or(())?;
            ctx.skill_index == pos
        }
        "skill_position_changed" => ev.skill_position_changed,

        // ── Skill use（count only）──
        "skill_use" => skill_use_matches(ctx, cond)?,

        // ── Skill element possession ──
        "have_skill_of" => {
            let view = sprite_of(ctx, get_str_or(cond, "of", "sprite_self"));
            let el = cond.get("element").ok_or(())?;
            let element = if el.is_object() {
                crate::resolve::resolve(ctx, el).ok_or(())?
            } else {
                crate::resolve::scalar_of(el).ok_or(())?
            };
            let es = match element {
                S(s) => s,
                _ => return Err(()),
            };
            view.skill_elements.contains(&es)
        }

        // ── Entry / abnormal / state change events ──
        "sprite_entered" => sprite_of(ctx, get_str_or(cond, "of", "sprite_self")).just_entered,
        "sprite_acted" => sprite_of(ctx, get_str_or(cond, "of", "sprite_self")).just_acted,
        "on_abnormal_tick" => {
            let name = get_str(cond, "name").ok_or(())?;
            ev.last_tick_abnormal == name
                && ev.last_tick_target == get_str_or(cond, "of", "sprite_opp")
        }
        "on_abnormal_changed" => {
            let name = get_str(cond, "name").ok_or(())?;
            ev.abnormal_changed_name == name
                && ev.abnormal_changed_target == get_str_or(cond, "of", "sprite_opp")
        }
        "on_abnormal_applied" => {
            let name = get_str(cond, "name").ok_or(())?;
            ev.abnormal_applied_name == name
                && ev.abnormal_applied_target == get_str_or(cond, "of", "sprite_opp")
        }
        "on_skills_energy_changed" => {
            ev.skills_energy_changed_of == get_str_or(cond, "of", "sprite_self")
        }
        "on_positive_changed" => {
            ev.positive_changed_of == get_str_or(cond, "of", "sprite_opp")
        }
        "on_energy_changed" => ev.energy_changed_of == get_str_or(cond, "of", "sprite_self"),
        "on_heal" => {
            ev.heal_of == get_str_or(cond, "of", "sprite_self") && ctx.heal_delta_self > 0
        }

        // ── Turn boundaries ──
        "turn_end" => ev.turn_end,
        "turn_start" | "always" => true,

        // ── Generic comparison ──
        "compare" => {
            let lhs = resolve_dict_query(ctx, cond)?;
            let op = get_str(cond, "op").ok_or(())?;
            let rhs_v = cond.get("value").ok_or(())?;
            let rhs = crate::resolve::resolve(ctx, rhs_v).ok_or(())?;
            compare_op(&lhs, op, &rhs)?
        }

        // ── Devotion ──
        "devotion_triggered" => ev.devotion_triggered,

        // ── Team composition ──
        "team_has_element" => {
            let el = cond.get("element").ok_or(())?;
            let element = if el.is_object() {
                crate::resolve::resolve(ctx, el).ok_or(())?
            } else {
                crate::resolve::scalar_of(el).ok_or(())?
            };
            let es = match element {
                S(s) => s,
                _ => return Err(()),
            };
            ctx.team_elements_own.contains(&es)
        }

        // ── have 子分发 ──
        "have" => {
            let what = get_str(cond, "what").ok_or(())?;
            match what {
                "abnormal" => {
                    let view = sprite_of(ctx, get_str_or(cond, "of", "sprite_self"));
                    let name = get_str(cond, "name").unwrap_or("");
                    view.abnormal_stacks.get(name).copied().unwrap_or(0) > 0
                }
                "mark" => {
                    let team = get_str_or(cond, "of", "team_own");
                    let stacks = if team == "team_own" { &ctx.mark_stacks_own } else { &ctx.mark_stacks_opp };
                    let name = get_str(cond, "name").unwrap_or("");
                    stacks.get(name).copied().unwrap_or(0) > 0
                }
                "stat_positive" => {
                    let view = sprite_of(ctx, get_str_or(cond, "of", "sprite_self"));
                    let stat = get_str(cond, "stat").unwrap_or("");
                    view.stat_stages.get(stat).copied().unwrap_or(0) > 0
                }
                "stat_negative" => {
                    let view = sprite_of(ctx, get_str_or(cond, "of", "sprite_self"));
                    let stat = get_str(cond, "stat").unwrap_or("");
                    view.stat_stages.get(stat).copied().unwrap_or(0) < 0
                }
                "any_stat_positive" => {
                    let view = sprite_of(ctx, get_str_or(cond, "of", "sprite_self"));
                    view.stat_stages.values().any(|v| *v > 0)
                }
                "any_stat_negative" => {
                    let view = sprite_of(ctx, get_str_or(cond, "of", "sprite_self"));
                    view.stat_stages.values().any(|v| *v < 0)
                }
                "counter" => {
                    let name = get_str(cond, "name").unwrap_or("");
                    ctx.counter_values.get(name).copied().unwrap_or(0) > 0
                }
                _ => return Err(()), // HAVE_EVAL KeyError
            }
        }

        // ── Trait path bridge ──
        "trait_path" => {
            let path = get_str(cond, "path").unwrap_or("");
            let op = get_str(cond, "op").unwrap_or("eq");
            let expected = cond.get("value").cloned().unwrap_or(J::Null);
            let actual = resolve_trait_path_value(ctx, path)?;
            let expected_v = crate::resolve::resolve(ctx, &expected)
                .or_else(|| crate::resolve::scalar_of(&expected))
                .ok_or(())?;
            compare_op(&actual, op, &expected_v)?
        }

        _ => return Err(()), // Unknown condition → KeyError → 跳过观察者
    };
    Ok(r)
}

// ── Trait path bridge（cond.py 版 _TRAIT_PATH_MAP，注意与 formula.rs 的不同）──

fn trait_path_field(path: &str) -> Option<&'static str> {
    Some(match path {
        "self.energy" | "self.energy_self" => "energy_self",
        "self.hp" => "hp_self",
        "self.hp_ratio" | "self.hp_self_ratio" => "hp_self_ratio",
        "self.max_hp" => "hp_self_max",
        "self.is_charging" | "self._charging" => "is_charging_self",
        "self.first_action" | "first_action" => "first_action_self",
        "self.first_action_battle" | "first_action_battle" => "first_action_battle_self",
        "self.charged" => "charged_self",
        "self.positive_count" => "positive_count_self",
        "self.abnormal_count" => "abnormal_count_self",
        "self.fainted" => "self_koed",
        "self.just_entered" => "just_entered",
        "self.damage_reduction" => "damage_reduction_self",
        "target.energy" => "energy_opp",
        "target.hp" => "hp_opp",
        "target.hp_ratio" => "hp_opp_ratio",
        "target.max_hp" => "hp_opp_max",
        "target.positive_count" => "positive_count_opp",
        "target.abnormal_count" => "abnormal_count_opp",
        "target.fainted" => "self_koed",
        "skill.power" => "power_self",
        "skill.skill_type" => "skill_type_self",
        "skill.element" => "element_self",
        "skill.energy_cost" => "energy_cost_self",
        "skill.combo" => "combo_self",
        "opponent_skill.power" => "power_opp",
        "use.combo" => "combo_self",
        "use.is_first" => "is_first",
        "battle.globals.weather" => "weather",
        "player_fainted_count" => "fainted_own",
        "opponent_fainted_count" => "fainted_opp",
        "effect_name" | "effect.name" => "abnormal_applied_name",
        _ => return None,
    })
}

/// cond.py 的 _resolve_trait_path_value。
fn resolve_trait_path_value(ctx: &Ctx, path: &str) -> Result<Val, ()> {
    use Val::*;
    // 直接字段映射
    if let Some(field) = trait_path_field(path) {
        return Ok(crate::resolve::ctx_field_by_name(ctx, field).unwrap_or(I(0)));
    }

    // 计算路径
    match path {
        "skill.is_attack" => return Ok(B(ATTACK_TYPES.contains(&ctx.skill_type_self.as_str()))),
        "skill.is_defense" => return Ok(B(ctx.skill_type_self == "防御")),
        "skill.is_status" => return Ok(B(matches!(ctx.skill_type_self.as_str(), "状态" | "变化"))),
        "target.is_fainted" => return Ok(B(ctx.event.target_fainted)),
        "is_faint" => return Ok(B(ctx.event.self_koed)),
        "self.energy_cost_total" => return Ok(I(ctx.skills_energy_sum_self)),
        "target_bloodline" => return Ok(S(ctx.bloodline_opp.clone())),
        "skill" => return Ok(S(ctx.skill_name_self.clone())),
        "type_mult" => return Ok(F(1.0)), // getattr(ctx, "type_mult", 1.0)，Ctx 无此字段
        "opponent.lives" => return Ok(I(ctx.lives_opp)),
        "self._migration_cycle" => {
            return Ok(I(ctx.counter_values.get("_migration_cycle").copied().unwrap_or(0)))
        }
        "self._burst_extended_once" => {
            return Ok(I(ctx.counter_values.get("_burst_extended_once").copied().unwrap_or(0)))
        }
        "team_elements" => {
            return Ok(L(ctx.team_elements_own.iter().map(|s| S(s.clone())).collect()))
        }
        "effect.is_stat" => return Ok(B(false)), // getattr(ctx, "effect_is_stat", False)
        _ => {}
    }

    // (self|target).effects[name=X].prop
    if let Some(rest) = path.strip_prefix("self.").or_else(|| path.strip_prefix("target.")) {
        let is_self = path.starts_with("self.");
        if let Some(rest) = rest.strip_prefix("effects[name=") {
            if let Some((name, prop)) = rest.split_once(']') {
                let prop = prop.strip_prefix('.').unwrap_or("");
                let stacks = if is_self { &ctx.abnormal_stacks_self } else { &ctx.abnormal_stacks_opp };
                let val = stacks.get(name).copied().unwrap_or(0);
                return Ok(match prop {
                    "exists" => B(val > 0),
                    "stacks" => I(val),
                    _ => I(0),
                });
            }
        }
        if let Some(rest) = rest.strip_prefix("counters[") {
            if let Some(key) = rest.strip_suffix(']') {
                return Ok(I(ctx.counter_values.get(key).copied().unwrap_or(0)));
            }
        }
        if let Some(rest) = rest.strip_prefix("skills[") {
            // skills[filter].prop：仅 element 过滤有意义
            if let Some((filter_str, prop)) = rest.split_once(']') {
                let prop = prop.strip_prefix('.').unwrap_or("");
                let elements = if is_self { &ctx.skill_elements_self } else { &ctx.skill_elements_opp };
                for part in filter_str.split(',') {
                    if let Some((k, v)) = part.split_once('=') {
                        if k.trim() == "element" {
                            let count = if elements.contains(v.trim()) { 1 } else { 0 };
                            return Ok(I(if prop == "count" { count } else { 0 }));
                        }
                    }
                }
                return Ok(I(0));
            }
        }
    }

    // (player.|opponent.)?team_counters[key]
    {
        let probe = path.strip_prefix("player.").unwrap_or(path);
        let probe = probe.strip_prefix("opponent.").unwrap_or(probe);
        if let Some(rest) = probe.strip_prefix("team_counters[") {
            if let Some(key) = rest.strip_suffix(']') {
                if path.starts_with("opponent.") {
                    return Ok(I(ctx.team_counters_opp.get(key).copied().unwrap_or(0)));
                }
                return Ok(I(ctx.team_counters_own.get(key).copied().unwrap_or(0)));
            }
        }
    }

    // 回退：getattr(ctx, path, None) → getattr(ctx.event, path, None)
    Ok(crate::resolve::ctx_field_by_name(ctx, path).unwrap_or(Val::Null))
}

// ── Condition → Trigger point inference ──

/// CONDITION_TRIGGERS：条件类型 → 触发点集合。
pub fn condition_triggers(cond_type: &str) -> Option<&'static [&'static str]> {
    Some(match cond_type {
        "skill_use" => &["post_skill"],
        "counter_succeeded" => &["post_counter"],
        "self_was_countered" => &["post_skill"],
        "prev_counter_succeeded" => &["post_counter"],
        "charged" | "is_charging" => &["post_skill", "turn_end"],
        "burst" | "first_action" | "first_action_battle" => &["post_skill"],
        "opp_is_attack" => &["post_skill"],
        "prev_skill_is" => &["post_skill"],
        "is_first" | "is_second" => &["post_skill"],
        "skill_at" | "skill_position_changed" => &["post_skill"],
        "energy_depleted" => &["post_skill", "post_energy_change"],
        "on_ko" | "on_self_ko" => &["post_ko"],
        "on_damage_taken" | "damage_restraint" | "prev_damage_taken" => &["post_damage"],
        "hp_below" => &["post_damage", "post_entry", "post_heal"],
        "energy_le" => &["post_skill", "post_energy_change", "post_entry"],
        "energy_eq" => &["post_skill", "post_energy_change"],
        "weather_is" => &["post_skill", "post_entry", "turn_end"],
        "opp_switched" | "self_switched" => &["post_switch"],
        "sprite_entered" => &["post_entry"],
        "sprite_acted" => &["post_skill"],
        "have_skill_of" => &["post_entry", "post_skill"],
        "on_abnormal_tick" => &["post_abnormal_tick"],
        "on_abnormal_changed" => &["post_abnormal_change"],
        "on_abnormal_applied" => &["post_abnormal_apply"],
        "on_skills_energy_changed" => &["post_energy_change"],
        "on_positive_changed" => &["post_positive_change"],
        "on_energy_changed" => &["post_energy_change"],
        "on_heal" => &["post_heal"],
        "turn_end" => &["turn_end"],
        "turn_start" => &["turn_start"],
        "always" => &["turn_start", "post_entry"],
        "devotion_triggered" => &["post_skill"],
        "have" => &["post_skill", "post_entry", "post_abnormal_change", "post_positive_change"],
        _ => return None,
    })
}

/// infer_triggers：遍历条件树收集触发点（未知类型 → 空集 = 全触发回退）。
pub fn infer_triggers(cond: &J) -> StrSet {
    let mut out = StrSet::new();
    infer_walk(cond, &mut out);
    out
}

fn infer_walk(cond: &J, out: &mut StrSet) {
    match cond {
        J::Object(obj) => {
            let key = obj.get("cond").and_then(|x| x.as_str()).unwrap_or("");
            match key {
                "and" | "or" => {
                    if let Some(conds) = obj.get("conditions").and_then(|x| x.as_array()) {
                        for c in conds {
                            infer_walk(c, out);
                        }
                    }
                }
                "not" => {
                    if let Some(c) = obj.get("condition") {
                        infer_walk(c, out);
                    }
                }
                other => {
                    if let Some(trigs) = condition_triggers(other) {
                        out.extend(trigs.iter().map(|s| s.to_string()));
                    }
                }
            }
        }
        J::String(s) => {
            if let Some(trigs) = condition_triggers(s) {
                out.extend(trigs.iter().map(|t| t.to_string()));
            }
        }
        _ => {}
    }
}
