//! vm_exec — 效果解释器（移植自 backend/vm/executor.py + compiler/passes/
//! skill_parse.py 默认值 + backend/vm/ops/* 全部处理器）。
//!
//! Rust 直接解释原始 JSON 效果树；解析失败的效果静默丢弃——对应 Python
//! 编译期 SkillParsePass.process 的逐条 try/except（主技能路径用预编译
//! 效果，坏效果在加载期即被丢弃）。
//!
//! 语义规则：一律走「编译后 typed 路径」——op_xxx 内部的 dict 分支默认值
//! 是死代码（如 tick/interrupt/lock 的 sprite_opp、weather 的 turns=5）。

use crate::cond::eval_one;
use crate::damage::calc_damage;
use crate::journal::Mutation;
use crate::resolve::{resolve, Val};
use crate::vm_ctx::Ctx;
use serde_json::Value as J;

// ── 排序（vm/sort.py）──

const PHASES: [(&str, i32); 6] = [
    ("cost", 0),
    ("power", 1),
    ("mult", 2),
    ("result", 3),
    ("counter", 4),
    ("turn_end", 5),
];
const DEFAULT_PHASE: i32 = 3;

fn phase_of(effect: &J) -> i32 {
    let feeds = effect.get("feeds").and_then(|x| x.as_str()).unwrap_or("");
    if !feeds.is_empty() {
        if let Some((_, p)) = PHASES.iter().find(|(k, _)| *k == feeds) {
            return *p;
        }
    }
    let needs = effect.get("needs").and_then(|x| x.as_str()).unwrap_or("");
    if !needs.is_empty() {
        if let Some((_, p)) = PHASES.iter().find(|(k, _)| *k == needs) {
            return *p;
        }
    }
    DEFAULT_PHASE
}

fn priority_of(effect: &J) -> i64 {
    match effect.get("priority") {
        Some(J::Number(n)) => n.as_i64().unwrap_or(0),
        _ => 0,
    }
}

/// 相位升序 → priority 降序 → 原序稳定。返回排序后的索引序列。
pub fn sort_effects(effects: &[J]) -> Vec<usize> {
    let mut tagged: Vec<(usize, i32, i64)> = (0..effects.len())
        .map(|i| (i, phase_of(&effects[i]), -priority_of(&effects[i])))
        .collect();
    tagged.sort_by(|a, b| a.1.cmp(&b.1).then(a.2.cmp(&b.2)).then(a.0.cmp(&b.0)));
    tagged.into_iter().map(|(i, _, _)| i).collect()
}

// ── 数值提取（Python int()/float() 语义）──

fn val_i(v: Val) -> Option<i64> {
    match v {
        Val::I(i) => Some(i),
        Val::F(f) => Some(f.trunc() as i64),
        Val::B(b) => Some(if b { 1 } else { 0 }),
        _ => None,
    }
}

fn val_f(v: Val) -> Option<f64> {
    match v {
        Val::I(i) => Some(i as f64),
        Val::F(f) => Some(f),
        Val::B(b) => Some(if b { 1.0 } else { 0.0 }),
        _ => None,
    }
}

fn get_i(effect: &J, key: &str, default: i64) -> Option<i64> {
    match effect.get(key) {
        None | Some(J::Null) => Some(default),
        Some(v) => val_i(crate::resolve::scalar_of(v)?),
    }
}

fn get_f(effect: &J, key: &str, default: f64) -> Option<f64> {
    match effect.get(key) {
        None | Some(J::Null) => Some(default),
        Some(v) => val_f(crate::resolve::scalar_of(v)?),
    }
}

fn get_bool(effect: &J, key: &str, default: bool) -> Option<bool> {
    match effect.get(key) {
        None | Some(J::Null) => Some(default),
        Some(J::Bool(b)) => Some(*b),
        Some(v) => val_i(crate::resolve::scalar_of(v)?).map(|i| i != 0),
    }
}

fn get_str<'a>(effect: &'a J, key: &str, default: &'a str) -> Option<&'a str> {
    match effect.get(key) {
        None | Some(J::Null) => Some(default),
        Some(J::String(s)) => Some(s.as_str()),
        Some(_) => None,
    }
}

fn get_str_opt(effect: &J, key: &str) -> Option<Option<String>> {
    Some(match effect.get(key) {
        None | Some(J::Null) => None,
        Some(J::String(s)) => Some(s.clone()),
        Some(_) => return None,
    })
}

fn per_hit_repeat(mut ms: Vec<Mutation>, per_hit: bool, combo: i64) -> Vec<Mutation> {
    let combo = combo.max(1);
    if per_hit && combo > 1 && !ms.is_empty() {
        let base = ms.clone();
        for _ in 1..combo {
            ms.extend(base.clone());
        }
    }
    ms
}

