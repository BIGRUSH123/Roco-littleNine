"""特性 JSON 可视化编辑器。

启动后打开浏览器，左侧文件列表 + 右侧 JSON 编辑 + 一键保存。
无外部依赖，仅用 Python 标准库。

用法: python scripts/tools/trait_editor.py [--port 8766]
"""

import json
import shutil
import subprocess
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

TRAITS_DIR = Path(__file__).resolve().parent.parent.parent / 'data' / 'traits'
TRAITS_DIR_ESC = str(TRAITS_DIR).replace('\\', '\\\\')
IR_RISC_PATH = Path(__file__).resolve().parent.parent.parent / 'data' / 'IR_RISC.md'
def _find_claude() -> str:
    """查找 claude CLI，优先 PATH，其次搜索常见安装位置。"""
    found = shutil.which('claude') or shutil.which('claude.exe')
    if found:
        return found
    # 常见安装路径
    for p in (
        Path.home() / '.local' / 'bin' / 'claude.exe',
        Path.home() / 'AppData' / 'Local' / 'Programs' / 'claude' / 'claude.exe',
        Path.home() / 'AppData' / 'Roaming' / 'npm' / 'claude.cmd',
    ):
        if p.exists():
            return str(p)
    return 'claude'  # 最后的 fallback

CLAUDE_CLI = _find_claude()

# ── Format helpers ──────────────────────────────────────────────

TOP_ORDER = [
    'id', 'name', 'description', 'effects', 'triggers',
]

TRIGGER_ORDER = [
    'on', 'condition', 'use_modifiers', 'effects', 'battleskill_mut',
]

DROP_IF_DEFAULT = {}


def _normalize_trigger(trg: dict) -> dict:
    out = {k: trg[k] for k in TRIGGER_ORDER if k in trg}
    for k, v in trg.items():
        if k not in out:
            out[k] = v
    if isinstance(out.get('effects'), list):
        out['effects'] = [_normalize_effect(e) for e in out['effects']]
    return out


def _normalize_effect(eff: dict) -> dict:
    return eff  # 保持原样，特性中的 effects 结构多样


def format_trait(data: dict) -> str:
    ordered = {k: data[k] for k in TOP_ORDER if k in data}
    for k, v in data.items():
        if k not in ordered:
            ordered[k] = v
    for k, dv in DROP_IF_DEFAULT.items():
        if ordered.get(k) == dv:
            del ordered[k]
    if isinstance(ordered.get('triggers'), list):
        ordered['triggers'] = [_normalize_trigger(t) for t in ordered['triggers']]
    if isinstance(ordered.get('effects'), list):
        ordered['effects'] = [_normalize_effect(e) for e in ordered['effects']]
    return json.dumps(ordered, ensure_ascii=False, indent=2) + '\n'


