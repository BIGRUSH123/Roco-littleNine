"""技能 JSON 可视化编辑器。

启动后打开浏览器，左侧文件列表 + 右侧 JSON 编辑 + 一键保存。
无外部依赖，仅用 Python 标准库。

用法: python scripts/tools/skill_editor.py [--port 8765]
"""

import json
import os
import sys
import webbrowser
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote, urlparse

SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / 'data' / 'skills'
SKILLS_DIR_ESC = str(SKILLS_DIR).replace('\\', '\\\\')

# ── Format helpers ──────────────────────────────────────────────

TOP_ORDER = [
    'id', 'name', 'element', 'skill_type', 'power', 'energy_cost',
    'counter', 'priority', 'combo', 'transmission', 'exclusive_to',
    'effects', 'description',
]

EFFECT_ORDER = {
    'stat':       ['kind', 'target', 'stat', 'steps', 'scope'],
    'abnormal':   ['kind', 'target', 'name', 'stacks', 'scope'],
    'mark':       ['kind', 'target', 'name', 'stacks'],
    'weather':    ['kind', 'weather', 'turns'],
    'conditional':['kind', 'when', 'then'],
}

SPECIAL_ORDER = [
    'kind', 'value', 'amount', 'target', 'abnormal_name',
    'per_stack_value', 'max_value',
]

DROP_IF_DEFAULT = {
    'counter': '无',
    'priority': 0,
    'combo': -1,
    'transmission': -1,
    'exclusive_to': '',
    'power': 0,
}


def _normalize_effect(eff: dict) -> dict:
    kind = eff.get('kind', '')
    order = EFFECT_ORDER.get(kind, SPECIAL_ORDER)
    out = {k: eff[k] for k in order if k in eff}
    for k, v in eff.items():
        if k not in out:
            out[k] = v
    if kind == 'conditional' and isinstance(out.get('then'), list):
        out['then'] = [_normalize_effect(e) for e in out['then']]
    return out


def format_skill(data: dict) -> str:
    ordered = {k: data[k] for k in TOP_ORDER if k in data}
    for k, v in data.items():
        if k not in ordered:
            ordered[k] = v
    for k, dv in DROP_IF_DEFAULT.items():
        if ordered.get(k) == dv:
            del ordered[k]
    if isinstance(ordered.get('effects'), list):
        ordered['effects'] = [_normalize_effect(e) for e in ordered['effects']]
    return json.dumps(ordered, ensure_ascii=False, indent=2) + '\n'

