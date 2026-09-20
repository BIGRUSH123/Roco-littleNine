//! formula — "=@..." 公式串求值（移植自 resolve.py 的
//! _resolve_formula_string / _resolve_trait_ref + 受限 Python eval）。
//!
//! Python eval 语义逐项镜像：
//! - `/` 真除法（恒 float）；`//` 向下取整除；`%` 结果符号随除数
//! - `**` 右结合，且一元负号绑定弱于左侧 `**`（-2**2 = -4）
//! - int() 向零截断；round() 银行家舍入
//! - 求值失败 → 0（对应 Python 的 except Exception: return 0）

use crate::damage::py_round;
use crate::resolve::Val;
use crate::vm_ctx::Ctx;

// ── @ref 解析（_resolve_trait_ref）──

/// _FORMULA_PATH_MAP：路径 → Ctx/Event 字段。
fn formula_path_field(path: &str) -> Option<&'static str> {
    Some(match path {
        "self.energy" => "energy_self",
        "self.hp" => "hp_self",
        "self.hp_ratio" => "hp_self_ratio",
        "self.max_hp" => "hp_self_max",
        "self.is_charging" => "is_charging_self",
        "self.first_action" => "first_action_self",
        "self.first_action_battle" => "first_action_battle_self",
        "self.charged" => "charged_self",
        "self.positive_count" => "positive_count_self",
        "self.abnormal_count" => "abnormal_count_self",
        "self.fainted" => "self_koed",          // event 字段
        "self.just_entered" => "just_entered",
        "self.damage_reduction" => "damage_reduction_self",
        "self.energy_cost_total" => "skills_energy_sum_self",
        "self.energy_cost" => "energy_cost_self",
        "self.speed" => "speed_self",
        "self.atk" => "atk_self",
        "self.def" => "def_self",
        "self.sp_atk" => "sp_atk_self",
        "self.sp_def" => "sp_def_self",
        "target.energy" => "energy_opp",
        "target.hp" => "hp_opp",
        "target.hp_ratio" => "hp_opp_ratio",
        "target.max_hp" => "hp_opp_max",
        "target.positive_count" => "positive_count_opp",
        "target.abnormal_count" => "abnormal_count_opp",
        "self.skill_element_count" => "skill_element_count_self",
        "target.skill_element_count" => "skill_element_count_opp",
        "target.energy_cost" => "energy_cost_opp",
        "target.energy_cost_total" => "skills_energy_sum_opp",
        "target.speed" => "speed_opp",
        "target.atk" => "atk_opp",
        "target.def" => "def_opp",
        "target.sp_atk" => "sp_atk_opp",
        "target.sp_def" => "sp_def_opp",
        "skill.power" => "power_self",
        "skill.element" => "element_self",
        "skill.energy_cost" => "energy_cost_self",
        "skill.combo" => "combo_self",
        "opponent_skill.power" => "power_opp",
        "player_fainted_count" => "fainted_own",
        "opponent_fainted_count" => "fainted_opp",
        "use.is_first" => "is_first",
        "first_action" => "first_action_self",
        "first_action_battle" => "first_action_battle_self",
        "delta" => "energy_delta_self",
        "battle.globals.weather" => "weather",
        "opponent.lives" => "lives_opp",
        "effect_name" => "abnormal_applied_name",   // event 字段
        "player_moe_stacks" => "moe_team_stacks",
        "positive_changed_stat" => "positive_changed_stat",   // event 字段
        "positive_changed_steps" => "positive_changed_steps", // event 字段
        _ => return None,
    })
}