# ── API Handler ──────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(body))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(length).decode('utf-8') if length else ''

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,PUT,POST,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        p = urlparse(self.path)

        if p.path == '/':
            self._serve_html()
        elif p.path == '/api/traits':
            self._list_traits()
        elif p.path.startswith('/api/trait/'):
            name = unquote(p.path[len('/api/trait/'):])
            self._get_trait(name)
        else:
            self.send_response(404)
            self.end_headers()

    def do_PUT(self):
        p = urlparse(self.path)
        if p.path.startswith('/api/trait/'):
            name = unquote(p.path[len('/api/trait/'):])
            self._save_trait(name)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        p = urlparse(self.path)
        if p.path.startswith('/api/trait/') and p.path.endswith('/convert'):
            name = unquote(p.path[len('/api/trait/'):-len('/convert')])
            self._convert_trait(name)
        else:
            self.send_response(404)
            self.end_headers()

    def _list_traits(self):
        traits = []
        for f in sorted(TRAITS_DIR.glob('*.json')):
            if f.name.startswith('_'):
                continue
            try:
                d = json.loads(f.read_text('utf-8'))
                traits.append({
                    'name': d.get('name', f.stem),
                    'filename': f.name,
                    'id': d.get('id', 0),
                    'triggers_count': len(d.get('effects', [])) or len(d.get('triggers', [])),
                    'description': d.get('description', ''),
                })
            except Exception:
                traits.append({
                    'name': f.stem, 'filename': f.name,
                    'id': 0, 'triggers_count': 0,
                    'description': '',
                })
        self._send_json(traits)

    def _get_trait(self, name):
        fpath = TRAITS_DIR / f'{name}.json'
        if not fpath.exists():
            self._send_json({'error': f'特性 "{name}" 不存在'}, 404)
            return
        self._send_json({'content': fpath.read_text('utf-8'), 'filename': fpath.name})

    def _save_trait(self, name):
        if name.startswith('_'):
            self._send_json({'error': '不能编辑索引文件'}, 403)
            return

        fpath = TRAITS_DIR / f'{name}.json'
        if not fpath.exists():
            self._send_json({'error': f'特性 "{name}" 不存在'}, 404)
            return

        body = self._read_body()
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            self._send_json({'error': f'JSON 格式错误: {e}'}, 400)
            return

        if not isinstance(data, dict):
            self._send_json({'error': 'JSON 必须是对象 (dict)'}, 400)
            return
        if 'name' not in data:
            self._send_json({'error': '缺少必填字段: name'}, 400)
            return
        if data.get('name') != name:
            self._send_json({'error': f'name 字段 "{data.get("name","")}" 与文件名 "{name}" 不一致'}, 400)
            return

        formatted = format_trait(data)
        fpath.write_text(formatted, 'utf-8')
        self._send_json({'ok': True, 'filename': fpath.name})

    def _convert_trait(self, name):
        try:
            self._do_convert_trait(name)
        except Exception as e:
            self._send_json({'error': f'转换异常: {e}'}, 500)

    def _do_convert_trait(self, name):
        fpath = TRAITS_DIR / f'{name}.json'
        if not fpath.exists():
            self._send_json({'error': f'特性 "{name}" 不存在'}, 404)
            return

        body = self._read_body()
        trait_json = body if body else fpath.read_text('utf-8')

        # ── 读取 IR_RISC 规范全文 ──────────────────────────────────
        ir_risc_text = ''
        if IR_RISC_PATH.exists():
            ir_risc_text = IR_RISC_PATH.read_text('utf-8')

        prompt = (
            '# 任务\n'
            '你是格斗小九游戏的**特性编译器**。你只生成特性(trait)的 `effects[]`，不生成技能(skill)。\n'
            '仔细阅读下面特性 JSON 中的 `description` 字段，理解该特性的语义和效果，'
            '然后为它生成 `effects[]` 字段。\n'
            '**description 是唯一的语义来源**——忽略 JSON 中可能存在的旧 `triggers[]`、`passive[]` 或其他结构字段，'
            '一切以 description 描述的效果为准。\n\n'

            '# 核心概念：特性 vs 技能\n'
            '- **技能(skill)**：一次性动作。`effects[]` 中直接存放 VM opcode，技能释放时立即执行。\n'
            '- **特性(trait)**：持久化被动能力。`effects[]` 中**只能**存放 `observer` op —— '
            '每个 observer 是一个条件监听器，在特定 hook 点触发 `then` 块中的 VM opcode。\n'
            '- 特性 = 持久化条件→动作绑定。外层永远是 observer 包装器，'
            '`then` 内部才是 VM opcode。\n\n'

            '# 输出格式\n'
            '```json\n'
            '{\n'
            '  "id": ...,\n'
            '  "name": "...",\n'
            '  "description": "...",\n'
            '  "effects": [\n'
            '    {\n'
            '      "op": "observer",\n'
            '      "cond": { ... },\n'
            '      "then": [ { ... }, { ... } ],\n'
            '      "listen": "...",\n'
            '      "scope": "..."\n'
            '    }\n'
            '  ]\n'
            '}\n'
            '```\n'
            '- `effects[]` 中的每一项都必须是一个 observer op\n'
            '- 一个特性可以有多个 observer（如"入场时X，行动后Y"→ 2个 observer）\n'
            '- 保留 `id`, `name`, `description` 不变；保留 `source` 字段（如存在）\n\n'

            '# Observer 结构\n'
            '每个 observer 必须包含 5 个字段：\n'
            '| 字段 | 类型 | 说明 |\n'
            '|------|------|------|\n'
            '| `op` | string | 固定为 "observer" |\n'
            '| `cond` | object | 触发条件（见下方条件参考）|\n'
            '| `then` | array | 触发时执行的 VM opcode 数组，每条 op 必须包含 `target` 字段 |\n'
            '| `listen` | string | 触发 hook 点（见下方 hook 参考表）|\n'
            '| `scope` | string | observer 自身生命周期（见下方 scope 参考）|\n\n'

            '# Hook 点参考（listen 字段取值）\n'
            '| listen | 触发时机 | 匹配的 cond 条件 |\n'
            '|--------|---------|-----------------|\n'
            '| "post_entry" | 精灵入场后 | sprite_entered |\n'
            '| "post_leave" | 己方离场后 | sprite_left (of: sprite_self) |\n'
            '| "post_enemy_leave" | 敌方离场后 | sprite_left (of: sprite_opp) / opp_switched |\n'
            '| "pre_calc" | 伤害计算前(遍历) | have_skill_of / compare(无条件被动) |\n'
            '| "post_skill" | 技能释放后 | skill_use / sprite_acted |\n'
            '| "post_damage" | 受到伤害后 | on_damage_taken |\n'
            '| "post_switch" | 精灵切换后 | opp_switched / self_switched |\n'
            '| "post_ko" | 精灵力竭后 | on_self_ko / on_ko |\n'
            '| "post_counter" | 应对成功后 | counter_succeeded / prev_counter_succeeded |\n'
            '| "post_abnormal_tick" | 异常tick后 | on_abnormal_tick |\n'
            '| "post_abnormal_change" | 异常变化后 | on_abnormal_changed |\n'
            '| "post_abnormal_apply" | 异常施加后 | on_abnormal_applied |\n'
            '| "post_energy_change" | 能量变化后 | on_skills_energy_changed / on_energy_changed |\n'
            '| "post_positive_change" | 增益变化后 | on_positive_changed |\n'
            '| "turn_end" | 回合结束时 | turn_end |\n\n'

            '# Scope 参考\n'
            '| scope | 含义 | 离场 | 力竭 | 适用场景 |\n'
            '|-------|------|------|------|---------|\n'
            '| "battlefield" | 在场有效 | 消失 | 消失 | 光环类效果 |\n'
            '| "persistent" | 跨回合持久 | 消失 | 消失 | 入场/动作触发效果（最常用）|\n'
            '| "permanent" | 永久 | 保留 | 保留 | 永久成长/全局被动 |\n'
            '| "turn" | 当回合 | 消失 | 消失 | 单回合临时效果 |\n'
            '- observer 自身的 scope 和 then 内部 op 的 scope 可以不同\n'
            '- 例：observer.scope="persistent"（observer持久存在）+ op.scope="permanent"（效果永久）\n\n'

            '# 条件参考（cond 常用类型）\n'
            '**事件触发类：**\n'
            '- {"cond": "sprite_entered", "of": "sprite_self"} — 己方入场\n'
            '- {"cond": "sprite_entered", "of": "sprite_opp"} — 敌方入场\n'
            '- {"cond": "sprite_left", "of": "sprite_self"} — 己方离场\n'
            '- {"cond": "sprite_left", "of": "sprite_opp"} — 敌方离场\n'
            '- {"cond": "sprite_acted", "of": "sprite_self"} — 己方行动后\n'
            '- {"cond": "skill_use"} — 任意技能使用后\n'
            '- {"cond": "skill_use", "element": "火"} — 使用火系技能后\n'
            '- {"cond": "skill_use", "energy_cost": 3} — 使用能耗=3的技能后\n'
            '- {"cond": "on_self_ko"} — 自己力竭时\n'
            '- {"cond": "on_ko"} — 任意精灵力竭时\n'
            '- {"cond": "on_damage_taken"} — 受到伤害时\n'
            '- {"cond": "counter_succeeded"} — 应对成功时\n'
            '- {"cond": "opp_switched"} — 敌方切换时\n'
            '- {"cond": "turn_end"} — 回合结束时\n'
            '- {"cond": "on_abnormal_tick", "name": "中毒"} — 中毒tick时\n'
            '- {"cond": "team_has_element", "element": "虫"} — 队伍存在虫系\n\n'
            '**状态检查类（通常与事件条件用 and 组合）：**\n'
            '- {"cond": "have_skill_of", "of": "sprite_opp", "element": {"q": "element", "of": "skill_off_0"}} — 敌方持有攻击技能系别\n'
            '- {"cond": "weather_is", "weather": "rain"} — 天气为雨天\n'
            '- {"cond": "compare", "q": "hp_ratio", "of": "sprite_self", "op": "lt", "value": 0.5} — HP低于50%\n'
            '- {"cond": "compare", "q": "energy", "of": "sprite_self", "op": "gte", "value": 0} — 始终为真(无条件触发)\n'
            '- {"cond": "compare", "q": "abnormal_stacks", "of": "sprite_opp", "name": "中毒", "op": "gte", "value": 1} — 敌方有中毒\n\n'
            '**逻辑组合：**\n'
            '- {"cond": "and", "conditions": [...]} — 全部满足\n'
            '- {"cond": "or", "conditions": [...]} — 任一满足\n'
            '- {"cond": "not", "condition": {...}} — 取反\n\n'

            '# then[] 常用 opcode\n'
            '| op | 说明 | 关键字段 |\n'
            '|----|------|---------|\n'
            '| stat_stage | 修改能力等级 | stat(stat名), steps(±1=±10%), target, scope |\n'
            '| mult_mod | 修改倍率 | attr(atk/def/power_mult/damage_reduction/...), value, mode(add/mult), target |\n'
            '| power_mod | 修改技能属性 | attr(power/combo/energy_cost/...), delta, target |\n'
            '| flag_set | 设置标记 | flag, value, target |\n'
            '| heal | 回复生命 | ratio(0.0~1.0) 或 value(绝对值), target |\n'
            '| energize | 回复/扣除能量 | delta(正=回复,负=扣除), target |\n'
            '| revive | 复活 | hp_ratio, delay_turns, target |\n'
            '| abnormal | 施加异常 | name, stacks, target |\n'
            '| mark | 施加印记 | name, stacks, target |\n'
            '| dispel | 驱散效果 | target |\n'
            '| inherit | 继承效果 | source, inherit_target(enemy_new/ally_new), target |\n'
            '| transform | 变形 | species(目标物种), target |\n'
            '| exchange | 交换 | what("hp_ratio"/"effects"), target |\n'
            '| reset | 重置属性 | stat, target |\n'
            '| redirect | 重定向 | target |\n'
            '| charge | 蓄力 | target |\n'
            '| replay | 重放技能 | from, skill_filter |\n'
            '| borrow | 借用技能 | from("skill_opp_current") |\n'
            '| defer | 延迟执行 | turns, at("turn_start"/"turn_end"), then[] |\n'
            '| branch | 条件分支 | cond, then[], else[], else_if[] |\n'
            '- 每条 then 内的 op 必须包含 `target` 字段："sprite_self"(己方) / "sprite_opp"(敌方) / "target"(技能目标) / "skill_off_0"(攻击技能) / "self"(己方别名)\n'
            '- =@表达式：`"steps": "=@player_fainted_count * 3"` (运行时求值)\n'
            '- Query查询：`"value": {"q": "abnormal_stacks", "of": "sprite_opp", "name": "中毒"}`\n\n'

            '# 典型示例\n\n'

            '## 例1：入场加攻\n'
            'description: "入场时获得物攻+100%"\n'
            '→ effects: [{"op":"observer","cond":{"cond":"sprite_entered","of":"sprite_self"},"listen":"post_entry","scope":"battlefield","then":[{"op":"mult_mod","target":"sprite_self","attr":"atk","value":1,"mode":"add"}]}]\n\n'

            '## 例2：敌方入场debuff\n'
            'description: "敌方入场时，全技能能耗+1"\n'
            '→ effects: [{"op":"observer","cond":{"cond":"sprite_entered","of":"sprite_opp"},"listen":"post_entry","scope":"battlefield","then":[{"op":"power_mod","target":"sprite_opp","attr":"energy_cost","delta":1}]}]\n\n'

            '## 例3：条件减伤\n'
            'description: "受到自己携带技能系别的攻击伤害-40%"\n'
            '→ effects: [{"op":"observer","cond":{"cond":"have_skill_of","of":"sprite_opp","element":{"q":"element","of":"skill_off_0"}},"listen":"pre_calc","scope":"battlefield","then":[{"op":"mult_mod","target":"sprite_opp","attr":"damage_reduction","value":0.4,"mode":"add"}]}]\n\n'

            '## 例4：使用技能触发\n'
            'description: "使用火系技能后，获得双攻+30%"\n'
            '→ effects: [{"op":"observer","cond":{"cond":"and","conditions":[{"cond":"skill_use"},{"cond":"skill_use","element":"火"}]},"listen":"post_skill","scope":"persistent","then":[{"op":"stat_stage","target":"sprite_self","stat":"atk","steps":3,"scope":"battlefield"},{"op":"stat_stage","target":"sprite_self","stat":"sp_atk","steps":3,"scope":"battlefield"}]}]\n\n'

            '## 例5：力竭复活\n'
            'description: "力竭3回合后复活"\n'
            '→ effects: [{"op":"observer","cond":{"cond":"on_self_ko"},"listen":"post_ko","scope":"persistent","then":[{"op":"revive","target":"sprite_self","hp_ratio":1,"delay_turns":3}]}]\n\n'

            '## 例6：离场继承\n'
            'description: "离场后，自己的增益/减益被换上来的精灵继承"\n'
            '→ effects: [{"op":"observer","cond":{"cond":"sprite_left","of":"sprite_self"},"listen":"post_leave","scope":"persistent","then":[{"op":"inherit","target":"ally_new","source":"sprite_self","inherit_stat_effects":true}]}]\n\n'

            '## 例7：回合末回复\n'
            'description: "回合结束时，回复12%生命"\n'
            '→ effects: [{"op":"observer","cond":{"cond":"turn_end"},"listen":"turn_end","scope":"persistent","then":[{"op":"heal","target":"sprite_self","ratio":0.12}]}]\n\n'

            '## 例8：多Observer（入场buff + 每次行动衰减）\n'
            'description: "入场时获得物攻+100%，每次行动后物攻-20%"\n'
            '→ effects: [{"op":"observer","cond":{"cond":"sprite_entered","of":"sprite_self"},"listen":"post_entry","scope":"battlefield","then":[{"op":"mult_mod","target":"sprite_self","attr":"atk","value":1,"mode":"add"}]},{"op":"observer","cond":{"cond":"sprite_acted","of":"sprite_self"},"listen":"post_skill","scope":"battlefield","then":[{"op":"mult_mod","target":"sprite_self","attr":"atk","value":-0.2,"mode":"add"}]}]\n\n'

            '## 例9：敌方离场触发\n'
            'description: "敌方精灵离场后，更换入场的精灵失去3能量"\n'
            '→ effects: [{"op":"observer","cond":{"cond":"sprite_left","of":"sprite_opp"},"listen":"post_enemy_leave","scope":"battlefield","then":[{"op":"energize","target":"sprite_opp","delta":-3}]}]\n\n'

            '# 关键规则（必须遵守）\n'
            '1. **effects[] 每项外层必须包裹 observer op**。永远不要在 effects[] 中直接放裸 opcode（如 stat_stage/mult_mod/heal），这些只能出现在 observer 的 then[] 内部。\n'
            '2. 每个 observer 必须包含全部 5 个字段：`op`, `cond`, `then`, `listen`, `scope`。\n'
            '3. `then[]` 中的每条 op 必须包含 `target` 字段，指明效果对象。\n'
            '4. `listen` 值必须与 `cond` 匹配。sprite_entered→post_entry；turn_end→turn_end；skill_use→post_skill；on_self_ko→post_ko；等等。\n'
            '5. 如果 description 包含多个独立效果（如"入场时X，行动后Y"），用多个 observer 分别表示。\n'
            '6. 保留 `id`, `name`, `description` 不变。保留 `source` 字段（如存在）。\n'
            '7. 输出**仅**完整的 JSON（含生成的 effects[]）。不要 markdown 代码块标记，不要任何解释文字。\n\n'

            '# 参考: IR_RISC 规范全文（opcode 字段级细节）\n'
            + ir_risc_text[:30000] + '\n\n'

            '# 需要处理的 Trait JSON\n'
            '```json\n' + trait_json + '\n```\n\n'
            '**只输出**完整的 JSON（含生成的 effects[]）。不要 markdown 代码块，不要任何解释。'
        )

        try:
            # 将 prompt 通过 stdin 传入，避免命令行参数过长
            result = subprocess.run(
                [CLAUDE_CLI, '--print'],
                input=prompt, capture_output=True, text=True, encoding='utf-8', timeout=120,
                cwd=str(Path(__file__).resolve().parent.parent.parent),
            )
            output = result.stdout.strip()
            if result.returncode != 0:
                err = result.stderr.strip() or f'exit code {result.returncode}'
                self._send_json({'error': f'Claude 调用失败: {err}', 'raw': output or ''}, 500)
                return

            # Extract JSON from output (handle markdown fences)
            if output.startswith('```'):
                # Remove opening fence line (``` or ```json)
                output = output.split('\n', 1)[1] if '\n' in output else output[3:]
            if output.rstrip().endswith('```'):
                # Remove closing fence
                output = output[:output.rstrip().rfind('```')].rstrip()

            try:
                json.loads(output)
            except json.JSONDecodeError:
                self._send_json({'error': 'Claude 返回的不是有效 JSON', 'raw': output[:2000]}, 500)
                return

            self._send_json({'ok': True, 'content': output, 'filename': fpath.name})
        except FileNotFoundError:
            self._send_json({'error': f'claude CLI 未找到 (尝试路径: {CLAUDE_CLI})，请确认已安装 Claude Code'}, 500)
        except subprocess.TimeoutExpired:
            self._send_json({'error': 'Claude 调用超时 (120s)'}, 500)

    def _serve_html(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(HTML.encode('utf-8'))


# ── HTML UI ──────────────────────────────────────────────────────

HTML = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>特性 JSON 编辑器 — 格斗小九</title>
<style>
:root {
  --bg: #0a0c10;
  --surface: #12151e;
  --border: #1e2230;
  --text: #c8ccd6;
  --dim: #5c6170;
  --accent: #5eb0e8;
  --fire: #e0554a;
  --water: #4a90d9;
  --ice: #6dd4e8;
  --earth: #c49a4a;
  --elec: #e0c040;
  --mech: #98a8b8;
  --neutral: #a0a4ac;
  --light: #e8d840;
  --illusion: #c080d0;
  --ghost: #8070c0;
  --evil: #585868;
  --dragon: #e08840;
  --grass: #60b860;
  --bug: #90b840;
  --wing: #a0b8e8;
  --poison: #a860b8;
  --fight: #c87050;
  --moe: #f0a0b0;
  --good: #40b880;
  --danger: #e04050;
  --warn: #e0a030;
  --info: #5090d8;
  --font-mono: 'Cascadia Code','Fira Code','JetBrains Mono','Consolas',monospace;
  --font-ui: 'Segoe UI','PingFang SC','Microsoft YaHei',system-ui,sans-serif;
}
* { margin:0; padding:0; box-sizing:border-box; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-ui);
  height: 100vh;
  display: flex;
  overflow: hidden;
  user-select: none;
}
body::before {
  content: '';
  position: fixed;
  inset: 0;
  background:
    radial-gradient(ellipse at 20% 50%, rgba(94,176,232,0.03) 0%, transparent 50%),
    radial-gradient(ellipse at 80% 20%, rgba(192,128,208,0.03) 0%, transparent 50%);
  pointer-events: none;
  z-index: 0;
}