# ── API Handler ──────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # 安静模式

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
        self.send_header('Access-Control-Allow-Methods', 'GET,PUT,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        p = urlparse(self.path)

        if p.path == '/':
            self._serve_html()
        elif p.path == '/api/skills':
            self._list_skills()
        elif p.path.startswith('/api/skill/'):
            name = unquote(p.path[len('/api/skill/'):])
            self._get_skill(name)
        else:
            self.send_response(404)
            self.end_headers()

    def do_PUT(self):
        p = urlparse(self.path)
        if p.path.startswith('/api/skill/'):
            name = unquote(p.path[len('/api/skill/'):])
            self._save_skill(name)
        else:
            self.send_response(404)
            self.end_headers()

    def _list_skills(self):
        skills = []
        for f in sorted(SKILLS_DIR.glob('*.json')):
            if f.name.startswith('_'):
                continue
            try:
                d = json.loads(f.read_text('utf-8'))
                skills.append({
                    'name': d.get('name', f.stem),
                    'filename': f.name,
                    'id': d.get('id', 0),
                    'element': d.get('element', ''),
                    'skill_type': d.get('skill_type', ''),
                    'power': d.get('power', 0),
                    'energy_cost': d.get('energy_cost', 0),
                    'combo': d.get('combo', -1),
                    'transmission': d.get('transmission', 0),
                    'effects_count': len(d.get('effects', [])),
                })
            except Exception:
                skills.append({
                    'name': f.stem, 'filename': f.name,
                    'id': 0, 'element': '?', 'skill_type': '?',
                    'power': 0, 'energy_cost': 0, 'combo': 0,
                    'transmission': 0, 'effects_count': 0,
                })
        self._send_json(skills)

    def _get_skill(self, name):
        fpath = SKILLS_DIR / f'{name}.json'
        if not fpath.exists():
            self._send_json({'error': f'技能 "{name}" 不存在'}, 404)
            return
        self._send_json({'content': fpath.read_text('utf-8'), 'filename': fpath.name})

    def _save_skill(self, name):
        if name.startswith('_'):
            self._send_json({'error': '不能编辑索引文件'}, 403)
            return

        fpath = SKILLS_DIR / f'{name}.json'
        if not fpath.exists():
            self._send_json({'error': f'技能 "{name}" 不存在'}, 404)
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

        formatted = format_skill(data)
        fpath.write_text(formatted, 'utf-8')
        self._send_json({'ok': True, 'filename': fpath.name})

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
<title>技能 JSON 编辑器 — 格斗小九</title>
<style>
:root {
  --bg: #0a0c10;
  --surface: #12151e;
  --border: #1e2230;
  --text: #c8ccd6;
  --dim: #5c6170;
  --accent: #e8b44b;
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
    radial-gradient(ellipse at 20% 50%, rgba(232,180,75,0.03) 0%, transparent 50%),
    radial-gradient(ellipse at 80% 20%, rgba(74,144,217,0.03) 0%, transparent 50%);
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
.file-item.active { background: rgba(232,180,75,0.08); border-left: 2px solid var(--accent); }

.file-item .elem-dot {
  width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
}
.file-item .info { flex:1; min-width:0; }
.file-item .info .name { font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.file-item .info .meta {
  font-size: 10px; color: var(--dim); display: flex; gap: 6px;
}
.file-item .badge {
  font-size: 9px; padding: 1px 5px; border-radius: 3px;
  background: var(--bg); color: var(--dim); flex-shrink: 0;
}
.file-item .badge.modified { background: rgba(232,180,75,0.15); color: var(--accent); }
.file-item .badge.error { background: rgba(224,64,80,0.15); color: var(--danger); }
.file-item.saved { background: rgba(64,184,128,0.06); }
.file-item.saved::after {
  content: '✓';
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

/* element colors */
.e-火 { background: var(--fire); } .e-水 { background: var(--water); }
.e-草 { background: var(--grass); } .e-冰 { background: var(--ice); }
.e-电 { background: var(--elec); } .e-地 { background: var(--earth); }
.e-武 { background: var(--fight); } .e-毒 { background: var(--poison); }
.e-虫 { background: var(--bug); } .e-龙 { background: var(--dragon); }
.e-机械 { background: var(--mech); } .e-普通 { background: var(--neutral); }
.e-光 { background: var(--light); } .e-幻 { background: var(--illusion); }
.e-恶 { background: var(--evil); } .e-幽灵 { background: var(--ghost); }
.e-萌 { background: var(--moe); } .e-翼 { background: var(--wing); }

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
    <h1>技能编辑器</h1>
    <div class="subtitle">JSON Editor · 格斗小九</div>
  </div>
  <input id="search-box" type="text" placeholder="搜索技能名、属性、类型..." autocomplete="off">
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
      <div class="file-title" id="current-name">选择一个技能</div>
      <div class="file-path" id="current-path"></div>
    </div>
    <span id="status-msg"></span>
    <div style="flex:1"></div>
    <button id="btn-pretty" title="格式化 JSON">格式化</button>
    <button id="btn-reload">重置</button>
    <button id="btn-save" disabled>保存</button>
  </div>

  <div id="empty-state">
    <div class="icon">◈</div>
    <p>从左侧列表选择一个技能开始编辑</p>
    <p style="font-size:11px;opacity:0.5">Ctrl+S 保存 · 点击文件名加载</p>
  </div>

  <div id="editor-wrap" style="display:none">
    <div id="line-numbers"></div>
    <textarea id="json-editor" spellcheck="false" wrap="off"></textarea>
  </div>
</main>

<div id="toast"></div>
<div id="kbd-hint">
  <kbd>Ctrl+S</kbd> 保存 &nbsp; <kbd>↑</kbd><kbd>↓</kbd> 切换文件
</div>

<script>
// ── State ──────────────────────────────────────
let skills = [];
let currentFile = null;
let originalContent = '';
let modified = false;
let savedFiles = new Set();  // 本次会话已保存的文件名集合

const $list = document.getElementById('file-list');
const $search = document.getElementById('search-box');
const $editor = document.getElementById('json-editor');
const $lineNums = document.getElementById('line-numbers');
const $editorWrap = document.getElementById('editor-wrap');
const $empty = document.getElementById('empty-state');
const $btnSave = document.getElementById('btn-save');
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
  const res = await fetch('/api/skills');
  skills = await res.json();
  renderList();
}
loadList();

// ── Render ────────────────────────────────────
function renderList() {
  const q = $search.value.toLowerCase();
  const filtered = skills.filter(s => {
    if (!q) return true;
    return (s.name + s.element + s.skill_type + s.filename).toLowerCase().includes(q);
  });

  $statTotal.textContent = '总计: ' + skills.length;
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
    const elCls = 'e-' + s.element;
    let rowCls = s.filename === currentFile ? ' active' : '';
    if (savedFiles.has(s.filename)) rowCls += ' saved';
    return `<div class="file-item${rowCls}" data-file="${_esc(s.filename)}" data-index="${i}">
      <div class="elem-dot ${elCls}"></div>
      <div class="info">
        <div class="name">${_esc(s.name)}</div>
        <div class="meta">
          <span>${_esc(s.element)}</span>
          <span>${_esc(s.skill_type)}</span>
          <span>W:${s.power}</span>
          <span>E:${s.energy_cost}</span>
        </div>
      </div>
      ${s.effects_count ? '<span class="badge">'+s.effects_count+'eff</span>' : ''}
      ${badges}
    </div>`;
  }).join('');

  // click handlers
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
  const res = await fetch('/api/skill/' + encodeURIComponent(name));
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
  $curPath.textContent = 'data/skills/' + filename;

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
  // validate JSON
  try {
    JSON.parse(content);
  } catch (e) {
    toast('JSON 格式错误: ' + e.message, 'err');
    return;
  }

  const name = currentFile.replace('.json','');
  const res = await fetch('/api/skill/' + encodeURIComponent(name), {
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
    renderList(); // refresh sidebar
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
const TOP_ORDER = [
  'id', 'name', 'element', 'skill_type', 'power', 'energy_cost',
  'counter', 'priority', 'combo', 'transmission', 'exclusive_to',
  'effects', 'description'
];

const EFFECT_ORDER = {
  stat:       ['kind', 'target', 'stat', 'steps', 'scope'],
  abnormal:   ['kind', 'target', 'name', 'stacks', 'scope'],
  mark:       ['kind', 'target', 'name', 'stacks'],
  weather:    ['kind', 'weather', 'turns'],
  conditional:['kind', 'when', 'then'],
};

const SPECIAL_ORDER = [
  'kind', 'value', 'amount', 'target', 'abnormal_name',
  'per_stack_value', 'max_value'
];

const DROP_IF_DEFAULT = {
  counter: '无',
  priority: 0,
  combo: -1,
  transmission: -1,
  exclusive_to: '',
  power: 0,
};

function formatSkill(raw) {
  // 1) 解析
  const obj = (typeof raw === 'string') ? JSON.parse(raw) : raw;

  // 2) 顶层字段排序
  const ordered = {};
  for (const k of TOP_ORDER) {
    if (k in obj) ordered[k] = obj[k];
  }
  // 保留未在 TOP_ORDER 中的额外字段
  for (const k of Object.keys(obj)) {
    if (!(k in ordered)) ordered[k] = obj[k];
  }

  // 3) 移除默认值（可选字段）
  for (const [k, dv] of Object.entries(DROP_IF_DEFAULT)) {
    if (ordered[k] === dv) delete ordered[k];
  }

  // 4) 规范化 effects
  if (Array.isArray(ordered.effects)) {
    ordered.effects = ordered.effects.map(normalizeEffect);
  }

  // 5) 输出
  return JSON.stringify(ordered, null, 2) + '\n';
}

function normalizeEffect(eff) {
  const kind = eff.kind || '';
  let order = EFFECT_ORDER[kind];

  if (!order) {
    // 瞬时效果（原 special 扁平化）
    order = SPECIAL_ORDER;
  }

  const out = {};
  for (const k of order) {
    if (k in eff) out[k] = eff[k];
  }
  // 保留额外字段
  for (const k of Object.keys(eff)) {
    if (!(k in out)) out[k] = eff[k];
  }

  // 递归处理 conditional.then
  if (kind === 'conditional' && Array.isArray(out.then)) {
    out.then = out.then.map(normalizeEffect);
  }

  // 清理 value=0 且 amount=0 的冗余字段（保留至少一个）
  if (!kind) {}
  return out;
}

// ── Bind button ────────────────────────────────
document.getElementById('btn-pretty').addEventListener('click', () => {
  try {
    const formatted = formatSkill($editor.value);
    $editor.value = formatted;
    updateLineNumbers();
    if (formatted !== originalContent) {
      modified = true; $btnSave.disabled = false;
    }
  } catch (e) {
    toast('JSON 格式错误，无法格式化: ' + e.message, 'err');
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
    port = 8765
    for a in sys.argv[1:]:
        if a.startswith('--port='):
            port = int(a.split('=')[1])

    server = HTTPServer(('127.0.0.1', port), Handler)
    url = f'http://127.0.0.1:{port}'
    print(f'技能编辑器已启动: {url}')
    print('按 Ctrl+C 退出')
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n已退出')
        server.shutdown()


if __name__ == '__main__':
    main()