/// 直改效果 dict（trait JSON 非 observer 部分）→ ModifierInjection。
/// 支持 power_mod / mult_mod / flag_set / stat_stage 的常用键。
pub fn parse_direct_mod(d: &J, default_source: &str) -> Option<Mutation> {
    let obj = d.as_object()?;
    let op = obj.get("op").and_then(|x| x.as_str())?;
    let target = obj
        .get("target")
        .and_then(|x| x.as_str())
        .unwrap_or("sprite_self")
        .to_string();
    let scope = obj
        .get("scope")
        .and_then(|x| x.as_str())
        .unwrap_or("battlefield")
        .to_string();
    let source = obj
        .get("source")
        .and_then(|x| x.as_str())
        .map(String::from)
        .unwrap_or_else(|| default_source.to_string());
    let num = |k: &str| -> Option<f64> {
        obj.get(k).and_then(|x| x.as_f64())
    };
    let (stat, value, mode) = match op {
        "power_mod" => (
            obj.get("attr").and_then(|x| x.as_str())?.to_string(),
            num("delta").or_else(|| num("value")).unwrap_or(0.0),
            obj.get("mode").and_then(|x| x.as_str()).unwrap_or("add").to_string(),
        ),
        "mult_mod" => (
            obj.get("attr").and_then(|x| x.as_str())?.to_string(),
            num("value").unwrap_or(1.0),
            obj.get("mode").and_then(|x| x.as_str()).unwrap_or("set").to_string(),
        ),
        "flag_set" => (
            obj.get("flag").and_then(|x| x.as_str())?.to_string(),
            num("value").unwrap_or(1.0),
            "set".to_string(),
        ),
        "stat_stage" => (
            obj.get("stat").and_then(|x| x.as_str())?.to_string(),
            num("steps").unwrap_or(0.0),
            "add".to_string(),
        ),
        _ => return None,
    };
    Some(Mutation::ModifierInjection {
        target,
        stat,
        value: crate::resolve::Val::F(value),
        scope,
        mode,
        name: obj.get("name").and_then(|x| x.as_str()).map(String::from),
        source: Some(source),
        element: obj.get("element").and_then(|x| x.as_str()).map(String::from),
        per_element: obj.get("per_element").and_then(|x| x.as_i64()),
        on_next: obj.get("on_next").and_then(|x| x.as_bool()).unwrap_or(false),
        if_type: obj.get("if_type").and_then(|x| x.as_str()).map(String::from),
        skill_filter: obj.get("skill_filter").and_then(|x| x.as_str()).map(String::from),
        skill_where: obj.get("skill_where").cloned(),
        ttl: obj.get("ttl").and_then(|x| x.as_i64()).unwrap_or(0),
        then: None,
    })
}

// ── 主入口 ──

/// execute：排序 + 顺序执行（executor.execute，sort=True）。
pub fn execute(ctx: &Ctx, effects: &[J]) -> Vec<Mutation> {
    execute_with_skill_header(ctx, effects, None)
}

/// InjectHitPass：按技能头部补隐含 HitOp（攻击类型 + power>0 + 无顶层 hit）。
/// py 的隐式 hit 在【编译期】并入 vm_effects——burst 登记/skill_history
/// 存的都是带 hit 的列表；rust 主执行与登记必须共用此函数。
pub fn augment_with_header(effects: &[J], skill: Option<&J>) -> Vec<J> {
    let Some(sk) = skill else { return effects.to_vec() };
    let skill_type = sk.get("skill_type").and_then(|x| x.as_str()).unwrap_or("");
    let power = sk.get("power").and_then(|x| x.as_i64()).unwrap_or(0);
    let top_hit = effects
        .iter()
        .any(|e| e.get("op").and_then(|x| x.as_str()) == Some("hit"));
    if matches!(skill_type, "物攻" | "魔攻" | "动态攻击") && power > 0 && !top_hit {
        let combo = sk.get("combo").and_then(|x| x.as_i64()).unwrap_or(1);
        let mut v = effects.to_vec();
        let mut hit = serde_json::Map::new();
        hit.insert("op".into(), J::String("hit".into()));
        hit.insert("power".into(), J::from(power));
        hit.insert("type".into(), J::String(skill_type.to_string()));
        if let Some(el) = sk.get("element") {
            hit.insert("element".into(), el.clone());
        }
        hit.insert("combo".into(), J::from(combo));
        v.push(J::Object(hit));
        v
    } else {
        effects.to_vec()
    }
}

/// execute_with_skill_header：镜像 InjectHitPass —— 攻击类型 + power>0 +
/// 无顶层 hit → 末尾追加隐含 HitOp（skill JSON 为头部上下文）。
pub fn execute_with_skill_header(ctx: &Ctx, effects: &[J], skill: Option<&J>) -> Vec<Mutation> {
    let owned = augment_with_header(effects, skill);
    let effects: &[J] = &owned;
    let mut journal = Vec::new();
    for i in sort_effects(effects) {
        if let Some(ms) = process_one(ctx, &effects[i]) {
            journal.extend(ms);
        }
    }
    journal
}

/// 不排序直接执行（executor.process_effects）。
pub fn process_effects(ctx: &Ctx, effects: &[J]) -> Vec<Mutation> {
    let mut journal = Vec::new();
    for e in effects {
        if let Some(ms) = process_one(ctx, e) {
            journal.extend(ms);
        }
    }
    journal
}