/// ctx / event 字段统一取值（_get_ctx_field）。
fn get_field(ctx: &Ctx, field: &str) -> Val {
    use Val::*;
    match field {
        "energy_self" => I(ctx.energy_self),
        "hp_self" => I(ctx.hp_self),
        "hp_self_ratio" => F(ctx.hp_self_ratio),
        "hp_self_max" => I(ctx.hp_self_max),
        "is_charging_self" => B(ctx.is_charging_self),
        "first_action_self" => B(ctx.first_action_self),
        "first_action_battle_self" => B(ctx.first_action_battle_self),
        "charged_self" => B(ctx.charged_self),
        "positive_count_self" => I(ctx.positive_count_self),
        "abnormal_count_self" => I(ctx.abnormal_count_self),
        "just_entered" => B(ctx.just_entered),
        "damage_reduction_self" => F(ctx.damage_reduction_self),
        "skills_energy_sum_self" => I(ctx.skills_energy_sum_self),
        "energy_cost_self" => I(ctx.energy_cost_self),
        "speed_self" => I(ctx.speed_self),
        "atk_self" => I(ctx.atk_self),
        "def_self" => I(ctx.def_self),
        "sp_atk_self" => I(ctx.sp_atk_self),
        "sp_def_self" => I(ctx.sp_def_self),
        "energy_opp" => I(ctx.energy_opp),
        "hp_opp" => I(ctx.hp_opp),
        "hp_opp_ratio" => F(ctx.hp_opp_ratio),
        "hp_opp_max" => I(ctx.hp_opp_max),
        "positive_count_opp" => I(ctx.positive_count_opp),
        "abnormal_count_opp" => I(ctx.abnormal_count_opp),
        "skill_element_count_self" => I(ctx.skill_element_count_self),
        "skill_element_count_opp" => I(ctx.skill_element_count_opp),
        "energy_cost_opp" => I(ctx.energy_cost_opp),
        "skills_energy_sum_opp" => I(ctx.skills_energy_sum_opp),
        "speed_opp" => I(ctx.speed_opp),
        "atk_opp" => I(ctx.atk_opp),
        "def_opp" => I(ctx.def_opp),
        "sp_atk_opp" => I(ctx.sp_atk_opp),
        "sp_def_opp" => I(ctx.sp_def_opp),
        "power_self" => I(ctx.power_self),
        "element_self" => S(ctx.element_self.clone()),
        "combo_self" => I(ctx.combo_self),
        "power_opp" => I(ctx.power_opp),
        "fainted_own" => I(ctx.fainted_own),
        "fainted_opp" => I(ctx.fainted_opp),
        "is_first" => B(ctx.is_first),
        "energy_delta_self" => I(ctx.energy_delta_self),
        "weather" => S(ctx.weather.clone()),
        "lives_opp" => I(ctx.lives_opp),
        "moe_team_stacks" => I(ctx.moe_team_stacks),
        // event 字段
        "self_koed" => B(ctx.event.self_koed),
        "abnormal_applied_name" => S(ctx.event.abnormal_applied_name.clone()),
        "positive_changed_stat" => S(ctx.event.positive_changed_stat.clone()),
        "positive_changed_steps" => I(ctx.event.positive_changed_steps),
        _ => I(0),
    }
}

/// 解析 `name[...]`/`name` 形式的一段标识符 + 可选下标。
struct PathParts {
    head: String,
    /// 有序段：.ident 或 [bracket]
    segs: Vec<Seg>,
}

enum Seg {
    Attr(String),
    Bracket(String),
}

fn parse_ref_path(path: &str) -> Option<PathParts> {
    let bytes = path.as_bytes();
    let mut i = 0;
    let start = i;
    while i < bytes.len() && (bytes[i].is_ascii_alphanumeric() || bytes[i] == b'_') {
        i += 1;
    }
    if i == start {
        return None;
    }
    let head = path[start..i].to_string();
    let mut segs = Vec::new();
    while i < bytes.len() {
        match bytes[i] {
            b'.' => {
                i += 1;
                let s = i;
                while i < bytes.len() && (bytes[i].is_ascii_alphanumeric() || bytes[i] == b'_') {
                    i += 1;
                }
                if i == s {
                    return None;
                }
                segs.push(Seg::Attr(path[s..i].to_string()));
            }
            b'[' => {
                let close = path[i..].find(']')? + i;
                segs.push(Seg::Bracket(path[i + 1..close].to_string()));
                i = close + 1;
            }
            _ => return None,
        }
    }
    Some(PathParts { head, segs })
}

/// _resolve_trait_ref：单个 @path 引用。
pub fn resolve_trait_ref(path: &str, ctx: &Ctx) -> Val {
    let path = path.strip_prefix('@').unwrap_or(path);

    if let Some(field) = formula_path_field(path) {
        return get_field(ctx, field);
    }

    // (self|target).effects[name=X].prop
    if let Some(rest) = path.strip_prefix("self.").or_else(|| path.strip_prefix("target.")) {
        let is_self = path.starts_with("self.");
        if let Some(rest) = rest.strip_prefix("effects[name=") {
            if let Some((name, prop)) = rest.split_once(']') {
                let prop = prop.strip_prefix('.').unwrap_or("");
                let stacks = if is_self { &ctx.abnormal_stacks_self } else { &ctx.abnormal_stacks_opp };
                let val = stacks.get(name).copied().unwrap_or(0);
                return match prop {
                    "exists" => Val::B(val > 0),
                    "stacks" => Val::I(val),
                    _ => Val::I(0),
                };
            }
        }
        if let Some(rest) = rest.strip_prefix("counters[") {
            if let Some(key) = rest.strip_suffix(']') {
                return Val::I(ctx.counter_values.get(key).copied().unwrap_or(0));
            }
        }
        if let Some(rest) = rest.strip_prefix("skills[element=") {
            if let Some((element, prop)) = rest.split_once(']') {
                if prop == ".count" {
                    let counts = if is_self {
                        &ctx.skill_element_counts_self
                    } else {
                        &ctx.skill_element_counts_opp
                    };
                    return Val::I(counts.get(element).copied().unwrap_or(0));
                }
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
                    return Val::I(ctx.team_counters_opp.get(key).copied().unwrap_or(0));
                }
                return Val::I(ctx.team_counters_own.get(key).copied().unwrap_or(0));
            }
        }
    }

    Val::I(0)
}