/* ── Sidebar ─────────────────────────────────── */
#sidebar {
  width: 300px;
  min-width: 300px;
  background: var(--surface);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  z-index: 1;
}
#sidebar-header {
  padding: 16px;
  border-bottom: 1px solid var(--border);
}
#sidebar-header h1 {
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--accent);
  text-transform: uppercase;
}
#sidebar-header .subtitle {
  font-size: 10px;
  color: var(--dim);
  margin-top: 2px;
}
#search-box {
  margin: 10px 16px;
  padding: 8px 12px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text);
  font-family: var(--font-ui);
  font-size: 12px;
  outline: none;
  transition: border-color 0.2s;
}
#search-box:focus { border-color: var(--accent); }
#search-box::placeholder { color: var(--dim); }

/* stats bar */
#stats-bar {
  display: flex;
  gap: 8px;
  padding: 0 16px 8px;
  font-size: 10px;
  color: var(--dim);
}
#stats-bar span { background: var(--bg); padding: 3px 8px; border-radius: 4px; }

/* file list */
#file-list {
  flex: 1;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--border) transparent;
}
.file-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 16px;
  cursor: pointer;
  border-bottom: 1px solid color-mix(in srgb, var(--border) 40%, transparent);
  transition: background 0.15s;
  font-size: 12px;
}
.file-item:hover { background: rgba(255,255,255,0.03); }
.file-item.active { background: rgba(94,176,232,0.08); border-left: 2px solid var(--accent); }