/// process_one：单效果。None = 解析失败/静默丢弃。
pub fn process_one(ctx: &Ctx, effect: &J) -> Option<Vec<Mutation>> {
    effect.as_object()?;
    if effect.get("when").is_some() && effect.get("op").is_none() {
        let cond = effect.get("when")?;
        if cond.is_object() && cond.get("cond").is_none() {
            return None; // legacy 死触发格式
        }
        return process_when_block(ctx, effect);
    }
    // 兼容缺 op 的 hit（如 指指点点：有 skill_type+power 无 op）
    if effect.get("op").is_none() {
        if effect.get("skill_type").is_some() && effect.get("power").is_some() {
            let mut e2 = effect.clone();
            e2["op"] = J::String("hit".into());
            let ty = effect.get("skill_type").and_then(|x| x.as_str()).unwrap_or("物攻");
            e2["type"] = J::String(ty.into());
            return op_hit(ctx, &e2);
        }
        return None; // 无 op/when → 解析失败丢弃
    }
    let op = get_str(effect, "op", "")?;
    match op {
        "when" | "branch" => process_when_block(ctx, effect),
        "mod" => op_mod(ctx, effect),
        "stat_stage" => op_stat_stage(ctx, effect),
        "power_mod" => op_power_mod(ctx, effect),
        "mult_mod" => op_mult_mod(ctx, effect),
        "flag_set" => op_flag_set(ctx, effect),
        "heal" => op_heal(ctx, effect),
        "energize" => op_energize(ctx, effect),
        "revive" => op_revive(ctx, effect),
        "hit" => op_hit(ctx, effect),
        "mark" => op_mark(ctx, effect),
        "abnormal" => op_abnormal(ctx, effect),
        "weather" => op_weather(ctx, effect),
        "dispel" => op_dispel(ctx, effect),
        "steal" => op_steal(ctx, effect),
        "tick" => op_tick(ctx, effect),
        "double" => op_double(ctx, effect),
        "effect_delta" => op_effect_delta(ctx, effect),
        "charge" => op_charge(ctx, effect),
        "escape" => op_escape(ctx, effect),
        "return" => op_return(ctx, effect),
        "lock" => op_lock(ctx, effect),
        "interrupt" => op_interrupt(ctx, effect),
        "exchange" => op_exchange(ctx, effect),
        "reset" => op_reset(ctx, effect),
        "redirect" => op_redirect(ctx, effect),
        "replay" => op_replay(ctx, effect),
        "borrow" => op_borrow(ctx, effect),
        "count" => op_count(ctx, effect),
        "observer" => op_observer(ctx, effect),
        "team_counter_write" => op_team_counter_write(ctx, effect),
        "lives" | "lives_change" => op_lives_change(ctx, effect),
        "defer" | "schedule" => op_schedule(ctx, effect),
        "inherit" => op_inherit_effects(ctx, effect),
        "transform" => op_transform(ctx, effect),
        "trait_interaction" => op_trait_interaction(ctx, effect),
        "gain_skills" => op_gain_skills(ctx, effect),
        "burst_grant" => op_burst_grant(ctx, effect),
        _ => None,
    }
}

/// WhenBlock 递归。
fn process_when_block(ctx: &Ctx, effect: &J) -> Option<Vec<Mutation>> {
    let cond = effect.get("when").or_else(|| effect.get("cond"))?;
    match eval_one(ctx, cond) {
        Ok(true) => process_list(ctx, effect.get("then")),
        Ok(false) => {
            if let Some(branches) = effect
                .get("elif")
                .or_else(|| effect.get("else_if"))
                .and_then(|x| x.as_array())
            {
                for branch in branches {
                    let bcond = branch.get("cond").or_else(|| branch.get("when"));
                    let hit = match bcond {
                        Some(c) => eval_one(ctx, c).ok()?,
                        None => false,
                    };
                    if hit {
                        return process_list(ctx, branch.get("then"));
                    }
                }
            }
            process_list(ctx, effect.get("else"))
        }
        Err(()) => None,
    }
}

fn process_list(ctx: &Ctx, effects: Option<&J>) -> Option<Vec<Mutation>> {
    let arr = effects.and_then(|x| x.as_array())?;
    let mut out = Vec::new();
    for e in arr {
        out.extend(process_one(ctx, e)?);
    }
    Some(out)
}

// ── 处理器 ──

/// RISC stat_stage → StatChange（仅 source 元数据）。
fn op_stat_stage(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let mut stat = get_str(e, "stat", "")?.to_string();
    if stat.starts_with('=') {
        // Python: stat = str(resolve(ctx, stat))
        let r = resolve(ctx, &J::String(stat.clone()))?;
        stat = match &r {
            Val::S(s) => s.clone(),
            Val::I(i) => i.to_string(),
            Val::F(f) => py_str_f64(*f),
            _ => return None,
        };
    }
    let steps;
    if let Some(v) = e.get("value").filter(|v| !v.is_null()) {
        steps = val_i(resolve(ctx, v)?)?;
    } else {
        // parse 只认 "steps" 字段（美拉德案例：delta 被忽略 → steps=0）
        match e.get("steps") {
            None => steps = 0,
            Some(raw)
                if raw.is_object() && raw.get("q").is_some()
                    || (raw.is_string() && raw.as_str().unwrap_or("").starts_with('=')) =>
            {
                steps = val_i(resolve(ctx, raw)?)?;
            }
            Some(_) => steps = get_i(e, "steps", 0)?,
        }
    }
    let per_hit = get_bool(e, "per_hit", false)?;
    let ms = vec![Mutation::StatChange {
        target: get_str(e, "target", "sprite_self")?.to_string(),
        stat,
        steps,
        scope: get_str(e, "scope", "battlefield")?.to_string(),
        source: get_str_opt(e, "source")?,
        element: None,
        per_element: None,
        on_next: false,
        if_type: None,
        skill_filter: None,
        skill_where: None,
    }];
    Some(per_hit_repeat(ms, per_hit, ctx.combo_self))
}

