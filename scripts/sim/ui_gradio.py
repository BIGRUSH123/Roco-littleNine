"""scripts/sim/ui_gradio.py — Gradio 前端 UI for 模拟对战

用法:
  python scripts/sim/ui_gradio.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import gradio as gr

# 确保项目根在 sys.path
BASE = Path(__file__).resolve().parent.parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from scripts.sim.factory import SimFactory
from scripts.sim.agent import RuleAgent
from scripts.sim.battle import Battle
from scripts.sim.player import PlayStyle

WIKI_ROOT = BASE / "wiki"
SKILLS_DIR = BASE / "data" / "skills"


# ═══════════════════════════════════════════════════════════════════
# 数据加载（启动时一次性）
# ═══════════════════════════════════════════════════════════════════

def _load_sprite_skills() -> list[tuple[str, list[str]]]:
    """从 wiki/精灵图鉴 提取 (精灵名, [技能列表])。"""
    available = {p.stem for p in SKILLS_DIR.glob("*.json")}
    entries: list[tuple[str, list[str]]] = []

    sprite_dir = WIKI_ROOT / "精灵图鉴"
    if not sprite_dir.is_dir():
        return entries

    for md in sorted(sprite_dir.rglob("*.md")):
        if md.name.startswith("_") or md.stem == "index":
            continue
        text = md.read_text(encoding="utf-8", errors="ignore")

        name_m = re.search(r'^name:\s*"(.+?)"', text, re.MULTILINE)
        sprite_name = name_m.group(1) if name_m else md.stem

        skills: list[str] = []
        in_section = False
        for line in text.split("\n"):
            if re.match(r"## 技能", line):
                in_section = True
                continue
            if in_section:
                if line.startswith("## "):
                    break
                m = re.search(r"\[(?:[*]*)([^]]+?)(?:[*]*)\]\(([^)]+)\)", line)
                if m:
                    name = m.group(1).strip("*")
                    if name in available:
                        skills.append(name)

        if skills:
            entries.append((sprite_name, skills))

    return entries


_sprite_entries = _load_sprite_skills()
SPRITE_NAMES = [name for name, _ in _sprite_entries]
SKILL_MAP = dict(_sprite_entries)
_factory = SimFactory()


# ═══════════════════════════════════════════════════════════════════
# HTML 渲染
# ═══════════════════════════════════════════════════════════════════

def _hp_bar(current: int, maximum: int) -> str:
    if maximum <= 0:
        pct = 0.0
    else:
        pct = max(0.0, min(100.0, current / maximum * 100))

    if pct > 50:
        color = "#22c55e"
    elif pct > 25:
        color = "#f59e0b"
    else:
        color = "#ef4444"

    return (
        f'<div style="background:#1e293b;border-radius:4px;height:12px;width:100%;overflow:hidden;">'
        f'<div style="background:{color};height:100%;width:{pct:.0f}%;'
        f'transition:width .4s;border-radius:4px;"></div></div>'
    )


def _format_effects(sprite) -> str:
    if not sprite.effects:
        return ""
    parts: list[str] = []
    for e in sprite.effects:
        if e.category == "abnormal":
            parts.append(f'<span style="color:#f87171">{e.name}&times;{e.stacks}</span>')
        elif e.is_stat:
            c = "#4ade80" if e.steps > 0 else "#f87171"
            parts.append(f'<span style="color:{c}">{e.name}</span>')
        else:
            parts.append(f'<span style="color:#a78bfa">{e.name}</span>')
    return (
        '<div style="margin-top:2px;font-size:11px;">'
        f'效果: {", ".join(parts)}</div>'
    )


def _format_marks(battle, team: str) -> str:
    pos, neg = battle.globals.get_marks(team)
    parts = []
    if pos:
        parts.append(f"+{pos.name}&times;{pos.stacks}")
    if neg:
        parts.append(f"-{neg.name}&times;{neg.stacks}")
    if parts:
        return f'<div style="font-size:11px;color:#fbbf24;margin-top:2px;">印记: {", ".join(parts)}</div>'
    return ""


def _format_team(player) -> str:
    parts = []
    for i, s in enumerate(player.team):
        prefix = "&#9654;" if i == player.active_index else ""
        if s.is_fainted:
            status = "&#128128;"
        else:
            status = f"{s.current_hp}HP"
        parts.append(f'{prefix}{s.name}({status})')
    return "  ".join(parts)


def _action_short(s: str) -> str:
    if s.startswith("skill:"):
        return s[6:]
    if s.startswith("switch:"):
        return "↓" + s[7:]
    return s


def render_battle_html(battle: Battle) -> str:
    pa, pb = battle.player_a, battle.player_b
    sa, sb = pa.active, pb.active
    g = battle.globals

    weather_str = f"天气: {g.weather} ({g.weather_turns}t)" if g.weather else ""

    return f"""