// ── 受限 Python 表达式求值 ──

#[derive(Debug, Clone)]
enum Tok {
    Num(Val),
    Ident(String),
    Ref(String), // @path（含 @）
    Plus, Minus, Star, Slash, DSlash, Percent, Power,
    LParen, RParen, Comma,
}

fn tokenize(expr: &str) -> Result<Vec<Tok>, ()> {
    let b = expr.as_bytes();
    let mut i = 0;
    let mut out = Vec::new();
    while i < b.len() {
        let c = b[i];
        match c {
            b' ' | b'\t' => i += 1,
            b'+' => { out.push(Tok::Plus); i += 1; }
            b'-' => { out.push(Tok::Minus); i += 1; }
            b'*' => {
                if i + 1 < b.len() && b[i + 1] == b'*' { out.push(Tok::Power); i += 2; }
                else { out.push(Tok::Star); i += 1; }
            }
            b'/' => {
                if i + 1 < b.len() && b[i + 1] == b'/' { out.push(Tok::DSlash); i += 2; }
                else { out.push(Tok::Slash); i += 1; }
            }
            b'%' => { out.push(Tok::Percent); i += 1; }
            b'(' => { out.push(Tok::LParen); i += 1; }
            b')' => { out.push(Tok::RParen); i += 1; }
            b',' => { out.push(Tok::Comma); i += 1; }
            b'@' => {
                // @path：与 _REF_PATTERN 一致 —— @ident，后随 (.ident | [..])*
                let start = i;
                i += 1;
                while i < b.len() && (b[i].is_ascii_alphanumeric() || b[i] == b'_') { i += 1; }
                if i == start + 1 { return Err(()); }
                loop {
                    if i < b.len() && (b[i] == b'.' || b[i] == b'[') {
                        if b[i] == b'.' {
                            i += 1;
                            while i < b.len() && (b[i].is_ascii_alphanumeric() || b[i] == b'_') { i += 1; }
                        } else {
                            let close = expr[i..].find(']').ok_or(())? + i;
                            i = close + 1;
                        }
                    } else { break; }
                }
                out.push(Tok::Ref(expr[start..i].to_string()));
            }
            b'0'..=b'9' | b'.' => {
                let start = i;
                let mut is_float = false;
                while i < b.len() && (b[i].is_ascii_digit() || b[i] == b'.') {
                    if b[i] == b'.' { is_float = true; }
                    i += 1;
                }
                // 科学计数法
                if i < b.len() && (b[i] == b'e' || b[i] == b'E') {
                    let mut j = i + 1;
                    if j < b.len() && (b[j] == b'+' || b[j] == b'-') { j += 1; }
                    if j < b.len() && b[j].is_ascii_digit() {
                        is_float = true;
                        i = j;
                        while i < b.len() && b[i].is_ascii_digit() { i += 1; }
                    }
                }
                let text = &expr[start..i];
                if is_float {
                    out.push(Tok::Num(Val::F(text.parse::<f64>().map_err(|_| ())?)));
                } else {
                    match text.parse::<i64>() {
                        Ok(v) => out.push(Tok::Num(Val::I(v))),
                        Err(_) => out.push(Tok::Num(Val::F(text.parse::<f64>().map_err(|_| ())?))),
                    }
                }
            }
            _ if c.is_ascii_alphabetic() || c == b'_' => {
                let start = i;
                while i < b.len() && (b[i].is_ascii_alphanumeric() || b[i] == b'_') { i += 1; }
                out.push(Tok::Ident(expr[start..i].to_string()));
            }
            _ => return Err(()),
        }
    }
    Ok(out)
}