.file-item .id-dot {
  width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
  background: var(--accent);
}
.file-item .info { flex:1; min-width:0; }
.file-item .info .name { font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.file-item .info .meta {
  font-size: 10px; color: var(--dim); display: flex; gap: 6px;
}
.file-item .info .desc-preview {
  font-size: 10px; color: var(--dim); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  max-width: 220px; margin-top: 1px;
}
.file-item .badge {
  font-size: 9px; padding: 1px 5px; border-radius: 3px;
  background: var(--bg); color: var(--dim); flex-shrink: 0;
}
.file-item .badge.modified { background: rgba(94,176,232,0.15); color: var(--accent); }
.file-item .badge.error { background: rgba(224,64,80,0.15); color: var(--danger); }
.file-item.saved { background: rgba(64,184,128,0.06); }
.file-item.saved::after {
  content: '\2713';
  font-size: 10px;
  color: var(--good);
  font-weight: 700;
  margin-left: auto;
  padding-left: 8px;
}

/* ── Main panel ──────────────────────────────── */
#main {
  flex: 1;
  display: flex;
  flex-direction: column;
  z-index: 1;
  min-width: 0;
}
#toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 20px;
  border-bottom: 1px solid var(--border);
  background: color-mix(in srgb, var(--surface) 60%, var(--bg));
}
#toolbar .file-title {
  font-weight: 700; font-size: 15px; flex: 1;
}
#toolbar .file-path {
  font-size: 10px; color: var(--dim); font-family: var(--font-mono);
}
#toolbar button {
  padding: 7px 18px;
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
  font-family: var(--font-ui);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  transition: all 0.15s;
}
#btn-save {
  background: var(--accent);
  color: #0a0c10;
  border-color: var(--accent);
}
#btn-save:hover { filter: brightness(1.15); }
#btn-save:disabled { opacity: 0.3; cursor: not-allowed; filter: none; }
#btn-reload {
  background: transparent;
  color: var(--text);
}
#btn-reload:hover { border-color: var(--dim); background: rgba(255,255,255,0.04); }
#btn-pretty {
  background: transparent;
  color: var(--dim);
  font-weight: 400;
}
#btn-pretty:hover { color: var(--text); border-color: var(--dim); }