<div style="display:flex;gap:12px;font-family:'Segoe UI',system-ui,sans-serif;font-size:13px;">

  <!-- 我方 -->
  <div style="flex:1;background:#0f172a;border:2px solid #3b82f6;border-radius:10px;padding:14px;">
    <div style="font-size:16px;font-weight:bold;color:#60a5fa;margin-bottom:6px;">&#9876; {pa.name}</div>
    <div style="font-size:18px;color:#f1f5f9;margin-bottom:2px;">
      {sa.name} {"&#128128;" if sa.is_fainted else ""}
    </div>
    <div style="color:#94a3b8;font-size:11px;margin-bottom:4px;">
      HP {sa.current_hp}/{sa.max_hp}
    </div>
    {_hp_bar(sa.current_hp, sa.max_hp)}
    <div style="margin-top:6px;color:#fbbf24;">&#9889; 能量: {sa.energy}/10</div>
    {_format_effects(sa)}
    {_format_marks(battle, 'A')}
    <div style="margin-top:8px;font-size:11px;color:#64748b;word-break:break-all;">
      {_format_team(pa)}
    </div>
  </div>

  <!-- 中间 -->
  <div style="flex:0 0 100px;display:flex;flex-direction:column;justify-content:center;
              align-items:center;color:#94a3b8;">
    <div style="font-size:22px;font-weight:bold;">VS</div>
    <div style="font-size:12px;margin-top:6px;">回合 {battle.turn}</div>
    <div style="font-size:11px;color:#a78bfa;margin-top:4px;">{weather_str}</div>
  </div>

  <!-- 对方 -->
  <div style="flex:1;background:#0f172a;border:2px solid #ef4444;border-radius:10px;padding:14px;">
    <div style="font-size:16px;font-weight:bold;color:#f87171;margin-bottom:6px;">&#128737; {pb.name}</div>
    <div style="font-size:18px;color:#f1f5f9;margin-bottom:2px;">
      {sb.name} {"&#128128;" if sb.is_fainted else ""}
    </div>
    <div style="color:#94a3b8;font-size:11px;margin-bottom:4px;">
      HP {sb.current_hp}/{sb.max_hp}
    </div>
    {_hp_bar(sb.current_hp, sb.max_hp)}
    <div style="margin-top:6px;color:#fbbf24;">&#9889; 能量: {sb.energy}/10</div>
    {_format_effects(sb)}
    {_format_marks(battle, 'B')}
    <div style="margin-top:8px;font-size:11px;color:#64748b;word-break:break-all;">
      {_format_team(pb)}
    </div>
  </div>