/// RISC power_mod → ModifierInjection。
fn op_power_mod(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let per_hit = get_bool(e, "per_hit", false)?;
    let value = match e.get("value").filter(|v| !v.is_null()) {
        Some(v) => resolve(ctx, v)?,
        None => match e.get("delta").filter(|v| !v.is_null()) {
            Some(d) => resolve(ctx, d)?,
            None => Val::I(0),
        },
    };
    let ms = vec![Mutation::ModifierInjection {
        target: get_str(e, "target", "sprite_self")?.to_string(),
        stat: get_str(e, "attr", "")?.to_string(),
        value: Val::F(val_f(value)?),
        scope: get_str(e, "scope", "battlefield")?.to_string(),
        mode: get_str(e, "mode", "add")?.to_string(),
        name: get_str_opt(e, "name")?,
        source: get_str_opt(e, "source")?,
        element: get_str_opt(e, "element")?,
        per_element: None, // Python parse 无 per_element 字段 → 恒 None
        on_next: false,
        if_type: None, // Python parse 无 if_type 字段 → 恒 None
        skill_filter: get_str_opt(e, "skill_filter")?,
        skill_where: e.get("skill_where").filter(|v| !v.is_null()).cloned(),
        ttl: get_i(e, "ttl", 0)?,
        then: None,
    }];
    Some(per_hit_repeat(ms, per_hit, ctx.combo_self))
}

/// RISC mult_mod → ModifierInjection。
fn op_mult_mod(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let per_hit = get_bool(e, "per_hit", false)?;
    let value = match e.get("value").filter(|v| !v.is_null()) {
        Some(v) => resolve(ctx, v)?,
        None => Val::F(1.0),
    };
    let ms = vec![Mutation::ModifierInjection {
        target: get_str(e, "target", "sprite_self")?.to_string(),
        stat: get_str(e, "attr", "")?.to_string(),
        value: Val::F(val_f(value)?),
        scope: get_str(e, "scope", "battlefield")?.to_string(),
        mode: get_str(e, "mode", "set")?.to_string(),
        name: get_str_opt(e, "name")?,
        source: match e.get("source").filter(|v| !v.is_null()) {
            Some(s) => Some(s.as_str()?.to_string()),
            None => get_str_opt(e, "name")?,
        },
        element: get_str_opt(e, "element")?,
        per_element: None,
        on_next: get_bool(e, "on_next", false)?,
        if_type: get_str_opt(e, "if_type")?,
        skill_filter: get_str_opt(e, "skill_filter")?,
        skill_where: e.get("skill_where").filter(|v| !v.is_null()).cloned(),
        ttl: get_i(e, "ttl", 0)?,
        then: None,
    }];
    Some(per_hit_repeat(ms, per_hit, ctx.combo_self))
}

/// RISC flag_set → ModifierInjection。
fn op_flag_set(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let value = match e.get("value").filter(|v| !v.is_null()) {
        Some(v) => resolve(ctx, v)?,
        None => Val::B(true),
    };
    let value_out = match &value {
        // Python flag_set 不做 float 转换——bool/int/float 原样保留
        Val::I(_) | Val::F(_) | Val::B(_) => value.clone(),
        other => Val::F(val_f(other.clone())?),
    };
    Some(vec![Mutation::ModifierInjection {
        target: get_str(e, "target", "sprite_self")?.to_string(),
        stat: get_str(e, "flag", "")?.to_string(),
        value: value_out,
        scope: get_str(e, "scope", "battlefield")?.to_string(),
        mode: "set".into(),
        name: get_str_opt(e, "name")?,
        source: get_str_opt(e, "source")?,
        element: None,
        per_element: None,
        on_next: false,
        if_type: None,
        skill_filter: None,
        skill_where: None,
        ttl: 0, // parse 不传 ttl → FlagSetOp.ttl 恒默认 0（威慑.json 案例）
        then: None,
    }])
}

/// RISC heal → Heal / Damage。
fn op_heal(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let target = get_str(e, "target", "sprite_self")?.to_string();
    let amount = if let Some(ratio) = e.get("ratio").filter(|v| !v.is_null()) {
        let ratio = val_f(crate::resolve::scalar_of(ratio)?)?;
        let hp_max = hp_max_of(ctx, &target);
        crate::damage::py_round(ratio * hp_max as f64).max(1)
    } else if let Some(v) = e.get("value").filter(|v| !v.is_null()) {
        let raw = resolve(ctx, v)?;
        match raw {
            Val::F(f) if f > 0.0 && f <= 1.0 => {
                let hp_max = hp_max_of(ctx, &target);
                crate::damage::py_round(f * hp_max as f64).max(1)
            }
            other => val_i(other)?,
        }
    } else {
        0
    };
    if amount > 0 {
        Some(vec![Mutation::Heal { target, amount }])
    } else if amount < 0 {
        Some(vec![Mutation::Damage {
            target,
            amount: -amount,
            element: ctx.element_self.clone(),
            attack_type: ctx.skill_type_self.clone(),
        }])
    } else {
        Some(vec![])
    }
}

/// RISC energize → EnergyChange。
fn op_energize(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let delta = match e.get("delta").filter(|v| !v.is_null()) {
        Some(d) => val_i(resolve(ctx, d)?)?,
        None => 0,
    };
    if delta != 0 {
        Some(vec![Mutation::EnergyChange {
            target: get_str(e, "target", "sprite_self")?.to_string(),
            delta,
        }])
    } else {
        Some(vec![])
    }
}

/// RISC revive → ModifierInjection(stat="revive")。
fn op_revive(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let hp_ratio = match e.get("hp_ratio").filter(|v| !v.is_null()) {
        Some(v) => val_f(resolve(ctx, v)?)?,
        None => 1.0,
    };
    Some(vec![Mutation::ModifierInjection {
        target: get_str(e, "target", "sprite_self")?.to_string(),
        stat: "revive".into(),
        value: Val::F(hp_ratio),
        scope: "battlefield".into(),
        mode: "set".into(),
        name: None,
        source: None,
        element: None,
        per_element: None,
        on_next: false,
        if_type: None,
        skill_filter: None,
        skill_where: None,
        ttl: 0,
        then: None,
    }])
}