#status-msg {
  font-size: 11px; font-weight: 600;
  padding: 4px 10px; border-radius: 4px;
  opacity: 0; transition: opacity 0.2s;
}
#status-msg.show { opacity: 1; }
#status-msg.ok { color: var(--good); }
#status-msg.err { color: var(--danger); }

/* editor */
#editor-wrap {
  flex: 1;
  display: flex;
  overflow: hidden;
  position: relative;
}
#line-numbers {
  width: 56px;
  padding: 14px 0;
  background: color-mix(in srgb, var(--surface) 50%, var(--bg));
  border-right: 1px solid var(--border);
  text-align: right;
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.6;
  color: var(--dim);
  overflow: hidden;
  user-select: none;
}
#line-numbers div { padding-right: 14px; }
#json-editor {
  flex: 1;
  padding: 14px 20px;
  background: transparent;
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.6;
  border: none;
  outline: none;
  resize: none;
  tab-size: 2;
  white-space: pre;
  overflow-wrap: normal;
  overflow-x: auto;
}
#json-editor:focus { background: rgba(255,255,255,0.006); }

/* empty state */
#empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--dim);
  gap: 10px;
}
#empty-state .icon { font-size: 48px; opacity: 0.3; }
#empty-state p { font-size: 13px; }

/* toast */
#toast {
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  padding: 10px 24px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.03em;
  z-index: 100;
  opacity: 0;
  transition: opacity 0.3s, transform 0.3s;
  pointer-events: none;
}
#toast.show { opacity: 1; transform: translateX(-50%) translateY(-4px); }
#toast.ok { background: var(--good); color: #060f08; }
#toast.err { background: var(--danger); color: #fff; }