/// Python 二元/一元算术。非数值参与 → Err（对应 str(True)/NameError 路径）。
fn bin_op(a: Val, op: &Tok, b: Val) -> Result<Val, ()> {
    use Val::*;
    let mix = |x: &Val, y: &Val| -> Option<(f64, f64)> { Some((x.as_num()?, y.as_num()?)) };
    match op {
        Tok::Plus | Tok::Minus | Tok::Star => {
            // int ⊕ int → int；含 float → float
            if let (I(x), I(y)) = (&a, &b) {
                return Ok(match op {
                    Tok::Plus => I(x.wrapping_add(*y)),
                    Tok::Minus => I(x.wrapping_sub(*y)),
                    _ => I(x.wrapping_mul(*y)),
                });
            }
            let (x, y) = mix(&a, &b).ok_or(())?;
            Ok(match op {
                Tok::Plus => F(x + y),
                Tok::Minus => F(x - y),
                _ => F(x * y),
            })
        }
        Tok::Slash => {
            let (x, y) = mix(&a, &b).ok_or(())?;
            if y == 0.0 { return Err(()); } // Python ZeroDivisionError → 整体 0
            Ok(F(x / y))
        }
        Tok::DSlash => {
            if let (I(x), I(y)) = (&a, &b) {
                if *y == 0 { return Err(()); }
                // Python floor division
                return Ok(I(x.div_euclid(*y)));
            }
            let (x, y) = mix(&a, &b).ok_or(())?;
            if y == 0.0 { return Err(()); }
            Ok(F((x / y).floor()))
        }
        Tok::Percent => {
            if let (I(x), I(y)) = (&a, &b) {
                if *y == 0 { return Err(()); }
                return Ok(I(x.rem_euclid(*y)));
            }
            let (x, y) = mix(&a, &b).ok_or(())?;
            if y == 0.0 { return Err(()); }
            // Python：结果符号随除数（rem_euclid 恒非负，需修正）
            let r = x.rem_euclid(y);
            Ok(F(if r != 0.0 && y < 0.0 { r - y.abs() } else { r }))
        }
        Tok::Power => {
            if let (I(x), I(y)) = (&a, &b) {
                if *y >= 0 {
                    if let Some(v) = (*x).checked_pow((*y).min(u32::MAX as i64) as u32) {
                        return Ok(I(v));
                    }
                    return Ok(F((*x as f64).powf(*y as f64)));
                }
                return Ok(F((*x as f64).powf(*y as f64)));
            }
            let (x, y) = mix(&a, &b).ok_or(())?;
            Ok(F(x.powf(y)))
        }
        _ => Err(()),
    }
}

/// Python round(x, n)（n 可空）：银行家舍入。
fn py_round_n(x: f64, n: Option<i32>) -> f64 {
    match n {
        None => py_round(x) as f64,
        Some(n) => {
            let factor = 10f64.powi(n);
            let scaled = x * factor;
            (py_round(scaled) as f64) / factor
        }
    }
}

macro_rules! expect {
    ($p:expr, $pat:pat) => {
        match $p.next() {
            Some(t) if matches!(t, $pat) => {}
            _ => return Err(()),
        }
    };
}

struct Parser<'a> {
    toks: &'a [Tok],
    pos: usize,
    ctx: &'a Ctx,
}

impl<'a> Parser<'a> {
    fn peek(&self) -> Option<&Tok> { self.toks.get(self.pos) }
    fn next(&mut self) -> Option<Tok> {
        let t = self.toks.get(self.pos).cloned();
        if t.is_some() { self.pos += 1; }
        t
    }

    /// arith: term ((+|-) term)*
    fn parse_arith(&mut self) -> Result<Val, ()> {
        let mut left = self.parse_term()?;
        loop {
            match self.peek() {
                Some(Tok::Plus) | Some(Tok::Minus) => {
                    let op = self.next().unwrap();
                    let right = self.parse_term()?;
                    left = bin_op(left, &op, right)?;
                }
                _ => return Ok(left),
            }
        }
    }

    /// term: factor ((*|/|//|%) factor)*
    fn parse_term(&mut self) -> Result<Val, ()> {
        let mut left = self.parse_factor()?;
        loop {
            match self.peek() {
                Some(Tok::Star) | Some(Tok::Slash) | Some(Tok::DSlash) | Some(Tok::Percent) => {
                    let op = self.next().unwrap();
                    let right = self.parse_factor()?;
                    left = bin_op(left, &op, right)?;
                }
                _ => return Ok(left),
            }
        }
    }

    /// factor: (+|-) factor | power
    fn parse_factor(&mut self) -> Result<Val, ()> {
        match self.peek() {
            Some(Tok::Plus) => { self.next(); self.parse_factor() }
            Some(Tok::Minus) => {
                self.next();
                let v = self.parse_factor()?;
                match v {
                    Val::I(i) => Ok(Val::I(-i)),
                    Val::F(f) => Ok(Val::F(-f)),
                    _ => Err(()),
                }
            }
            _ => self.parse_power(),
        }
    }