/// legacy mod 大码（op_mod，typed 语义）。
fn op_mod(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    use Mutation::*;
    let target = get_str(e, "target", "sprite_self")?.to_string();
    let stat = get_str(e, "stat", "")?.to_string();
    let mode = get_str(e, "mode", "set")?.to_string();
    let scope = get_str(e, "scope", "battlefield")?.to_string();
    let on_next = get_bool(e, "on_next", false)?;
    let per_hit = get_bool(e, "per_hit", false)?;

    // parse：steps 为 "=/dict" 时 → value=steps、steps=0；value 缺省 Literal(0)
    let mut value: Option<Val> = match e.get("value").filter(|v| !v.is_null()) {
        Some(v) => Some(resolve(ctx, v)?),
        None => None,
    };
    let mut steps = get_i(e, "steps", 0)?;
    if let Some(sv) = e.get("steps") {
        if (sv.is_object() && sv.get("q").is_some())
            || (sv.is_string() && sv.as_str().unwrap_or("").starts_with('='))
        {
            value = Some(resolve(ctx, sv)?);
            steps = 0;
        }
    }
    if on_next {
        return Some(vec![]); // _defer_mod
    }

    // _resolve_value（typed）：steps != 0 优先，否则 value（缺省 0）
    let raw = if steps != 0 {
        Val::I(steps)
    } else {
        value.clone().unwrap_or(Val::I(0))
    };
    let value_f = val_f(raw.clone())?;

    let meta_element = get_str_opt(e, "element")?;
    // ModOp.per_element 经 parse 后恒为 int（默认 0）——meta 只跳过 None
    let meta_per_element = Some(get_i(e, "per_element", 0)?);
    let meta_skill_filter = get_str_opt(e, "skill_filter")?;
    let meta_skill_where = e.get("skill_where").filter(|v| !v.is_null()).cloned();
    // _parse_mod 无 source 字段；_metadata 循环不含 on_next → 恒 None/False
    let meta_source: Option<String> = None;
    let meta_if_type = get_str_opt(e, "if_type")?;
    let meta_on_next = false;

    let is_steps = steps != 0;
    let mut result: Vec<Mutation> = if is_steps {
        vec![StatChange {
            target: target.clone(),
            stat: stat.clone(),
            steps: val_i(raw.clone())?,
            scope: scope.clone(),
            source: meta_source.clone(),
            element: meta_element.clone(),
            per_element: meta_per_element,
            on_next: meta_on_next,
            if_type: meta_if_type.clone(),
            skill_filter: meta_skill_filter.clone(),
            skill_where: meta_skill_where.clone(),
        }]
    } else if stat == "hp" {
        let hp_max = hp_max_of(ctx, &target);
        if value_f >= 0.0 {
            let amount = if matches!(raw, Val::F(f) if f > 0.0 && f <= 1.0) {
                crate::damage::py_round(value_f * hp_max as f64).max(1)
            } else {
                crate::damage::py_round(value_f).max(1)
            };
            if amount > 0 {
                vec![Heal { target: target.clone(), amount }]
            } else {
                vec![]
            }
        } else {
            let amount = (value_f.round() as i64).abs();
            if amount > 0 {
                vec![Damage {
                    target: target.clone(),
                    amount,
                    element: ctx.element_self.clone(),
                    attack_type: ctx.skill_type_self.clone(),
                }]
            } else {
                vec![]
            }
        }
    } else if stat == "energy" {
        let delta = val_i(Val::F(value_f))?;
        if delta != 0 {
            vec![EnergyChange { target: target.clone(), delta }]
        } else {
            vec![]
        }
    } else if stat == "devotion" {
        // devotion 分支不透传 meta（Python 只传 name/then/ttl）
        vec![ModifierInjection {
            target: target.clone(),
            stat: stat.clone(),
            value: Val::F(value_f),
            scope: scope.clone(),
            mode: mode.clone(),
            name: get_str_opt(e, "name")?,
            source: None,
            element: None,
            per_element: None,
            on_next: false,
            if_type: None,
            skill_filter: None,
            skill_where: None,
            ttl: get_i(e, "ttl", 0)?,
            then: match e.get("then").filter(|v| !v.is_null()) {
                Some(J::Array(a)) => Some(a.clone()),
                _ => None,
            },
        }]
    } else {
        // stage / skill mod / flag / 未知 stat → ModifierInjection（带 meta）
        vec![ModifierInjection {
            target: target.clone(),
            stat: stat.clone(),
            value: Val::F(value_f),
            scope: scope.clone(),
            mode: mode.clone(),
            name: get_str_opt(e, "name")?,
            source: meta_source.clone(),
            element: meta_element.clone(),
            per_element: meta_per_element,
            on_next: meta_on_next,
            if_type: meta_if_type.clone(),
            skill_filter: meta_skill_filter.clone(),
            skill_where: meta_skill_where.clone(),
            ttl: get_i(e, "ttl", 0)?,
            then: None,
        }]
    };

    let combo = ctx.combo_self.max(1);
    if per_hit && combo > 1 && !result.is_empty() {
        let base = result.clone();
        for _ in 1..combo {
            result.extend(base.clone());
        }
    }
    Some(result)
}