/* keyboard hint */
#kbd-hint {
  position: fixed;
  bottom: 12px;
  right: 20px;
  font-size: 10px;
  color: var(--dim);
  z-index: 10;
  pointer-events: none;
}
#kbd-hint kbd {
  display: inline-block;
  padding: 1px 5px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 3px;
  font-family: var(--font-mono);
  font-size: 9px;
  margin: 0 2px;
}
</style>
</head>
<body>

<!-- Sidebar -->
<aside id="sidebar">
  <div id="sidebar-header">
    <h1>特性编辑器</h1>
    <div class="subtitle">Trait JSON Editor · 格斗小九</div>
  </div>
  <input id="search-box" type="text" placeholder="搜索特性名、描述、ID..." autocomplete="off">
  <div id="stats-bar">
    <span id="stat-total">总计: 0</span>
    <span id="stat-filtered">显示: 0</span>
    <span id="stat-saved" style="display:none;color:var(--good)">已保存: 0</span>
  </div>
  <div id="file-list"></div>
</aside>

<!-- Main -->
<main id="main">
  <div id="toolbar">
    <div>
      <div class="file-title" id="current-name">选择一个特性</div>
      <div class="file-path" id="current-path"></div>
    </div>
    <span id="status-msg"></span>
    <div style="flex:1"></div>
    <button id="btn-pretty" title="格式化 JSON">格式化</button>
    <button id="btn-convert" title="调用 Claude 根据 description 生成 effects[]">生成 effects</button>
    <button id="btn-reload">重置</button>
    <button id="btn-save" disabled>保存</button>
  </div>

  <div id="empty-state">
    <div class="icon">&#9678;</div>
    <p>从左侧列表选择一个特性开始编辑</p>
    <p style="font-size:11px;opacity:0.5">Ctrl+S 保存 · 点击文件名加载</p>
  </div>

  <div id="editor-wrap" style="display:none">
    <div id="line-numbers"></div>
    <textarea id="json-editor" spellcheck="false" wrap="off"></textarea>
  </div>