</div>"""


def render_log(battle: Battle | None) -> str:
    if battle is None:
        return "*点击「开始对战」...*"
    if not battle.log:
        return "*对局开始，点击「下一回合」...*"

    lines: list[str] = []
    for r in battle.log[-30:]:
        a = _action_short(r.action_a)
        b = _action_short(r.action_b)

        line = f"**T{r.turn}** [{r.first_team}先] {a} vs {b}"
        if r.events:
            key = [e for e in r.events if "HP" in e or "力竭" in e or "脱离" in e]
            shown = key if key else r.events[:2]
            line += f" — *{'; '.join(shown)}*"
        line += f"  \n`A:{r.sprite_a_hp}HP/{r.sprite_a_energy}E  B:{r.sprite_b_hp}HP/{r.sprite_b_energy}E`"
        lines.append(line)

    return "\n\n".join(lines) if lines else "*无日志*"


def _result_text(battle: Battle | None) -> str:
    if battle is None or not battle.is_finished:
        return ""
    if battle.winner == "A":
        return "## &#127942; 我方胜利!"
    if battle.winner == "B":
        return "## &#128128; AI 胜利!"
    return "## &#129309; 平局"


# ═══════════════════════════════════════════════════════════════════
# 对战逻辑
# ═══════════════════════════════════════════════════════════════════

_AI_DEFAULTS = [
    ("水灵", "balanced"),
    ("火神", "balanced"),
    ("迪莫", "balanced"),
]


def start_battle(
    sprite_a1: str, skills_a1: str,
    sprite_a2: str, skills_a2: str,
    sprite_a3: str, skills_a3: str,
    sprite_b1: str, style_b1: str,
    sprite_b2: str, style_b2: str,
    sprite_b3: str, style_b3: str,
):
    """初始化对战：构建双方队伍 + 选择首发。"""
    # ── 我方 ──
    team_a: list[dict] = []
    for sprite, skills in [(sprite_a1, skills_a1), (sprite_a2, skills_a2), (sprite_a3, skills_a3)]:
        if not sprite:
            continue
        skill_list = list(skills) if skills else []
        if not skill_list:
            skill_list = SKILL_MAP.get(sprite, [])[:4]
        if skill_list:
            team_a.append({"name": sprite, "skills": skill_list[:4]})

    if not team_a:
        return None, "", "*请至少配置一只我方精灵*", gr.update(interactive=False), gr.update(), gr.update()

    # ── AI ──
    team_b: list[dict] = []
    for sprite, style in [(sprite_b1, style_b1), (sprite_b2, style_b2), (sprite_b3, style_b3)]:
        if not sprite:
            continue
        skill_list = SKILL_MAP.get(sprite, [])[:4]
        if skill_list:
            team_b.append({"name": sprite, "skills": skill_list})

    if not team_b:
        return None, "", "*请至少配置一只 AI 精灵*", gr.update(interactive=False), gr.update(), gr.update()

    # ── 构建 ──
    try:
        style_a = SimFactory.default_style("balanced")
        p_a = _factory.build_player("我方", team_a, style=style_a)
        ai_style_name = style_b1 or "balanced"
        p_b = _factory.build_player("AI", team_b, style=SimFactory.default_style(ai_style_name))
    except Exception as exc:
        return None, f"*创建队伍失败: {exc}*", "", gr.update(interactive=False), gr.update(), gr.update()

    battle = Battle(p_a, p_b)
    agent_a = RuleAgent("A", p_a)
    agent_b = RuleAgent("B", p_b)

    # 首发选择
    p_a.active_index = agent_a.choose_lead(battle)
    p_b.active_index = agent_b.choose_lead(battle)

    battle._agent_a = agent_a
    battle._agent_b = agent_b

    html = render_battle_html(battle)
    log = render_log(battle)

    return battle, html, log, gr.update(interactive=True), gr.update(visible=False), gr.update(visible=True)


def next_turn(battle: Battle | None):
    """执行一个回合。"""
    if battle is None or battle.is_finished:
        return (
            battle,
            render_battle_html(battle) if battle else "",
            render_log(battle),
            gr.update(interactive=False),
            _result_text(battle),
        )

    agent_a = battle._agent_a
    agent_b = battle._agent_b
    battle.execute_turn(agent_a, agent_b)

    html = render_battle_html(battle)
    log = render_log(battle)
    done = battle.is_finished

    return battle, html, log, gr.update(interactive=not done), _result_text(battle)


def auto_battle(battle: Battle | None, progress=gr.Progress()):
    """自动完成剩余回合。"""
    if battle is None or battle.is_finished:
        return (
            battle,
            render_battle_html(battle) if battle else "",
            render_log(battle),
            gr.update(interactive=False),
            _result_text(battle),
        )

    agent_a = battle._agent_a
    agent_b = battle._agent_b
    remaining = battle.MAX_TURNS - battle.turn
    progress(0, desc="自动对战中...")

    for i in range(remaining):
        if battle.is_finished:
            break
        battle.execute_turn(agent_a, agent_b)
        progress((i + 1) / remaining)

    return (
        battle,
        render_battle_html(battle),
        render_log(battle),
        gr.update(interactive=False),
        _result_text(battle),
    )


def on_sprite_change(sprite_name: str):
    """选精灵后自动填写技能下拉选项。"""
    if not sprite_name:
        return gr.update(choices=[], value=[])
    skills = SKILL_MAP.get(sprite_name, [])
    return gr.update(choices=skills, value=skills[:4])


# ═══════════════════════════════════════════════════════════════════
# UI 布局
# ═══════════════════════════════════════════════════════════════════

_CSS = """
.gradio-container { max-width: 1100px !important; }
footer { display: none !important; }
"""
_THEME = gr.themes.Soft(primary_hue="blue", secondary_hue="slate")


def create_app() -> gr.Blocks:
    with gr.Blocks(title="格斗小九 PVP 模拟器") as app:
        gr.Markdown("# &#9876; 格斗小九 PVP 模拟器")

        battle_state = gr.State(None)

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 阶段 1: 队伍设置（visible until battle starts）
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        with gr.Column(visible=True) as setup_col:
            gr.Markdown("## &#127987; 队伍设置")
            with gr.Row(equal_height=False):
                # 我方
                with gr.Column():
                    gr.Markdown("### &#127987; 我方队伍")

                    s_a1 = gr.Dropdown(
                        label="精灵 1", choices=SPRITE_NAMES,
                        value="迪莫")
                    sk_a1 = gr.Dropdown(
                        label="技能 (最多4个)", choices=SKILL_MAP.get("迪莫", []),
                        value=["光球", "闪光冲击", "防御", "魔法增效"], multiselect=True, max_choices=4)

                    s_a2 = gr.Dropdown(
                        label="精灵 2", choices=SPRITE_NAMES,
                        value="火神")
                    sk_a2 = gr.Dropdown(
                        label="技能 (最多4个)", choices=SKILL_MAP.get("火神", []),
                        value=["火焰切割", "怒火"], multiselect=True, max_choices=4)

                    s_a3 = gr.Dropdown(
                        label="精灵 3", choices=SPRITE_NAMES,
                        value=None)
                    sk_a3 = gr.Dropdown(
                        label="技能 (最多4个)", choices=[],
                        value=[], multiselect=True, max_choices=4)

                # AI
                with gr.Column():
                    gr.Markdown("### &#129302; AI 队伍")

                    s_b1 = gr.Dropdown(
                        label="精灵 1", choices=SPRITE_NAMES,
                        value="水灵")
                    st_b1 = gr.Dropdown(
                        label="风格", value="balanced",
                        choices=["balanced", "aggressive", "defensive", "cautious"])

                    s_b2 = gr.Dropdown(
                        label="精灵 2", choices=SPRITE_NAMES,
                        value="迪莫")
                    st_b2 = gr.Dropdown(
                        label="风格", value="balanced",
                        choices=["balanced", "aggressive", "defensive", "cautious"])

                    s_b3 = gr.Dropdown(
                        label="精灵 3", choices=SPRITE_NAMES,
                        value=None)
                    st_b3 = gr.Dropdown(
                        label="风格", value="balanced",
                        choices=["balanced", "aggressive", "defensive", "cautious"])

            # 精灵→技能联动
            for dd, tb in [(s_a1, sk_a1), (s_a2, sk_a2), (s_a3, sk_a3)]:
                dd.change(on_sprite_change, inputs=[dd], outputs=[tb])

            start_btn = gr.Button("&#9889; 开始对战", variant="primary", size="lg")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 阶段 2: 对战（visible after battle starts）
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        with gr.Column(visible=False) as battle_col:
            gr.Markdown("## &#9876; 对战")

            battle_html = gr.HTML(
                '<div style="text-align:center;color:#64748b;padding:60px;'
                'font-size:16px;">请在下方队伍设置中选择队伍，然后点击「开始对战」</div>'
            )

            with gr.Row():
                turn_btn = gr.Button("&#9654; 下一回合", variant="primary", interactive=False)
                auto_btn = gr.Button("&#9193; 自动对战", variant="secondary", interactive=False)
                reset_btn = gr.Button("&#128260; 返回组队", variant="stop")

            result_md = gr.Markdown("")

            gr.Markdown("---")
            gr.Markdown("### 战斗日志")
            log_md = gr.Markdown("*点击「开始对战」...*")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 事件绑定
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        start_inputs = [s_a1, sk_a1, s_a2, sk_a2, s_a3, sk_a3,
                        s_b1, st_b1, s_b2, st_b2, s_b3, st_b3]
        start_outputs = [battle_state, battle_html, log_md, turn_btn, setup_col, battle_col]

        start_btn.click(
            start_battle, inputs=start_inputs,
            outputs=start_outputs,
        )

        turn_btn.click(
            next_turn, inputs=[battle_state],
            outputs=[battle_state, battle_html, log_md, turn_btn, result_md],
        )

        auto_btn.click(
            auto_battle, inputs=[battle_state],
            outputs=[battle_state, battle_html, log_md, turn_btn, result_md],
        )

        def _reset():
            return (
                None,
                '<div style="text-align:center;color:#64748b;padding:60px;'
                'font-size:16px;">请在下方队伍设置中选择队伍，然后点击「开始对战」</div>',
                "*点击「开始对战」...*",
                "",
                gr.update(interactive=False),
                gr.update(visible=True),
                gr.update(visible=False),
            )

        reset_btn.click(
            _reset,
            outputs=[battle_state, battle_html, log_md, result_md, turn_btn, setup_col, battle_col],
        )

    return app


if __name__ == "__main__":
    app = create_app()
    print(f"加载 {len(_sprite_entries)} 只精灵, {sum(len(v) for v in SKILL_MAP.values())} 个技能映射")
    app.launch(server_name="127.0.0.1", server_port=7860, share=False, css=_CSS, theme=_THEME)