/// hit：独立伤害（ops/hit.py）。
fn op_hit(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let power = resolve(ctx, e.get("power").unwrap_or(&J::from(0)))?;
    let type_ = match e.get("type") {
        Some(J::String(s)) => s.clone(),
        _ => String::new(), // parse 默认 "物攻"——但 raw JSON 无 type 时
                            // parser 已填；此处兜底空串（dict 直入路径）
    };
    if std::env::var("ROCO_DEBUG_DMG").is_ok() {
        let elem_len = e
            .get("element")
            .and_then(|v| v.as_str())
            .map(|s| s.chars().count());
        eprintln!(
            "[rust op_hit] power={:?} type={} elem_len={:?} atk_self={} def_opp={} sp_atk_self={} sp_def_opp={} dr_opp={}",
            power, type_, elem_len, ctx.atk_self, ctx.def_opp,
            ctx.sp_atk_self, ctx.sp_def_opp, ctx.damage_reduction_opp
        );
    }
    let element = match e.get("element").filter(|v| !v.is_null()) {
        Some(J::String(s)) => s.clone(),
        _ => ctx.element_self.clone(),
    };
    let (atk_base, def_base, atk_stage, def_stage) = if type_ == "物攻" {
        (
            ctx.atk_self,
            ctx.def_opp,
            stage_mult(ctx.stat_stages_self.get("atk").copied().unwrap_or(0)),
            stage_mult(ctx.stat_stages_opp.get("def").copied().unwrap_or(0)),
        )
    } else {
        (
            ctx.sp_atk_self,
            ctx.sp_def_opp,
            stage_mult(ctx.stat_stages_self.get("sp_atk").copied().unwrap_or(0)),
            stage_mult(ctx.stat_stages_opp.get("sp_def").copied().unwrap_or(0)),
        )
    };
    let amount = calc_damage(
        val_i(power)?,
        atk_base,
        def_base,
        atk_stage,
        def_stage,
        1.0,
        1.0,
        1.0,
        ctx.damage_reduction_opp,
        ctx.power_mult_self,
        1.0,
        0,
        ctx.damage_mult_self,
        ctx.combo_self,
        ctx.mark_bonus_own,
    );
    Some(vec![Mutation::Damage {
        target: "sprite_opp".into(),
        amount,
        element,
        attack_type: type_,
    }])
}

fn stage_mult(steps: i64) -> f64 {
    steps as f64 * 0.1
}

fn hp_max_of(ctx: &Ctx, target: &str) -> i64 {
    match target {
        "sprite_opp" => ctx.hp_opp_max,
        _ => ctx.hp_self_max,
    }
}

/// Python str(float)：repr 形式（14.0 → "14.0"）。
fn py_str_f64(f: f64) -> String {
    let s = format!("{f}");
    s
}

/// mark：apply/dispel/steal/convert。
fn op_mark(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let target = get_str(e, "target", "sprite_self")?;
    let action = get_str(e, "action", "apply")?.to_string();
    let delta = match e.get("stacks").filter(|v| !v.is_null()) {
        Some(s) => val_i(crate::resolve::scalar_of(s)?)?,
        None => val_i(resolve(ctx, e.get("value").unwrap_or(&J::from(1)))?)?,
    };
    let mut team = if matches!(target, "team_own" | "own_team" | "sprite_self") {
        "own"
    } else {
        "opp"
    };
    let explicit = get_str(e, "target_team", "")?;
    if !explicit.is_empty() {
        team = if matches!(explicit, "own" | "own_team" | "team_own") {
            "own"
        } else {
            "opp"
        };
    }
    let per_hit = get_bool(e, "per_hit", false)?;
    let ms = vec![Mutation::MarkChange {
        target_team: team.to_string(),
        name: get_str(e, "name", "")?.to_string(),
        delta,
        action,
        ratio: get_f(e, "ratio", 1.0)?,
        source_abnormal: get_str_opt(e, "source_abnormal")?,
    }];
    Some(per_hit_repeat(ms, per_hit, ctx.combo_self))
}

/// abnormal：施加异常层数。
fn op_abnormal(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let target = get_str(e, "target", "sprite_self")?.to_string();
    let name = get_str(e, "name", "")?.to_string();
    // parse：stacks 为查询/公式 → value=stacks、stacks=0（typed 处理器
    // 只读 stacks，不 resolve value —— delta 恒 0 的 Python 怪癖，须镜像）
    let delta = match e.get("stacks") {
        None | Some(J::Null) => 1,
        Some(sv) if sv.is_object() && sv.get("q").is_some() => 0,
        Some(J::String(s)) if s.starts_with('=') => 0,
        Some(J::String(s)) => s.parse::<i64>().ok()?,
        Some(J::Number(_)) => get_i(e, "stacks", 1)?,
        Some(_) => return None,
    };
    let scope = get_str(e, "scope", "battlefield")?.to_string();
    let per_hit = get_bool(e, "per_hit", false)?;
    let ms = vec![Mutation::AbnormalChange {
        target,
        name,
        delta,
        scope,
    }];
    Some(per_hit_repeat(ms, per_hit, ctx.combo_self))
}

fn op_weather(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let _ = ctx;
    Some(vec![Mutation::WeatherSet {
        weather: get_str(e, "weather", "")?.to_string(),
        turns: get_i(e, "turns", 8)?,
    }])
}

fn op_dispel(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let _ = ctx;
    Some(vec![Mutation::Dispel {
        target: get_str(e, "target", "sprite_self")?.to_string(),
        what: get_str(e, "what", "")?.to_string(),
        name: get_str_opt(e, "name")?,
        limit: match e.get("limit").filter(|v| !v.is_null()) {
            None => None,
            Some(v) => Some(val_i(crate::resolve::scalar_of(v)?)?),
        },
        type_limit: match e.get("type_limit").filter(|v| !v.is_null()) {
            None => None,
            Some(v) => Some(val_i(crate::resolve::scalar_of(v)?)?),
        },
        source: None,
    }])
}