</main>

<div id="toast"></div>
<div id="kbd-hint">
  <kbd>Ctrl+S</kbd> 保存 &nbsp; <kbd>&uarr;</kbd><kbd>&darr;</kbd> 切换文件
</div>

<script>
// ── State ──────────────────────────────────────
let traits = [];
let currentFile = null;
let originalContent = '';
let modified = false;
let savedFiles = new Set();

const $list = document.getElementById('file-list');
const $search = document.getElementById('search-box');
const $editor = document.getElementById('json-editor');
const $lineNums = document.getElementById('line-numbers');
const $editorWrap = document.getElementById('editor-wrap');
const $empty = document.getElementById('empty-state');
const $btnSave = document.getElementById('btn-save');
const $btnConvert = document.getElementById('btn-convert');
const $status = document.getElementById('status-msg');
const $toast = document.getElementById('toast');
const $curName = document.getElementById('current-name');
const $curPath = document.getElementById('current-path');
const $statTotal = document.getElementById('stat-total');
const $statFiltered = document.getElementById('stat-filtered');
const $statSaved = document.getElementById('stat-saved');

// ── Toast ─────────────────────────────────────
let _toastTimer;
function toast(msg, type) {
  clearTimeout(_toastTimer);
  $toast.textContent = msg;
  $toast.className = type + ' show';
  _toastTimer = setTimeout(() => $toast.className = '', 2000);
}

// ── Load list ─────────────────────────────────
async function loadList() {
  const res = await fetch('/api/traits');
  traits = await res.json();
  renderList();
}
loadList();

// ── Render ────────────────────────────────────
function renderList() {
  const q = $search.value.toLowerCase();
  const filtered = traits.filter(s => {
    if (!q) return true;
    return (s.name + s.description + String(s.id) + s.filename).toLowerCase().includes(q);
  });

  $statTotal.textContent = '总计: ' + traits.length;
  $statFiltered.textContent = '显示: ' + filtered.length;
  if (savedFiles.size > 0) {
    $statSaved.style.display = '';
    $statSaved.textContent = '已保存: ' + savedFiles.size;
  } else {
    $statSaved.style.display = 'none';
  }

  $list.innerHTML = filtered.map((s, i) => {
    let badges = '';
    if (s.filename === currentFile) badges += '<span class="badge modified">当前</span>';
    let rowCls = s.filename === currentFile ? ' active' : '';
    if (savedFiles.has(s.filename)) rowCls += ' saved';
    return `<div class="file-item${rowCls}" data-file="${_esc(s.filename)}" data-index="${i}">
      <div class="id-dot"></div>
      <div class="info">
        <div class="name">${_esc(s.name)}</div>
        <div class="meta">
          <span>ID:${s.id}</span>
          <span>${s.triggers_count} trigger</span>
        </div>
        <div class="desc-preview">${_esc(s.description)}</div>
      </div>
      ${badges}
    </div>`;
  }).join('');

  $list.querySelectorAll('.file-item').forEach(el => {
    el.addEventListener('click', () => {
      const fname = el.dataset.file;
      if (fname) loadFile(fname);
    });
  });
}

