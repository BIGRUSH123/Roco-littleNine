"""特性 JSON 可视化编辑器。

启动后打开浏览器，左侧文件列表 + 右侧 JSON 编辑 + 一键保存。
无外部依赖，仅用 Python 标准库。

用法: python scripts/tools/trait_editor.py [--port 8766]
"""

import json
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

TRAITS_DIR = Path(__file__).resolve().parent.parent.parent / 'data' / 'traits'
TRAITS_DIR_ESC = str(TRAITS_DIR).replace('\\', '\\\\')

# ── Format helpers ──────────────────────────────────────────────

TOP_ORDER = [
    'id', 'name', 'description', 'triggers',
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
        self.send_header('Access-Control-Allow-Methods', 'GET,PUT,OPTIONS')
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
                    'triggers_count': len(d.get('triggers', [])),
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
const TOP_ORDER = ['id', 'name', 'description', 'triggers'];
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