fn op_steal(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let _ = ctx;
    Some(vec![Mutation::Steal {
        from_target: get_str(e, "target", "sprite_self")?.to_string(),
        what: get_str(e, "what", "")?.to_string(),
        name: get_str_opt(e, "name")?,
        amount: Some(get_i(e, "amount", 0)?),
        action: get_str(e, "action", "steal")?.to_string(),
    }])
}

fn op_tick(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let _ = ctx;
    Some(vec![Mutation::Tick {
        target: get_str(e, "target", "sprite_self")?.to_string(),
        abnormal_name: get_str(e, "name", "")?.to_string(),
    }])
}

fn op_double(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let _ = ctx;
    Some(vec![Mutation::Double {
        target: get_str(e, "target", "sprite_self")?.to_string(),
        what: get_str(e, "what", "")?.to_string(),
        name: get_str_opt(e, "name")?,
    }])
}

fn op_effect_delta(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let _ = ctx;
    Some(vec![Mutation::EffectDelta {
        target: get_str(e, "target", "sprite_opp")?.to_string(),
        what: get_str(e, "what", "negative")?.to_string(),
        delta: get_i(e, "delta", 1)?,
    }])
}

fn op_charge(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let _ = ctx;
    Some(vec![Mutation::Charge {
        target: get_str(e, "target", "sprite_self")?.to_string(),
    }])
}

fn op_escape(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let _ = ctx;
    Some(vec![Mutation::Escape {
        target: get_str(e, "target", "sprite_self")?.to_string(),
        inherit: get_bool(e, "inherit", false)?,
        urgent: get_bool(e, "urgent", false)?,
        then: match e.get("then").filter(|v| !v.is_null()) {
            Some(J::Array(a)) => Some(a.clone()),
            _ => None,
        },
    }])
}

fn op_return(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let _ = ctx;
    Some(vec![Mutation::Return {
        target: get_str(e, "target", "sprite_self")?.to_string(),
    }])
}

fn op_lock(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let _ = ctx;
    Some(vec![Mutation::Lock {
        target: get_str(e, "target", "sprite_self")?.to_string(),
        turns: get_i(e, "turns", 1)?,
    }])
}

fn op_interrupt(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let _ = ctx;
    Some(vec![Mutation::Interrupt {
        target: get_str(e, "target", "sprite_self")?.to_string(),
    }])
}

fn op_exchange(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let _ = ctx;
    Some(vec![Mutation::Exchange {
        target: "sprite_opp".into(), // parse 无 target 字段 → 运行时恒此值
        what: get_str(e, "what", "")?.to_string(),
    }])
}

fn op_reset(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let _ = ctx;
    Some(vec![Mutation::Reset {
        target: get_str(e, "target", "skill_off_0")?.to_string(),
        stat: get_str(e, "stat", "")?.to_string(),
    }])
}

fn op_redirect(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let _ = ctx;
    Some(vec![Mutation::Redirect {
        target: get_str(e, "target", "sprite_self")?.to_string(),
    }])
}

fn op_replay(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let _ = ctx;
    let from_ = match e.get("from").filter(|v| !v.is_null()) {
        Some(J::String(s)) => s.clone(),
        _ => match e.get("from_").filter(|v| !v.is_null()) {
            Some(J::String(s)) => s.clone(),
            _ => String::new(),
        },
    };
    Some(vec![Mutation::Replay {
        from_,
        skill_filter: e.get("skill_filter").filter(|v| !v.is_null()).cloned(),
    }])
}

fn op_borrow(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let _ = ctx;
    let from_ = match e.get("from").filter(|v| !v.is_null()) {
        Some(J::String(s)) => s.clone(),
        _ => match e.get("from_").filter(|v| !v.is_null()) {
            Some(J::String(s)) => s.clone(),
            _ => String::new(),
        },
    };
    Some(vec![Mutation::Borrow { from_skill: from_ }])
}

/// count / observer：注册持久计数器。
fn op_count_inner(ctx: &Ctx, e: &J, from_observer: bool) -> Option<Vec<Mutation>> {
    let _ = ctx;
    let cond = if from_observer { e.get("cond") } else { e.get("cond").or_else(|| e.get("when")) };
    let (name, threshold, reset_on_fire) = if from_observer {
        let counter = e.get("counter");
        match counter.filter(|c| c.is_object()) {
            Some(c) => (
                c.get("name").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                c.get("threshold").and_then(|x| x.as_i64()).unwrap_or(1),
                c.get("reset").and_then(|x| x.as_bool()).unwrap_or(true),
            ),
            None => (String::new(), 1, true),
        }
    } else {
        (
            get_str(e, "name", "")?.to_string(),
            get_i(e, "threshold", 1)?,
            get_bool(e, "reset_on_fire", true)?,
        )
    };
    let listen = match e.get("listen").filter(|v| !v.is_null()) {
        None => None,
        Some(J::String(s)) => Some(vec![s.clone()]),
        Some(J::Array(a)) => {
            let mut out = Vec::new();
            for item in a {
                out.push(item.as_str()?.to_string());
            }
            Some(out)
        }
        _ => return None,
    };
    Some(vec![Mutation::CounterRegister {
        name: Some(name),
        cond: cond.cloned(),
        then: match e.get("then") {
            Some(J::Array(a)) => a.clone(),
            _ => vec![],
        },
        scope: get_str(e, "scope", "persistent")?.to_string(),
        listen,
        threshold,
        reset_on_fire,
    }])
}

fn op_count(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    op_count_inner(ctx, e, false)
}

fn op_observer(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    op_count_inner(ctx, e, true)
}