function _esc(s) { return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// ── Load file ─────────────────────────────────
async function loadFile(filename) {
  if (modified && !confirm('当前文件有未保存的修改，确定要切换吗？')) return;

  const name = filename.replace('.json','');
  const res = await fetch('/api/trait/' + encodeURIComponent(name));
  const data = await res.json();
  if (data.error) { toast(data.error, 'err'); return; }

  currentFile = filename;
  originalContent = data.content;
  modified = false;

  $editor.value = data.content;
  $editorWrap.style.display = 'flex';
  $empty.style.display = 'none';
  $btnSave.disabled = true;
  $status.className = '';
  $status.textContent = '';

  $curName.textContent = name;
  $curPath.textContent = 'data/traits/' + filename;

  updateLineNumbers();
  renderList();
}

// ── Line numbers ──────────────────────────────
function updateLineNumbers() {
  const lines = $editor.value.split('\n');
  let html = '';
  for (let i = 0; i < lines.length; i++) {
    html += '<div>' + (i + 1) + '</div>';
  }
  $lineNums.innerHTML = html;
}

$editor.addEventListener('input', () => {
  updateLineNumbers();
  if ($editor.value !== originalContent) {
    if (!modified) {
      modified = true;
      $btnSave.disabled = false;
    }
  } else {
    modified = false;
    $btnSave.disabled = true;
  }
});

$editor.addEventListener('scroll', () => {
  $lineNums.scrollTop = $editor.scrollTop;
});

// ── Save ──────────────────────────────────────
async function saveFile() {
  if (!currentFile || !modified) return;

  const content = $editor.value;
  try {
    JSON.parse(content);
  } catch (e) {
    toast('JSON 格式错误: ' + e.message, 'err');
    return;
  }

  const name = currentFile.replace('.json','');
  const res = await fetch('/api/trait/' + encodeURIComponent(name), {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: content,
  });
  const data = await res.json();
  if (data.ok) {
    originalContent = content;
    modified = false;
    $btnSave.disabled = true;
    savedFiles.add(currentFile);
    toast('✓ ' + name + ' 已保存', 'ok');
    renderList();
  } else {
    toast(data.error || '保存失败', 'err');
  }
}

// ── Button events ─────────────────────────────
document.getElementById('btn-save').addEventListener('click', saveFile);
document.getElementById('btn-reload').addEventListener('click', () => {
  if (currentFile && (modified ? confirm('确定要放弃修改吗？') : true)) {
    loadFile(currentFile);
  }
});

// ── Format ─────────────────────────────────────
const TOP_ORDER = ['id', 'name', 'description', 'effects', 'triggers'];
const TRIGGER_ORDER = ['on', 'condition', 'use_modifiers', 'effects', 'battleskill_mut'];

function formatTrait(raw) {
  const obj = (typeof raw === 'string') ? JSON.parse(raw) : raw;

  const ordered = {};
  for (const k of TOP_ORDER) {
    if (k in obj) ordered[k] = obj[k];
  }
  for (const k of Object.keys(obj)) {
    if (!(k in ordered)) ordered[k] = obj[k];
  }

  if (Array.isArray(ordered.triggers)) {
    ordered.triggers = ordered.triggers.map(normalizeTrigger);
  }

  return JSON.stringify(ordered, null, 2) + '\n';
}

function normalizeTrigger(trg) {
  const out = {};
  for (const k of TRIGGER_ORDER) {
    if (k in trg) out[k] = trg[k];
  }
  for (const k of Object.keys(trg)) {
    if (!(k in out)) out[k] = trg[k];
  }
  if (Array.isArray(out.effects)) {
    out.effects = out.effects.map(e => e);
  }
  return out;
}

document.getElementById('btn-pretty').addEventListener('click', () => {
  try {
    const formatted = formatTrait($editor.value);
    $editor.value = formatted;
    updateLineNumbers();
    if (formatted !== originalContent) {
      modified = true; $btnSave.disabled = false;
    }
  } catch (e) {
    toast('JSON 格式错误，无法格式化: ' + e.message, 'err');
  }
});

// ── Convert (Claude RISC IR) ──────────────────
$btnConvert.addEventListener('click', async () => {
  if (!currentFile) { toast('请先选择一个特性', 'err'); return; }

  $btnConvert.disabled = true;
  $btnConvert.textContent = '生成中…';
  $status.textContent = '⏳ Claude 正在生成…';
  const convertFile = currentFile;
  const content = $editor.value;
  try {
    const res = await fetch('/api/trait/' + encodeURIComponent(convertFile.replace('.json','')) + '/convert', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: content,
    });
    const data = await res.json();
    if (currentFile !== convertFile) return;  // user switched away
    if (data.ok) {
      $editor.value = data.content;
      originalContent = data.content;
      modified = true;
      $btnSave.disabled = false;
      updateLineNumbers();
      toast('✓ 生成完成，点击保存以写入文件', 'ok');
      $status.textContent = '生成完成';
    } else {
      if (data.raw && data.raw !== content) {
        $editor.value = data.raw;
        updateLineNumbers();
      }
      toast(data.error || '生成失败', 'err');
      $status.textContent = '生成失败';
    }
  } catch (e) {
    toast('网络错误: ' + e.message, 'err');
    $status.textContent = '';
  } finally {
    $btnConvert.disabled = false;
    $btnConvert.textContent = '生成 effects';
  }
});

// ── Search ────────────────────────────────────
$search.addEventListener('input', renderList);

// ── Keyboard shortcuts ────────────────────────
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault();
    saveFile();
  }
});

// ── Sync scroll ───────────────────────────────
$editor.addEventListener('scroll', () => {
  $lineNums.scrollTop = $editor.scrollTop;
});
</script>
</body>
</html>'''


# ── Entry ─────────────────────────────────────────────────────────

def main():
    port = 8766
    for a in sys.argv[1:]:
        if a.startswith('--port='):
            port = int(a.split('=')[1])

    server = HTTPServer(('127.0.0.1', port), Handler)
    url = f'http://127.0.0.1:{port}'
    print(f'特性编辑器已启动: {url}')
    print('按 Ctrl+C 退出')
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n已退出')
        server.shutdown()


if __name__ == '__main__':
    main()