    /// power: primary [** factor]（右结合；右侧允许一元负号）
    fn parse_power(&mut self) -> Result<Val, ()> {
        let base = self.parse_primary()?;
        if matches!(self.peek(), Some(Tok::Power)) {
            self.next();
            let exp = self.parse_factor()?; // 右结合
            return bin_op(base, &Tok::Power, exp);
        }
        Ok(base)
    }

    /// primary: Num | Ref | Ident(args) | ( arith )
    fn parse_primary(&mut self) -> Result<Val, ()> {
        match self.next().ok_or(())? {
            Tok::Num(v) => Ok(v),
            Tok::Ref(path) => {
                let v = resolve_trait_ref(&path, self.ctx);
                // Python 的替换后 eval：非数值字符串会成为 NameError → 整体 0。
                // 布尔 True/False 同理。集合/映射同理。
                match v {
                    Val::I(_) | Val::F(_) => Ok(v),
                    _ => Err(()),
                }
            }
            Tok::Ident(name) => {
                expect!(self, Tok::LParen);
                let mut args = Vec::new();
                if !matches!(self.peek(), Some(Tok::RParen)) {
                    loop {
                        args.push(self.parse_arith()?);
                        match self.peek() {
                            Some(Tok::Comma) => { self.next(); }
                            _ => break,
                        }
                    }
                }
                expect!(self, Tok::RParen);
                call_builtin(&name, &args)
            }
            Tok::LParen => {
                let v = self.parse_arith()?;
                expect!(self, Tok::RParen);
                Ok(v)
            }
            _ => Err(()),
        }
    }
}

fn call_builtin(name: &str, args: &[Val]) -> Result<Val, ()> {
    match name {
        "int" => {
            let x = single(args)?;
            Ok(Val::I(match x {
                Val::I(i) => i,
                Val::F(f) => crate::resolve::py_int(f),
                Val::B(b) => if b { 1 } else { 0 },
                _ => return Err(()),
            }))
        }
        "float" => {
            let x = single(args)?;
            Ok(Val::F(x.as_num().ok_or(())?))
        }
        "round" => {
            if args.is_empty() || args.len() > 2 { return Err(()); }
            match (&args[0], args.get(1)) {
                (Val::I(i), None) => Ok(Val::I(*i)),
                (Val::I(i), Some(Val::I(_))) => Ok(Val::I(*i)),
                (Val::F(f), None) => Ok(Val::I(py_round(*f))),
                (Val::F(f), Some(n)) => {
                    let n = match n {
                        Val::I(k) => *k as i32,
                        _ => return Err(()),
                    };
                    Ok(Val::F(py_round_n(*f, Some(n))))
                }
                _ => Err(()),
            }
        }
        "max" | "min" => {
            if args.is_empty() { return Err(()); }
            let mut best = args[0].clone();
            for a in &args[1..] {
                let (x, y) = (best.as_num().ok_or(())?, a.as_num().ok_or(())?);
                let take = if name == "max" { y > x } else { y < x };
                if take { best = a.clone(); }
            }
            Ok(best)
        }
        "abs" => {
            let x = single(args)?;
            Ok(match x {
                Val::I(i) => Val::I(i.abs()),
                Val::F(f) => Val::F(f.abs()),
                _ => return Err(()),
            })
        }
        _ => Err(()), // 未知名字 → NameError → 0
    }
}

fn single(args: &[Val]) -> Result<Val, ()> {
    if args.len() != 1 { return Err(()); }
    Ok(args[0].clone())
}

/// _resolve_formula_string 的主体：expr 已去掉 '='。
/// Python 逻辑：含运算符字符 → eval 表达式；否则按单一路径引用解析。
pub fn resolve_formula_string(ctx: &Ctx, expr: &str) -> Val {
    let has_op = expr.chars().any(|c| matches!(c, '+' | '-' | '*' | '/' | '(' | ')'));
    if has_op {
        let toks = match tokenize(expr) {
            Ok(t) if !t.is_empty() => t,
            _ => return Val::I(0),
        };
        let mut p = Parser { toks: &toks, pos: 0, ctx };
        match p.parse_arith() {
            Ok(v) if p.pos == p.toks.len() => v,
            _ => Val::I(0), // 解析/求值失败 → Python except Exception: return 0
        }
    } else {
        resolve_trait_ref(expr, ctx)
    }
}