fn op_team_counter_write(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let _ = ctx;
    Some(vec![Mutation::TeamCounterDelta {
        target: get_str(e, "target", "own")?.to_string(),
        key: get_str(e, "key", "")?.to_string(),
        delta: get_i(e, "delta", 1)?,
    }])
}

fn op_lives_change(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let _ = ctx;
    Some(vec![Mutation::LivesDelta {
        target_team: get_str(e, "target_team", "own")?.to_string(),
        delta: get_i(e, "delta", 1)?,
    }])
}

/// schedule/defer：延迟效果登记（含 freeze）。
fn op_schedule(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let turns = match e.get("turns").filter(|v| !v.is_null()) {
        Some(v) => val_i(crate::resolve::scalar_of(v)?)?,
        None => get_i(e, "delay_turns", 1)?,
    };
    let at_raw = e
        .get("at")
        .and_then(|x| x.as_str())
        .or_else(|| e.get("phase").and_then(|x| x.as_str()))
        .unwrap_or("start");
    let at = match at_raw {
        "turn_end" => "end",
        "turn_start" => "start",
        other => other,
    };
    let then = match e.get("then").filter(|v| !v.is_null()) {
        Some(J::Array(a)) => a.clone(),
        _ => match e.get("effects").filter(|v| !v.is_null()) {
            Some(J::Array(a)) => a.clone(),
            _ => vec![],
        },
    };
    let frozen: Vec<J> = then.iter().map(|eff| freeze_one(eff, ctx)).collect();
    Some(vec![Mutation::ScheduleEntry {
        turns,
        at: at.to_string(),
        then: frozen,
    }])
}

/// schedule.py::_freeze_one：冻结键 delta/value/ratio/power/hp_ratio/stacks
/// 的查询/公式为当场 resolve 的字面量；then/effects 递归。
fn freeze_one(effect: &J, ctx: &Ctx) -> J {
    if !effect.is_object() {
        return effect.clone();
    }
    let mut out = serde_json::Map::new();
    for (k, v) in effect.as_object().unwrap() {
        if matches!(k.as_str(), "then" | "effects") {
            if let Some(arr) = v.as_array() {
                out.insert(k.clone(), J::Array(arr.iter().map(|x| freeze_one(x, ctx)).collect()));
            } else {
                out.insert(k.clone(), v.clone());
            }
        } else if matches!(k.as_str(), "delta" | "value" | "ratio" | "power" | "hp_ratio" | "stacks") {
            let frozen: Option<J> = match v {
                J::Object(o) if o.contains_key("q") => {
                    resolve(ctx, v).map(|x| val_to_json(&x))
                }
                J::String(s) if s.starts_with('=') => {
                    resolve(ctx, v).map(|x| val_to_json(&x))
                }
                _ => None,
            };
            match frozen {
                Some(j) => {
                    out.insert(k.clone(), j);
                }
                None => {
                    out.insert(k.clone(), v.clone());
                }
            }
        } else {
            out.insert(k.clone(), v.clone());
        }
    }
    J::Object(out)
}

fn val_to_json(v: &Val) -> J {
    v.to_json()
}

fn op_inherit_effects(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let _ = ctx;
    let effects = match e.get("effects").filter(|v| !v.is_null()) {
        Some(J::Array(a)) => a.clone(),
        _ => vec![],
    };
    Some(vec![Mutation::InheritEffects {
        source_key: get_str(e, "source", "self")?.to_string(),
        target_key: get_str(e, "target", "enemy_new")?.to_string(),
        scope: get_str(e, "scope", "battlefield")?.to_string(),
        via_pending: get_bool(e, "via_pending", false)?,
        effects,
        inherit_stat_effects: get_bool(e, "inherit_stat_effects", false)?,
    }])
}

fn op_transform(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let _ = ctx;
    let skills = match e.get("skills").filter(|v| !v.is_null()) {
        Some(J::Array(a)) => {
            let mut out = Vec::new();
            for s in a {
                out.push(s.as_str()?.to_string());
            }
            Some(out)
        }
        _ => None,
    };
    Some(vec![Mutation::Transform {
        species: get_str(e, "species", "")?.to_string(),
        skills,
        reset_hp: get_bool(e, "reset_hp", false)?,
        reset_energy: get_bool(e, "reset_energy", false)?,
    }])
}

fn op_trait_interaction(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let _ = ctx;
    Some(vec![Mutation::TraitInteraction {
        action: get_str(e, "action", "")?.to_string(),
        target: get_str(e, "target", "sprite_self")?.to_string(),
        copy_from: get_str_opt(e, "copy_from")?,
        new_ability: get_str_opt(e, "new_ability")?,
    }])
}

fn op_gain_skills(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let _ = ctx;
    Some(vec![Mutation::GainSkills {
        count: get_i(e, "count", 1)?,
        exclude_carried: get_bool(e, "exclude_carried", true)?,
        source: get_str(e, "source", "learnset")?.to_string(),
        target: get_str(e, "target", "sprite_self")?.to_string(),
    }])
}

fn op_burst_grant(ctx: &Ctx, e: &J) -> Option<Vec<Mutation>> {
    let _ = ctx;
    let then = match e.get("then").filter(|v| !v.is_null()) {
        Some(J::Array(a)) => a.clone(),
        _ => vec![],
    };
    Some(vec![Mutation::BurstGrant {
        target: get_str(e, "target", "sprite_self")?.to_string(),
        skill_where: e.get("skill_where").filter(|v| !v.is_null()).cloned(),
        skill_filter: get_str_opt(e, "skill_filter")?,
        effects: then,
        source: get_str_opt(e, "source")?.unwrap_or_default(),
    }])
}
