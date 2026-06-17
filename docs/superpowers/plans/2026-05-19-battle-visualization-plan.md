# 精灵对战可视化 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于现有对战引擎，重构前端 UI 为自然奇幻风格，实现精灵可视化对战 + buff/debuff 清晰展示 + 属性克制可视化 + 回合时间线回放。

**Architecture:** 设计 Token 体系驱动 + 组件重建。Vue 3 + Vite + Tailwind CSS 4 + GSAP（动画引擎）+ Pinia（状态管理）+ Headless UI v2（可访问组件库）。三栏对战布局（对方精灵区 | 对战动画区 | 日志+克制热力图）。不引入路由。

**Tech Stack:** Vue 3.5, Vite 8, Tailwind CSS 4, GSAP 3.12, Pinia 2.1, Headless UI v2, FastAPI (backend)

---

## File Map

| 文件 | 职责 | 新建/修改 |
|------|------|-----------|
| `frontend/src/design/tokens.css` | CSS 变量定义（色板/字体/间距/圆角/阴影） | 新建 |
| `frontend/src/design/elements.css` | 18 系别元素属性色 | 新建 |
| `frontend/src/design/animations.css` | 辅助 CSS（GSAP 不覆盖的静态样式） | 新建 |
| `frontend/src/stores/battle.js` | Pinia — 对战核心状态 | 新建 |
| `frontend/src/stores/team.js` | Pinia — 队伍编成 | 新建 |
| `frontend/src/stores/spriteAssets.js` | Pinia — 精灵图片缓存 + 按需加载 | 新建 |
| `frontend/src/composables/useSpriteAnim.js` | GSAP 精灵动画 composable | 新建 |
| `frontend/src/components/SpriteCard.vue` | 精灵卡片（复用：选队/对战） | 新建 |
| `frontend/src/components/EffectCard.vue` | 增益/异常卡片（Popover 来源 tooltip） | 新建 |
| `frontend/src/components/SkillButton.vue` | 技能按钮（Popover 技能详情 tooltip） | 新建 |
| `frontend/src/components/TypeChart.vue` | 18×18 属性克制缩略热力图（Popover 放大） | 新建 |
| `frontend/src/components/TimelineReplay.vue` | 回合时间线回放面板（Dialog + GSAP） | 新建 |
| `frontend/src/components/BattleArena.vue` | 对战主组件（Pinia + GSAP 动画） | 重写 |
| `frontend/src/components/TeamSelection.vue` | 队伍选择（Dialog + Listbox） | 重写 |
| `frontend/src/components/BattleLog.vue` | 战斗日志（增强样式） | 重写 |
| `frontend/src/App.vue` | 引入 Pinia plugin + 初始化 stores | 重写 |
| `frontend/src/style.css` | 视觉升级（引入 design tokens，自然奇幻风） | 重写 |
| `frontend/src/main.js` | 注册 Pinia plugin | 修改 |
| `backend/api/schemas.py` | 新增 TurnSnapshot Pydantic model | 修改 |
| `backend/api/main.py` | `/api/battle/action` 响应中追加 turn_snapshot | 修改 |
| `frontend/package.json` | 新增 gsap, pinia, @headlessui/vue 依赖 | 修改 |
| `frontend/vite.config.js` | 添加 sprites 静态资源别名 | 修改 |

---

### Task 1: 安装新依赖 + 精灵资源解压

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/public/sprites/` (467 PNG files extracted)

- [ ] **Step 1: 解压精灵图资源**

```bash
# Create sprites directory
New-Item -ItemType Directory -Force "frontend/public/sprites"

# Extract all zip files from _attachments/sprites/ into public/sprites/
Get-ChildItem "_attachments/sprites/*.zip" | ForEach-Object {
    Expand-Archive -Path $_.FullName -DestinationPath "frontend/public/sprites" -Force
}

# Count extracted PNGs
(Get-ChildItem "frontend/public/sprites/*.png").Count
```

Expected: 467 PNG files in `frontend/public/sprites/`

- [ ] **Step 2: 安装 npm 依赖**

```bash
cd frontend
npm install gsap@^3.12 pinia@^2.1 @headlessui/vue@^2.0
```

Expected: 3 packages added to package.json and node_modules

- [ ] **Step 3: 验证 Vite dev server 可访问精灵图**

Run: `cd frontend; npx vite --host 0.0.0.0`
Expected: Visit `http://localhost:5173/sprites/<any-png-name>.png` — image loads

- [ ] **Step 4: 验证 GSAP/Pinia/HeadlessUI 可导入**

```bash
cd frontend
node -e "require('gsap'); console.log('GSAP OK')"
node -e "require('pinia'); console.log('Pinia OK')"
node -e "require('@headlessui/vue'); console.log('HeadlessUI OK')"
```

Expected: All three print "OK"

- [ ] **Step 5: Commit**

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "新增: GSAP + Pinia + Headless UI 依赖安装，精灵图资源解压至 public/sprites/"
```

---

### Task 2: 创建设计 Token CSS 文件

**Files:**
- Create: `frontend/src/design/tokens.css`
- Create: `frontend/src/design/elements.css`
- Create: `frontend/src/design/animations.css`

- [ ] **Step 1: 创建 tokens.css — 色板 + 字体 + 形状变量**

```css
/* frontend/src/design/tokens.css */
:root {
  /* 色板 */
  --color-parchment: #FBF7F0;
  --color-wood: #3D2B1F;
  --color-moss: #5C8D6E;
  --color-gold: #C9A96E;
  --color-purple: #4A3B5C;
  --color-fire: #D4534A;
  --color-heal: #6DBF7C;

  /* 语义色 */
  --bg-main: var(--color-parchment);
  --text-primary: var(--color-wood);
  --text-secondary: #6B5E4F;
  --accent: var(--color-gold);
  --border-default: rgba(61, 43, 31, 0.12);
  --border-hover: var(--color-gold);
  --btn-primary-bg: var(--color-moss);
  --btn-primary-text: #FFFFFF;
  --damage-color: var(--color-fire);
  --heal-color: var(--color-heal);
  --enemy-color: var(--color-purple);

  /* 字体 */
  --font-title: 'Georgia', 'Noto Serif SC', 'Source Han Serif SC', serif;
  --font-body: 'Verdana', 'Noto Sans SC', 'Source Han Sans SC', sans-serif;

  /* 间距 */
  --radius-sm: 8px;
  --radius-md: 12px;
  --radius-lg: 16px;

  /* 阴影 */
  --shadow-card: inset 0 1px 0 rgba(255,255,255,0.5), 0 1px 3px rgba(61,43,31,0.1);
  --shadow-btn: 0 2px 0 rgba(61,43,31,0.15);
  --shadow-glow: 0 0 12px rgba(201,169,110,0.3);
}

/* 卡片基底 */
.card {
  background: var(--bg-main);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-card);
}

.card-hover:hover {
  border-color: var(--border-hover);
  box-shadow: var(--shadow-card), var(--shadow-glow);
}

/* 按钮基底 */
.btn {
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow-btn);
  font-family: var(--font-body);
  font-weight: 600;
  transition: transform 0.1s, box-shadow 0.1s;
}
.btn:active {
  transform: translateY(1px);
  box-shadow: none;
}

.btn-primary {
  background: var(--btn-primary-bg);
  color: var(--btn-primary-text);
}

/* 标题 */
.title {
  font-family: var(--font-title);
  color: var(--text-primary);
}
```

- [ ] **Step 2: 创建 elements.css — 18 系别属性色**

```css
/* frontend/src/design/elements.css */
/* 18 系别属性色 — 加深饱和度适配深色卡片 */
.element-火  { --elem-color: #E53935; background: #E53935; color: #fff; }
.element-水  { --elem-color: #1E88E5; background: #1E88E5; color: #fff; }
.element-草  { --elem-color: #43A047; background: #43A047; color: #fff; }
.element-光  { --elem-color: #FDD835; background: #FDD835; color: #3D2B1F; }
.element-暗  { --elem-color: #8E24AA; background: #8E24AA; color: #fff; }
.element-龙  { --elem-color: #EF6C00; background: #EF6C00; color: #fff; }
.element-电  { --elem-color: #F9A825; background: #F9A825; color: #3D2B1F; }
.element-冰  { --elem-color: #26C6DA; background: #26C6DA; color: #3D2B1F; }
.element-虫  { --elem-color: #7CB342; background: #7CB342; color: #fff; }
.element-毒  { --elem-color: #AB47BC; background: #AB47BC; color: #fff; }
.element-土  { --elem-color: #6D4C41; background: #6D4C41; color: #fff; }
.element-地  { --elem-color: #8D6E63; background: #8D6E63; color: #fff; }
.element-石  { --elem-color: #757575; background: #757575; color: #fff; }
.element-钢  { --elem-color: #90A4AE; background: #90A4AE; color: #3D2B1F; }
.element-翼  { --elem-color: #4FC3F7; background: #4FC3F7; color: #3D2B1F; }
.element-幻  { --elem-color: #E040FB; background: #E040FB; color: #fff; }
.element-妖  { --elem-color: #F06292; background: #F06292; color: #fff; }
.element-武  { --elem-color: #D84315; background: #D84315; color: #fff; }
.element-普  { --elem-color: #9E9E9E; background: #9E9E9E; color: #fff; }
.element-幽灵 { --elem-color: #5E35B1; background: #5E35B1; color: #fff; }
.element-鬼  { --elem-color: #5E35B1; background: #5E35B1; color: #fff; }

/* 元素标签通用 */
.elem-tag {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.5px;
}
```

- [ ] **Step 3: 创建 animations.css — GSAP 辅助静态样式**

```css
/* frontend/src/design/animations.css */
/* GSAP 不覆盖的纯 CSS 动画和关键帧定义 */

/* 精灵待机阴影 */
.sprite-shadow {
  width: 60%;
  height: 12px;
  background: radial-gradient(ellipse, rgba(61,43,31,0.15) 0%, transparent 70%);
  margin: 0 auto;
  border-radius: 50%;
}

/* 伤害数字弹出 */
.damage-popup {
  position: absolute;
  font-family: var(--font-title);
  font-weight: 700;
  font-size: 24px;
  pointer-events: none;
  z-index: 100;
  text-shadow: 0 2px 4px rgba(0,0,0,0.3);
}

.damage-popup.harm {
  color: var(--damage-color);
}

.damage-popup.heal {
  color: var(--heal-color);
}

/* 回合圆点脉冲 */
@keyframes pulse-dot {
  0%, 100% { box-shadow: 0 0 0 0 rgba(201,169,110,0.6); }
  50% { box-shadow: 0 0 0 6px rgba(201,169,110,0); }
}

.pulse-dot {
  animation: pulse-dot 1.5s ease-in-out infinite;
}

/* 技能按钮悬浮光晕 */
.skill-glow {
  transition: box-shadow 0.15s ease;
}
.skill-glow:hover {
  box-shadow: 0 0 12px rgba(201,169,110,0.3);
}

/* 转场 */
.page-enter-active,
.page-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}
.page-enter-from {
  opacity: 0;
  transform: translateY(8px);
}
.page-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/design/tokens.css frontend/src/design/elements.css frontend/src/design/animations.css
git commit -m "新增: 设计 Token CSS — 色板/字体/形状/18系别色/辅助动画"
```

---

### Task 3: 创建 Pinia Stores

**Files:**
- Create: `frontend/src/stores/battle.js`
- Create: `frontend/src/stores/team.js`
- Create: `frontend/src/stores/spriteAssets.js`
- Modify: `frontend/src/main.js` (register Pinia)

- [ ] **Step 1: 创建 useSpriteAssetStore**

```js
// frontend/src/stores/spriteAssets.js
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useSpriteAssetStore = defineStore('spriteAssets', () => {
  const cache = ref(new Map())
  const loading = ref(new Set())
  const errors = ref(new Set())

  function getUrl(name) {
    if (cache.value.has(name)) return cache.value.get(name)
    // 触发加载但不同步返回
    loadSprite(name)
    return null
  }

  function hasError(name) {
    return errors.value.has(name)
  }

  async function loadSprite(name) {
    if (cache.value.has(name)) return cache.value.get(name)
    if (loading.value.has(name)) return null
    loading.value.add(name)

    return new Promise((resolve) => {
      const img = new Image()
      const url = `/sprites/${encodeURIComponent(name)}.png`
      img.onload = () => {
        cache.value.set(name, url)
        loading.value.delete(name)
        resolve(url)
      }
      img.onerror = () => {
        errors.value.add(name)
        loading.value.delete(name)
        resolve(null)
      }
      img.src = url
    })
  }

  async function preloadTeam(names) {
    const urls = await Promise.all(names.map(n => loadSprite(n)))
    return urls.filter(Boolean)
  }

  return { cache, loading, errors, getUrl, hasError, loadSprite, preloadTeam }
})
```

- [ ] **Step 2: 创建 useTeamStore**

```js
// frontend/src/stores/team.js
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useTeamStore = defineStore('team', () => {
  const slots = ref(Array(6).fill(null))
  const selectedBloodline = ref(null)
  const selectedItem = ref(null)
  const leadIndex = ref(0)
  const savedTeams = ref([])

  const filledCount = computed(() => slots.value.filter(s => s !== null).length)
  const isReady = computed(() => {
    return slots.value.some((s, i) =>
      s !== null && s.selectedSkills && s.selectedSkills.length > 0
    )
  })

  function setSlot(index, sprite) {
    slots.value[index] = sprite
  }

  function clearSlot(index) {
    slots.value[index] = null
    if (leadIndex.value === index) {
      leadIndex.value = slots.value.findIndex(s => s !== null)
    }
  }

  function buildTeamPayload() {
    const team = []
    const slotToTeam = {}
    slots.value.forEach((s, i) => {
      if (s) {
        slotToTeam[i] = team.length
        team.push({
          name: s.name,
          skills: s.selectedSkills || s.skills || [],
          bloodline: s.bloodline || undefined,
        })
      }
    })
    return { team, leadIndex: slotToTeam[leadIndex.value] ?? 0 }
  }

  return {
    slots, selectedBloodline, selectedItem, leadIndex, savedTeams,
    filledCount, isReady,
    setSlot, clearSlot, buildTeamPayload,
  }
})
```

- [ ] **Step 3: 创建 useBattleStore**

```js
// frontend/src/stores/battle.js
import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'

export const useBattleStore = defineStore('battle', () => {
  // 双方精灵
  const selfSprite = ref(null)
  const oppSprite = ref(null)

  // 队伍
  const selfTeam = ref([])
  const oppTeam = ref([])

  // 回合
  const turn = ref(0)
  const maxTurn = ref(150)
  const weather = ref(null)

  // 技能
  const selfSkills = ref([])
  const selectedSkill = ref(null)

  // 标记
  const marksA = ref([])
  const marksB = ref([])

  // 时间线回放
  const turnSnapshots = ref([])
  const replayMode = ref(false)
  const replayTurn = ref(0)

  // UI 状态
  const isProcessing = ref(false)
  const battlePhase = ref('selection')  // 'selection' | 'action' | 'result'
  const winner = ref(null)
  const logEntries = ref([])
  const sessionId = ref(null)
  const isFinished = ref(false)

  const hpPct = (current, max) => max === 0 ? 0 : Math.max(0, Math.min(100, (current / max) * 100))
  const hpColorClass = (current, max) => {
    if (max === 0) return 'bg-[#9E9E9E]'
    const pct = current / max
    if (pct > 0.5) return 'bg-[#6DBF7C]'
    if (pct > 0.25) return 'bg-[#FDD835]'
    return 'bg-[#D4534A]'
  }

  function updateFromResponse(data) {
    if (data.session_id) sessionId.value = data.session_id
    if (data.turn !== undefined) turn.value = data.turn
    if (data.is_finished !== undefined) {
      isFinished.value = data.is_finished
      if (data.is_finished) battlePhase.value = 'result'
    }
    if (data.winner) winner.value = data.winner
    if (data.weather !== undefined) weather.value = data.weather

    // Player A (self)
    if (data.player_a) {
      const pa = data.player_a
      selfTeam.value = pa.team || []
      const activeIdx = pa.active_index ?? 0
      selfSprite.value = pa.team?.[activeIdx] || null
      selfSkills.value = selfSprite.value?.skills || []
    }

    // Player B (opponent)
    if (data.player_b) {
      const pb = data.player_b
      oppTeam.value = pb.team || []
      const activeIdx = pb.active_index ?? 0
      oppSprite.value = pb.team?.[activeIdx] || null
    }

    // Marks
    if (data.marks_a) marksA.value = data.marks_a
    if (data.marks_b) marksB.value = data.marks_b

    // Snapshot
    if (data.turn_snapshot) {
      turnSnapshots.value.push(data.turn_snapshot)
    }
  }

  function appendLogs(logs) {
    if (logs && logs.length > 0) {
      logEntries.value.push(...logs)
    }
  }

  function setReplayTurn(t) {
    replayTurn.value = t
    const snap = turnSnapshots.value.find(s => s.turn === t)
    if (snap) {
      selfSprite.value = snap.self_sprite
      oppSprite.value = snap.opp_sprite
    }
  }

  function resetBattle() {
    selfSprite.value = null
    oppSprite.value = null
    selfTeam.value = []
    oppTeam.value = []
    turn.value = 0
    weather.value = null
    selfSkills.value = []
    selectedSkill.value = null
    marksA.value = []
    marksB.value = []
    turnSnapshots.value = []
    replayMode.value = false
    replayTurn.value = 0
    isProcessing.value = false
    battlePhase.value = 'selection'
    winner.value = null
    logEntries.value = []
    sessionId.value = null
    isFinished.value = false
  }

  return {
    selfSprite, oppSprite, selfTeam, oppTeam,
    turn, maxTurn, weather,
    selfSkills, selectedSkill,
    marksA, marksB,
    turnSnapshots, replayMode, replayTurn,
    isProcessing, battlePhase, winner, logEntries, sessionId, isFinished,
    hpPct, hpColorClass,
    updateFromResponse, appendLogs, setReplayTurn, resetBattle,
  }
})
```

- [ ] **Step 4: 注册 Pinia 到 main.js**

```js
// frontend/src/main.js
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './style.css'
import App from './App.vue'

const app = createApp(App)
app.use(createPinia())
app.mount('#app')
```

- [ ] **Step 5: 验证 stores 可导入且无语法错误**

```bash
cd frontend
node -e "
import { createPinia } from 'pinia';
import { createApp } from 'vue';
console.log('Pinia import OK');
" 2>&1 || echo "(ESM import in CJS context expected to fail — verify via Vite instead)"
```

Run dev server: `cd frontend; npx vite --host 0.0.0.0`
Check browser console for no import errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/stores/battle.js frontend/src/stores/team.js frontend/src/stores/spriteAssets.js frontend/src/main.js
git commit -m "新增: Pinia stores — battle/team/spriteAssets + main.js 注册 Pinia"
```

---

### Task 4: GSAP 精灵动画 Composable

**Files:**
- Create: `frontend/src/composables/useSpriteAnim.js`

- [ ] **Step 1: 实现 useSpriteAnim composable**

```js
// frontend/src/composables/useSpriteAnim.js
import { ref } from 'vue'
import gsap from 'gsap'

export function useSpriteAnim(spriteRef, shadowRef) {
  const isAnimating = ref(false)

  async function playIdle() {
    if (!spriteRef.value) return
    gsap.killTweensOf(spriteRef.value, 'scale')
    gsap.to(spriteRef.value, {
      scale: 1.02,
      duration: 1,
      yoyo: true,
      repeat: -1,
      ease: 'sine.inOut',
    })
    if (shadowRef?.value) {
      gsap.to(shadowRef.value, {
        scale: 0.95,
        duration: 1,
        yoyo: true,
        repeat: -1,
        ease: 'sine.inOut',
      })
    }
  }

  function stopIdle() {
    if (spriteRef.value) {
      gsap.killTweensOf(spriteRef.value, 'scale')
      gsap.set(spriteRef.value, { scale: 1 })
    }
    if (shadowRef?.value) {
      gsap.killTweensOf(shadowRef.value, 'scale')
      gsap.set(shadowRef.value, { scale: 1 })
    }
  }

  async function playHit() {
    if (!spriteRef.value) return
    isAnimating.value = true
    const tl = gsap.timeline()
    tl.to(spriteRef.value, {
      x: -5,
      duration: 0.05,
      repeat: 5,
      yoyo: true,
      ease: 'rough({ strength: 3, points: 10 })',
    })
    tl.to(spriteRef.value, {
      filter: 'brightness(1.5) hue-rotate(-30deg)',
      duration: 0.1,
      yoyo: true,
      repeat: 1,
    }, 0)
    await tl.play()
    gsap.set(spriteRef.value, { x: 0, filter: 'none' })
    isAnimating.value = false
  }

  async function playAttack() {
    if (!spriteRef.value) return
    isAnimating.value = true
    const el = spriteRef.value
    const tl = gsap.timeline()
    tl.to(el, {
      x: -20,
      duration: 0.15,
      ease: 'power2.out',
    })
    tl.to(el, {
      boxShadow: '0 0 20px 8px rgba(201,169,110,0.5)',
      duration: 0.1,
    })
    tl.to(el, {
      x: 0,
      boxShadow: '0 0 0px 0px rgba(201,169,110,0)',
      duration: 0.2,
      ease: 'power2.in',
    })
    await tl.play()
    gsap.set(el, { clearProps: 'boxShadow' })
    isAnimating.value = false
  }

  async function playFaint() {
    if (!spriteRef.value) return
    isAnimating.value = true
    await gsap.to(spriteRef.value, {
      filter: 'grayscale(1)',
      opacity: 0.6,
      y: 10,
      duration: 0.5,
      ease: 'power2.out',
    })
    isAnimating.value = false
  }

  async function playEntry() {
    if (!spriteRef.value) return
    isAnimating.value = true
    gsap.set(spriteRef.value, { filter: 'brightness(2)', opacity: 0.3 })
    await gsap.fromTo(spriteRef.value,
      { y: -60, opacity: 0 },
      { y: 0, opacity: 1, duration: 0.4, ease: 'power2.out' }
    )
    gsap.to(spriteRef.value, {
      filter: 'brightness(1)',
      duration: 0.2,
    })
    isAnimating.value = false
    playIdle()
  }

  async function playDamageNumber(el, value, isHeal = false) {
    if (!el) return
    gsap.set(el, { scale: 0.5, opacity: 1, y: 0 })
    await gsap.to(el, {
      scale: 1.3,
      duration: 0.2,
      ease: 'back.out(2)',
    })
    await gsap.to(el, {
      scale: 0.8,
      opacity: 0,
      y: -40,
      duration: 0.8,
      ease: 'power2.out',
      delay: 0.3,
    })
  }

  async function playHpTransition(barRef, newWidth) {
    if (!barRef.value) return
    await gsap.to(barRef.value, {
      width: newWidth + '%',
      duration: 0.5,
      ease: 'power2.out',
    })
  }

  function cleanup() {
    if (spriteRef.value) {
      gsap.killTweensOf(spriteRef.value)
    }
    isAnimating.value = false
  }

  return {
    isAnimating,
    playIdle, stopIdle,
    playHit, playAttack, playFaint, playEntry,
    playDamageNumber, playHpTransition,
    cleanup,
  }
}
```

- [ ] **Step 2: 在 Vite 中验证 composable 可导入**

Run: `cd frontend; npx vite build --mode development 2>&1`
Expected: No build errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/composables/useSpriteAnim.js
git commit -m "新增: GSAP 精灵动画 composable — 待机/受击/攻击/力竭/入场/伤害数字/HP过渡"
```

---

### Task 5: 新建 SpriteCard 组件

**Files:**
- Create: `frontend/src/components/SpriteCard.vue`

- [ ] **Step 1: 实现 SpriteCard.vue**

```vue
<!-- frontend/src/components/SpriteCard.vue -->
<script setup>
import { computed } from 'vue'
import { useSpriteAssetStore } from '../stores/spriteAssets.js'

const props = defineProps({
  sprite: { type: Object, default: null },
  size: { type: String, default: 'md' },    // 'sm' | 'md' | 'lg'
  showHp: { type: Boolean, default: false },
  showEnergy: { type: Boolean, default: false },
  isFainted: { type: Boolean, default: false },
})

const spriteAssets = useSpriteAssetStore()

const spriteUrl = computed(() => {
  if (!props.sprite?.name) return null
  return spriteAssets.getUrl(props.sprite.name)
})

const hasError = computed(() => {
  return props.sprite?.name ? spriteAssets.hasError(props.sprite.name) : false
})

const sizeClass = computed(() => ({
  sm: 'w-20 h-20',
  md: 'w-32 h-32',
  lg: 'w-48 h-48',
})[props.size])

const hpPct = computed(() => {
  if (!props.sprite) return 0
  const { current_hp, max_hp } = props.sprite
  return max_hp > 0 ? Math.max(0, Math.min(100, (current_hp / max_hp) * 100)) : 0
})

const primaryElement = computed(() => {
  return props.sprite?.element?.split(',')[0]?.trim() || ''
})
</script>

<template>
  <div class="flex flex-col items-center gap-2" :class="{ 'opacity-60 grayscale': isFainted }">
    <!-- Sprite Image / Fallback -->
    <div
      :class="[sizeClass, 'relative rounded-full flex items-center justify-center']"
      :style="{ background: primaryElement ? `var(--elem-color, #C9A96E)` : '#C9A96E' }"
    >
      <img
        v-if="spriteUrl"
        :src="spriteUrl"
        :alt="sprite?.name || '精灵'"
        class="w-full h-full object-contain p-1"
      />
      <span
        v-else
        class="text-white text-sm font-bold text-center px-2 leading-tight"
      >{{ sprite?.name || '???' }}</span>

      <!-- Fainted overlay -->
      <div v-if="isFainted" class="absolute inset-0 bg-black/40 rounded-full flex items-center justify-center">
        <span class="text-[#D4534A] text-xs font-bold">力竭</span>
      </div>
    </div>

    <!-- Shadow -->
    <div class="sprite-shadow"></div>

    <!-- HP Bar -->
    <div v-if="showHp && sprite" class="w-full max-w-40">
      <div class="flex justify-between text-xs text-[#6B5E4F] mb-0.5">
        <span>HP</span>
        <span>{{ sprite.current_hp }}/{{ sprite.max_hp }}</span>
      </div>
      <div class="w-full h-3 bg-[#E8E0D5] rounded-full overflow-hidden border border-[#D4C8B8]">
        <div
          class="h-full transition-all duration-500 rounded-full"
          :style="{ width: hpPct + '%' }"
          :class="hpPct > 50 ? 'bg-gradient-to-r from-[#6DBF7C] to-[#43A047]' : hpPct > 25 ? 'bg-gradient-to-r from-[#FDD835] to-[#F9A825]' : 'bg-gradient-to-r from-[#D4534A] to-[#C62828]'"
        ></div>
      </div>
    </div>

    <!-- Energy -->
    <div v-if="showEnergy && sprite" class="flex items-center gap-1">
      <span class="text-xs text-[#6B5E4F]">能量</span>
      <div class="flex gap-0.5">
        <div
          v-for="i in 10"
          :key="i"
          class="w-2.5 h-4 rounded-sm transition-colors duration-300"
          :class="i <= (sprite.energy || 0) ? 'bg-gradient-to-b from-[#C9A96E] to-[#A08050]' : 'bg-[#D4C8B8]'"
        ></div>
      </div>
      <span class="text-xs text-[#6B5E4F] ml-1">{{ sprite.energy || 0 }}/10</span>
    </div>
  </div>
</template>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/SpriteCard.vue
git commit -m "新增: SpriteCard 组件 — 精灵图片展示/后备占位/HP条/能量条"
```

---

### Task 6: 新建 EffectCard 组件

**Files:**
- Create: `frontend/src/components/EffectCard.vue`

- [ ] **Step 1: 实现 EffectCard.vue**

```vue
<!-- frontend/src/components/EffectCard.vue -->
<script setup>
import { Popover, PopoverButton, PopoverPanel, Transition } from '@headlessui/vue'

const props = defineProps({
  effect: { type: Object, required: true },
})

const isPositive = computed(() => props.effect.category !== 'abnormal')

const icon = computed(() => {
  if (!isPositive.value) {
    const abnormalIcons = {
      '灼烧': '🔥', '中毒': '💀', '冻结': '❄️', '麻痹': '⚡',
      '睡眠': '💤', '混乱': '😵', '寄生': '🌿', '诅咒': '👻',
      '烧伤': '🔥', '冻伤': '❄️',
    }
    return abnormalIcons[props.effect.name] || '⚠️'
  }
  const statIcons = {
    '攻击': '⚔️', '防御': '🛡️', '魔攻': '🔮', '魔防': '🛡️',
    '速度': '💨', '威力': '💥', '命中': '🎯', '闪避': '👟',
  }
  for (const [key, icon] of Object.entries(statIcons)) {
    if (props.effect.name.includes(key)) return icon
  }
  return '⬆️'
})

const sourceName = computed(() => props.effect.source || '')
</script>

<template>
  <Popover class="relative inline-flex">
    <PopoverButton
      :class="[
        'px-2 py-1 text-xs rounded-lg border font-medium transition-colors flex items-center gap-1',
        isPositive
          ? 'bg-[#E8F5E9] border-[#A5D6A7] text-[#5C8D6E] hover:bg-[#C8E6C9]'
          : 'bg-[#EDE7F6] border-[#B39DDB] text-[#4A3B5C] hover:bg-[#D1C4E9]'
      ]"
    >
      <span>{{ icon }}</span>
      <span>{{ effect.name }}</span>
      <span v-if="effect.steps > 0" class="font-mono">+{{ effect.steps }}</span>
      <span v-if="effect.stacks > 1" class="bg-[#3D2B1F] text-white text-[10px] rounded-full w-5 h-5 flex items-center justify-center ml-0.5">
        {{ effect.stacks }}
      </span>
    </PopoverButton>

    <Transition
      enter="transition duration-100 ease-out"
      enterFrom="opacity-0 scale-95"
      enterTo="opacity-100 scale-100"
      leave="transition duration-75 ease-in"
      leaveFrom="opacity-100"
      leaveTo="opacity-0"
    >
      <PopoverPanel class="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 z-50">
        <div class="bg-[#3D2B1F] text-[#FBF7F0] text-xs px-3 py-2 rounded-lg shadow-lg whitespace-nowrap">
          <template v-if="isPositive">
            <span class="text-[#6DBF7C]">增益</span>
            <span v-if="effect.name"> · {{ effect.name }}</span>
            <span v-if="effect.steps > 0"> +{{ effect.steps }}级</span>
          </template>
          <template v-else>
            <span class="text-[#D4534A]">异常</span>
            <span v-if="effect.name"> · {{ effect.name }}</span>
            <span v-if="effect.stacks > 1"> ×{{ effect.stacks }}</span>
          </template>
          <div v-if="sourceName" class="text-[#C9A96E] mt-1">
            来源: {{ sourceName }}
          </div>
        </div>
      </PopoverPanel>
    </Transition>
  </Popover>
</template>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/EffectCard.vue
git commit -m "新增: EffectCard 组件 — 增益/异常卡片 + Popover 来源 tooltip"
```

---

### Task 7: 新建 SkillButton 组件

**Files:**
- Create: `frontend/src/components/SkillButton.vue`

- [ ] **Step 1: 实现 SkillButton.vue**

```vue
<!-- frontend/src/components/SkillButton.vue -->
<script setup>
import { computed } from 'vue'
import { Popover, PopoverButton, PopoverPanel, Transition } from '@headlessui/vue'
import gsap from 'gsap'

const props = defineProps({
  skill: { type: Object, required: true },  // SkillSummary from API
  skillMeta: { type: Object, default: null }, // Full skill metadata from /api/skills
  disabled: { type: Boolean, default: false },
  energyInsufficient: { type: Boolean, default: false },
  selected: { type: Boolean, default: false },
})

const emit = defineEmits(['select'])

const btnRef = ref(null)

const element = computed(() => props.skillMeta?.element || '')
const skillType = computed(() => props.skillMeta?.skill_type || '')
const power = computed(() => props.skill.effective_power || 0)
const energyCost = computed(() => props.skill.effective_energy_cost ?? props.skill.base_energy_cost ?? 0)
const priority = computed(() => props.skillMeta?.priority || 0)
const description = computed(() => props.skillMeta?.description || '')
const cooldown = computed(() => props.skill.cooldown || 0)

const elementColorClass = computed(() => {
  return element.value ? `element-${element.value}` : ''
})

function onPointerEnter() {
  if (props.disabled) return
  gsap.to(btnRef.value, {
    y: -4,
    boxShadow: '0 0 12px rgba(201,169,110,0.3)',
    duration: 0.15,
    ease: 'power2.out',
  })
}

function onPointerLeave() {
  gsap.to(btnRef.value, {
    y: 0,
    boxShadow: '0 2px 0 rgba(61,43,31,0.15)',
    duration: 0.15,
    ease: 'power2.out',
  })
}

function onClick() {
  if (props.disabled) return
  gsap.fromTo(btnRef.value,
    { scale: 1 },
    { scale: 0.9, duration: 0.1, yoyo: true, repeat: 1, ease: 'power2.inOut' }
  )
  emit('select', props.skill.name)
}
</script>

<template>
  <Popover class="relative">
    <PopoverButton
      v-slot="{ open }"
      as="template"
    >
      <button
        ref="btnRef"
        @click="onClick"
        @pointerenter="onPointerEnter"
        @pointerleave="onPointerLeave"
        :disabled="disabled"
        :class="[
          'w-full text-left px-3 py-2 rounded-lg border font-medium transition-colors skill-glow',
          disabled
            ? 'bg-[#E8E0D5] border-[#D4C8B8] text-[#B0A595] cursor-not-allowed'
            : energyInsufficient
              ? 'bg-[#FFF3E0] border-[#D4534A]/40 text-[#D4534A] hover:border-[#D4534A]'
              : selected
                ? 'bg-[#E8F5E9] border-[#5C8D6E] text-[#5C8D6E] ring-1 ring-[#5C8D6E]/30'
                : 'bg-white border-[#D4C8B8] text-[#3D2B1F] hover:border-[#C9A96E]',
          open ? 'border-[#C9A96E]' : ''
        ]"
      >
        <!-- Skill Header -->
        <div class="flex items-center gap-1.5">
          <span
            v-if="skill.skill_index !== undefined"
            class="text-xs text-[#6B5E4F] font-mono"
          >[{{ skill.skill_index + 1 }}]</span>
          <span
            v-if="element"
            :class="['elem-tag', elementColorClass]"
          >{{ element }}</span>
          <span class="text-sm font-bold truncate">{{ skill.name }}</span>
          <span v-if="priority > 0" class="text-xs text-[#C9A96E] font-bold">☆+{{ priority }}</span>
        </div>

        <!-- Skill Stats -->
        <div class="flex items-center gap-2 mt-1 text-xs text-[#6B5E4F]">
          <template v-if="power > 0">
            <span>⚡{{ power }}威</span>
          </template>
          <span v-if="skillType" class="text-[#8D6E63]">{{ { '物攻': '物理', '魔攻': '魔法', '防御': '防御', '辅助': '辅助' }[skillType] || skillType }}</span>
          <span>⚡{{ energyCost }}费</span>
          <span v-if="skill.position_power_bonus > 0" class="text-[#5C8D6E]">+{{ skill.position_power_bonus }}</span>
        </div>

        <!-- Warnings -->
        <div v-if="energyInsufficient && !disabled" class="mt-1 text-xs text-[#D4534A] font-bold">能量不足</div>
        <div v-if="cooldown > 0" class="mt-1 text-xs text-[#EF6C00] font-bold">冷却中 ({{ cooldown }}回合)</div>
      </button>
    </PopoverButton>

    <Transition
      enter="transition duration-100 ease-out"
      enterFrom="opacity-0 scale-95"
      enterTo="opacity-100 scale-100"
      leave="transition duration-75 ease-in"
      leaveFrom="opacity-100"
      leaveTo="opacity-0"
    >
      <PopoverPanel class="absolute bottom-full left-1/2 -translate-x-1/2 mb-3 z-50">
        <div class="bg-[#3D2B1F] text-[#FBF7F0] text-xs px-4 py-3 rounded-xl shadow-lg max-w-xs">
          <div class="font-bold text-sm mb-1">{{ skill.name }}</div>
          <div class="text-[#C9A96E] mb-1">
            {{ element }} · {{ { '物攻': '物理攻击', '魔攻': '魔法攻击', '防御': '防御', '辅助': '辅助' }[skillType] || skillType }}
            <template v-if="power > 0"> · {{ power }}威力</template>
            · {{ energyCost }}能量
          </div>
          <div v-if="description" class="text-[#D4C8B8] leading-relaxed">{{ description }}</div>
        </div>
      </PopoverPanel>
    </Transition>
  </Popover>
</template>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/SkillButton.vue
git commit -m "新增: SkillButton 组件 — 技能卡片 + GSAP 悬浮弹跳 + Popover 技能详情"
```

---

### Task 8: 新建 TypeChart 组件

**Files:**
- Create: `frontend/src/components/TypeChart.vue`

- [ ] **Step 1: 实现 TypeChart.vue**

```vue
<!-- frontend/src/components/TypeChart.vue -->
<script setup>
import { computed, ref } from 'vue'
import { Popover, PopoverButton, PopoverPanel, Transition } from '@headlessui/vue'

const props = defineProps({
  typeChart: { type: Object, required: true },
})

const elements = computed(() => {
  const keys = Object.keys(props.typeChart)
  return keys.length > 0 ? keys : ['火','水','草','光','暗','龙','电','冰','虫','毒','土','地','石','钢','翼','幻','妖','武','普']
})

const hoveredCell = ref(null)

function effectivenessColor(v) {
  if (v === 0) return 'bg-[#E53935]/60'
  if (v >= 2) return 'bg-[#43A047]/80'
  if (v <= 0.5) return 'bg-[#EF6C00]/60'
  return 'bg-[#D4C8B8]/40'
}

function effectivenessText(v) {
  if (v === undefined || v === null) return '-'
  if (v === 0) return '0'
  return '×' + v
}
</script>

<template>
  <div class="p-3">
    <div class="text-xs font-bold text-[#3D2B1F] mb-2 font-[family-name:var(--font-title)]">属性克制</div>

    <!-- Mini Heatmap -->
    <div class="overflow-x-auto">
      <div class="inline-grid gap-px bg-[#D4C8B8] rounded-lg overflow-hidden"
           :style="{ gridTemplateColumns: `auto repeat(${elements.length}, 1fr)` }">
        <!-- Column headers -->
        <div class="bg-[#FBF7F0] p-0.5"></div>
        <div
          v-for="el in elements"
          :key="'col-' + el"
          class="bg-[#FBF7F0] p-0.5 text-center"
        >
          <span
            :class="['elem-tag text-[9px]', `element-${el}`]"
          >{{ el }}</span>
        </div>

        <!-- Rows -->
        <template v-for="atkEl in elements" :key="'row-' + atkEl">
          <div class="bg-[#FBF7F0] p-0.5 flex items-center justify-end pr-1">
            <span :class="['elem-tag text-[9px]', `element-${atkEl}`]">{{ atkEl }}</span>
          </div>
          <Popover
            v-for="defEl in elements"
            :key="`${atkEl}-${defEl}`"
            class="relative"
          >
            <PopoverButton class="block w-5 h-5 transition-transform hover:scale-125">
              <div
                :class="[
                  'w-full h-full rounded-sm',
                  effectivenessColor((typeChart[atkEl] || {})[defEl] ?? 1.0)
                ]"
              ></div>
            </PopoverButton>

            <Transition
              enter="transition duration-75 ease-out"
              enterFrom="opacity-0 scale-90"
              enterTo="opacity-100 scale-100"
              leave="transition duration-50"
              leaveFrom="opacity-100"
              leaveTo="opacity-0"
            >
              <PopoverPanel class="absolute z-50 bg-[#3D2B1F] text-[#FBF7F0] text-xs px-2 py-1 rounded shadow-lg whitespace-nowrap -translate-x-1/2 -translate-y-full -mt-1">
                {{ atkEl }} → {{ defEl }}: {{ effectivenessText((typeChart[atkEl] || {})[defEl]) }}
              </PopoverPanel>
            </Transition>
          </Popover>
        </template>
      </div>
    </div>

    <!-- Legend -->
    <div class="flex gap-3 mt-2 text-[10px] text-[#6B5E4F]">
      <span class="flex items-center gap-1"><span class="w-3 h-3 rounded-sm bg-[#43A047]/80 inline-block"></span>克制</span>
      <span class="flex items-center gap-1"><span class="w-3 h-3 rounded-sm bg-[#EF6C00]/60 inline-block"></span>抵抗</span>
      <span class="flex items-center gap-1"><span class="w-3 h-3 rounded-sm bg-[#E53935]/60 inline-block"></span>无效</span>
    </div>
  </div>
</template>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/TypeChart.vue
git commit -m "新增: TypeChart 组件 — 18×18 属性克制缩略热力图 + Popover 放大"
```

---

### Task 9: 新建 TimelineReplay 组件

**Files:**
- Create: `frontend/src/components/TimelineReplay.vue`

- [ ] **Step 1: 实现 TimelineReplay.vue**

```vue
<!-- frontend/src/components/TimelineReplay.vue -->
<script setup>
import { ref, computed, watch, onUnmounted } from 'vue'
import { Dialog, DialogPanel, DialogTitle, Transition } from '@headlessui/vue'
import { useBattleStore } from '../stores/battle.js'
import gsap from 'gsap'

const props = defineProps({
  isOpen: { type: Boolean, default: false },
})

const emit = defineEmits(['close'])

const battle = useBattleStore()

const isAutoPlaying = ref(false)
const currentReplayTurn = ref(0)
let autoPlayTimer = null

const isActive = computed(() => props.isOpen)

const snapshots = computed(() => battle.turnSnapshots)
const maxTurn = computed(() => {
  if (snapshots.value.length > 0) return snapshots.value[snapshots.value.length - 1].turn
  return battle.turn
})

const pastSnapshots = computed(() => {
  return snapshots.value.filter(s => s.turn <= currentReplayTurn.value)
})

function seekTo(turn) {
  currentReplayTurn.value = Math.max(0, Math.min(turn, maxTurn.value))
  battle.setReplayTurn(currentReplayTurn.value)
}

function stepBack() {
  seekTo(currentReplayTurn.value - 1)
}

function stepForward() {
  seekTo(currentReplayTurn.value + 1)
}

function toggleAutoPlay() {
  if (isAutoPlaying.value) {
    stopAutoPlay()
  } else {
    startAutoPlay()
  }
}

function startAutoPlay() {
  isAutoPlaying.value = true
  autoPlayTimer = setInterval(() => {
    if (currentReplayTurn.value >= maxTurn.value) {
      stopAutoPlay()
      return
    }
    seekTo(currentReplayTurn.value + 1)
  }, 1500)
}

function stopAutoPlay() {
  isAutoPlaying.value = false
  if (autoPlayTimer) {
    clearInterval(autoPlayTimer)
    autoPlayTimer = null
  }
}

function close() {
  stopAutoPlay()
  battle.replayMode = false
  emit('close')
}

onUnmounted(() => {
  stopAutoPlay()
})
</script>

<template>
  <Transition
    enter="transition duration-200 ease-out"
    enterFrom="opacity-0"
    enterTo="opacity-100"
    leave="transition duration-150 ease-in"
    leaveFrom="opacity-100"
    leaveTo="opacity-0"
  >
    <Dialog v-if="isActive" :open="isActive" @close="close" class="relative z-50">
      <!-- Backdrop -->
      <div class="fixed inset-0 bg-[#3D2B1F]/60" aria-hidden="true" />

      <div class="fixed inset-0 flex items-center justify-center p-4">
        <DialogPanel class="w-full max-w-2xl bg-[#FBF7F0] rounded-2xl border border-[#D4C8B8] shadow-xl overflow-hidden">
          <!-- Header -->
          <div class="px-6 py-4 border-b border-[#D4C8B8] flex items-center justify-between">
            <DialogTitle class="text-lg font-bold text-[#3D2B1F] font-[family-name:var(--font-title)]">
              ⏸ 回合时间线回放
            </DialogTitle>
            <button @click="close" class="text-[#6B5E4F] hover:text-[#3D2B1F] text-xl leading-none">&times;</button>
          </div>

          <!-- Timeline -->
          <div class="px-6 py-6">
            <!-- Turn indicator -->
            <div class="text-center mb-4">
              <span class="text-2xl font-bold text-[#3D2B1F] font-[family-name:var(--font-title)]">
                回合 {{ currentReplayTurn }}
              </span>
              <span class="text-sm text-[#6B5E4F] ml-2">/ {{ maxTurn }}</span>
            </div>

            <!-- Timeline dots -->
            <div class="relative h-16 flex items-center">
              <!-- Track -->
              <div class="absolute left-0 right-0 h-1 bg-[#D4C8B8] rounded-full"></div>
              <div
                class="absolute left-0 h-1 bg-[#C9A96E] rounded-full transition-all duration-300"
                :style="{ width: maxTurn > 0 ? (currentReplayTurn / maxTurn * 100) + '%' : '0%' }"
              ></div>

              <!-- Dots -->
              <div class="absolute left-0 right-0 flex justify-between px-2">
                <div
                  v-for="snap in snapshots"
                  :key="snap.turn"
                  class="relative group cursor-pointer"
                  :style="{ left: maxTurn > 0 ? (snap.turn / maxTurn * 100) + '%' : '0%' }"
                >
                  <button
                    @click="seekTo(snap.turn)"
                    :class="[
                      'w-3 h-3 rounded-full transition-all',
                      snap.turn <= currentReplayTurn
                        ? 'bg-[#C9A96E] scale-110'
                        : 'bg-[#D4C8B8] hover:bg-[#C9A96E]/60',
                      snap.turn === currentReplayTurn ? 'pulse-dot scale-125' : ''
                    ]"
                  ></button>
                </div>
              </div>
            </div>

            <!-- Controls -->
            <div class="flex items-center justify-center gap-3 mt-6">
              <button @click="stepBack" :disabled="currentReplayTurn <= 0"
                class="w-10 h-10 rounded-full bg-[#5C8D6E] text-white flex items-center justify-center text-lg disabled:opacity-30 disabled:cursor-not-allowed hover:bg-[#4A7D5E] transition-colors">◀</button>

              <button @click="toggleAutoPlay"
                :class="[
                  'px-5 py-2 rounded-xl font-bold text-sm transition-colors',
                  isAutoPlaying
                    ? 'bg-[#D4534A] text-white hover:bg-[#C62828]'
                    : 'bg-[#C9A96E] text-white hover:bg-[#B0985D]'
                ]">
                {{ isAutoPlaying ? '⏸ 停止' : '▶ 自动播放' }}
              </button>

              <button @click="stepForward" :disabled="currentReplayTurn >= maxTurn"
                class="w-10 h-10 rounded-full bg-[#5C8D6E] text-white flex items-center justify-center text-lg disabled:opacity-30 disabled:cursor-not-allowed hover:bg-[#4A7D5E] transition-colors">▶</button>
            </div>

            <!-- Snapshot summary -->
            <div v-if="snapshots.find(s => s.turn === currentReplayTurn)" class="mt-6 p-4 bg-[#F0EDE5] rounded-xl border border-[#D4C8B8]">
              <div class="text-xs font-bold text-[#3D2B1F] mb-2">回合 {{ currentReplayTurn }} 快照</div>
              <div class="grid grid-cols-2 gap-3 text-xs text-[#6B5E4F]">
                <div>
                  <span class="font-bold">我方 HP:</span>
                  {{ snapshots.find(s => s.turn === currentReplayTurn)?.self_sprite?.current_hp || '?' }}
                </div>
                <div>
                  <span class="font-bold">对方 HP:</span>
                  {{ snapshots.find(s => s.turn === currentReplayTurn)?.opp_sprite?.current_hp || '?' }}
                </div>
              </div>
            </div>
          </div>
        </DialogPanel>
      </div>
    </Dialog>
  </Transition>
</template>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/TimelineReplay.vue
git commit -m "新增: TimelineReplay 组件 — 回合时间线回放面板 (Dialog + GSAP + 拖拽圆点)"
```

---

### Task 10: 后端 — 新增 TurnSnapshot schema + API 响应

**Files:**
- Modify: `backend/api/schemas.py`
- Modify: `backend/api/main.py`

- [ ] **Step 1: 在 schemas.py 中添加 TurnSnapshot**

在 `backend/api/schemas.py` 的 BattleState 后面添加：

```python
# ═══════════════════════════════════════════════════════════════════
# 回合快照（回放）
# ═══════════════════════════════════════════════════════════════════

class SnapshotSprite(BaseModel):
    name: str
    current_hp: int
    max_hp: int
    energy: int
    is_fainted: bool
    effects: list[EffectSummary]
    skills: list[SkillSummary]


class TurnSnapshot(BaseModel):
    turn: int
    self_sprite: SnapshotSprite
    opp_sprite: SnapshotSprite
    log_entries: list[str]
```

- [ ] **Step 2: 在 /api/battle/action 响应中生成 turn_snapshot**

在 `backend/api/main.py` 的 `battle_action` 函数中，构建响应前添加 snapshot 构建：

```python
# 在 serialize_battle_state 调用后、return 前添加
def _build_turn_snapshot(battle: Battle, turn_log: list[str]) -> schemas.TurnSnapshot:
    sa = battle.player_a.active
    sb = battle.player_b.active

    def _snapshot_sprite(s) -> schemas.SnapshotSprite:
        return schemas.SnapshotSprite(
            name=s.name,
            current_hp=s.current_hp,
            max_hp=s.max_hp,
            energy=s.energy,
            is_fainted=s.is_fainted,
            effects=[schemas.EffectSummary(
                name=e.name, category=e.category, stacks=e.stacks, steps=e.steps,
            ) for e in s.effects if e.name != '首领化'],
            skills=[schemas.SkillSummary(
                name=sk.name,
                skill_index=i,
                base_power=sk.base.power,
                effective_power=sk.base.power + sk.power_mod,
                position_power_bonus=0,
                base_energy_cost=sk.base.energy_cost,
                effective_energy_cost=sk.base.energy_cost + sk.energy_cost_mod,
                cooldown=sk.cooldown,
                transmission=0,
                main_axis=False,
            ) for i, sk in enumerate(s.skills)],
        )

    return schemas.TurnSnapshot(
        turn=battle.turn,
        self_sprite=_snapshot_sprite(sa),
        opp_sprite=_snapshot_sprite(sb),
        log_entries=list(turn_log),
    )

# 在 return 前添加:
turn_snap = _build_turn_snapshot(battle, turn_log)

return {
    "state": serialize_battle_state(battle, req.session_id),
    "log": turn_log,
    "turn_snapshot": turn_snap,
}
```

Also add the same snapshot in `debug_action` function.

- [ ] **Step 3: 运行后端测试验证无回归**

```bash
python -m pytest backend/engine/test_integration.py backend/engine/test_battle_replay.py -x --tb=short
```

Expected: 86 tests pass

- [ ] **Step 4: Commit**

```bash
git add backend/api/schemas.py backend/api/main.py
git commit -m "新增: TurnSnapshot schema + /api/battle/action 响应包含回合快照"
```

---

### Task 11: 重写 BattleLog.vue

**Files:**
- Rewrite: `frontend/src/components/BattleLog.vue`

- [ ] **Step 1: 实现自然奇幻风 BattleLog**

```vue
<!-- frontend/src/components/BattleLog.vue -->
<script setup>
import { ref, watch, nextTick } from 'vue'

const props = defineProps({
  logs: { type: Array, required: true },
})

const logContainer = ref(null)

watch(() => props.logs, async () => {
  await nextTick()
  if (logContainer.value) {
    logContainer.value.scrollTop = logContainer.value.scrollHeight
  }
}, { deep: true })

function isTurnHeader(text) {
  return /^\[回合\d+\]/.test(text)
}

function formatLogClass(text) {
  if (/伤害|扣血|HP\s*[-−]/.test(text)) return 'text-[#D4534A]'
  if (/回复|治疗|HP\s*\+/.test(text)) return 'text-[#6DBF7C]'
  if (/中毒|灼烧|冻结|麻痹|睡眠|异常|诅咒|寄生/.test(text)) return 'text-[#4A3B5C]'
  if (/打断|应对|反击/.test(text)) return 'text-[#EF6C00]'
  if (/力竭/.test(text)) return 'text-[#D4534A] font-bold'
  if (/增益|提升|增加/.test(text)) return 'text-[#5C8D6E]'
  return 'text-[#6B5E4F]'
}

function formatLogHtml(text) {
  return text
    .replace(/(\d+)\s*点伤害/g, '<b class="text-[#D4534A]">$1点伤害</b>')
    .replace(/回复\s*(\d+)\s*点HP/g, '回复 <b class="text-[#6DBF7C]">$1点HP</b>')
    .replace(/力竭/g, '<b class="text-[#D4534A]">力竭</b>')
    .replace(/^\[(回合\d+)\](.*)/, '<span class="text-[#C9A96E] font-bold">[$1]</span>$2')
}
</script>

<template>
  <div class="bg-[#FBF7F0] border-l border-[#D4C8B8] flex flex-col overflow-hidden h-full rounded-r-2xl">
    <!-- Header -->
    <div class="px-4 py-3 border-b border-[#D4C8B8]">
      <span class="text-sm font-bold text-[#3D2B1F] font-[family-name:var(--font-title)]">战斗日志</span>
    </div>

    <!-- Logs -->
    <div ref="logContainer" class="flex-1 overflow-y-auto p-3 space-y-1.5">
      <div v-if="logs.length === 0" class="text-xs text-[#B0A595] italic p-3">
        等待战斗开始...
      </div>

      <div
        v-for="(log, idx) in logs"
        :key="idx"
        class="text-xs leading-relaxed px-3 py-1.5 rounded-lg font-mono"
        :class="[
          formatLogClass(log),
          isTurnHeader(log)
            ? 'bg-[#F0EDE5] border-l-2 border-[#C9A96E] font-bold mt-2 first:mt-0'
            : 'hover:bg-[#F5F2EC]'
        ]"
        v-html="formatLogHtml(log)"
      ></div>
    </div>
  </div>
</template>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/BattleLog.vue
git commit -m "重构: BattleLog — 自然奇幻风样式，按日志类型着色"
```

---

### Task 12: 重写 TeamSelection.vue

**Files:**
- Rewrite: `frontend/src/components/TeamSelection.vue`

- [ ] **Step 1: 实现自然奇幻风 TeamSelection**

(Significant rewrite using Tailwind design tokens, Dialog for sprite picker, Popover for skill details, Listbox for bloodline/item. Full component ~300 lines — will reference the current functional structure but replace all dark-theme CSS with token-driven classes.)

Key changes:
- Replace `elementColors` object with `element-{name}` CSS classes from elements.css
- Use Headless UI Dialog for the sprite picker modal instead of hand-rolled Transition
- Replace bloodline `<select>` with Headless UI Listbox
- Apply `.card`, `.title`, `.btn`, `.btn-primary` token classes
- Background: `bg-[#FBF7F0]` instead of `bg-[#1a1d23]`
- Text: `text-[#3D2B1F]` instead of `text-[#cdd6e0]`

- [ ] **Step 2: Vite 构建验证无错误**

```bash
cd frontend; npx vite build 2>&1
```

Expected: Build succeeds

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/TeamSelection.vue
git commit -m "重构: TeamSelection — 自然奇幻风 + Dialog 精灵选择 + Listbox 血脉/道具"
```

---

### Task 13: 重写 BattleArena.vue

**Files:**
- Rewrite: `frontend/src/components/BattleArena.vue`

- [ ] **Step 1: 实现新 BattleArena（Pinia + GSAP + 三栏布局）**

(Same approach as Task 12 — complete rewrite using new stores, composable, and sub-components. The key structural change: three-column layout with self sprite on right, opp sprite on left, battle arena in center, log + type chart on right.)

Key changes:
- Import and use `useBattleStore`, `useSpriteAssetStore`
- Import and use `useSpriteAnim` composable for sprite refs
- Import SpriteCard, EffectCard, SkillButton, TypeChart sub-components
- Implement three-column layout per spec section 2
- Replace prop drilling with store reads
- Add GSAP animation watchers on sprite state changes

- [ ] **Step 2: Vite 构建验证无错误**

```bash
cd frontend; npx vite build 2>&1
```

Expected: Build succeeds

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/BattleArena.vue
git commit -m "重构: BattleArena — Pinia stores + GSAP 动画 + 三栏自然奇幻风布局"
```

---

### Task 14: 重写 App.vue + style.css

**Files:**
- Rewrite: `frontend/src/App.vue`
- Rewrite: `frontend/src/style.css`

- [ ] **Step 1: 重写 style.css — 引入 design tokens**

```css
/* frontend/src/style.css */
@import "tailwindcss";
@import "./design/tokens.css";
@import "./design/elements.css";
@import "./design/animations.css";

:root {
  font-family: 'Verdana', 'Noto Sans SC', 'Source Han Sans SC', sans-serif;
  font-size: 14px;
  line-height: 1.5;
  font-weight: 400;

  color: var(--text-primary);
  background-color: var(--bg-main);

  font-synthesis: none;
  text-rendering: optimizeLegibility;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

body {
  margin: 0;
  min-width: 320px;
  min-height: 100vh;
  background-image:
    radial-gradient(ellipse at 20% 50%, rgba(201,169,110,0.06) 0%, transparent 50%),
    radial-gradient(ellipse at 80% 20%, rgba(92,141,110,0.04) 0%, transparent 50%);
}

/* 羊皮纸纹理（仅在支持 backdrop-filter 的设备） */
@supports (backdrop-filter: blur(1px)) {
  body::before {
    content: '';
    position: fixed;
    inset: 0;
    opacity: 0.03;
    background-image: url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%233D2B1F' fill-opacity='0.4'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E");
    pointer-events: none;
    z-index: 0;
  }
}

/* 复古滚动条 */
::-webkit-scrollbar {
  width: 8px;
}
::-webkit-scrollbar-track {
  background: #F0EDE5;
}
::-webkit-scrollbar-thumb {
  background: #C9A96E;
  border-radius: 4px;
}
::-webkit-scrollbar-thumb:hover {
  background: #B0985D;
}
```

- [ ] **Step 2: 重写 App.vue — 使用 Pinia stores**

```vue
<!-- frontend/src/App.vue -->
<script setup>
import { ref, onMounted, watch } from 'vue'
import { useBattleStore } from './stores/battle.js'
import { useTeamStore } from './stores/team.js'
import { useSpriteAssetStore } from './stores/spriteAssets.js'
import TeamSelection from './components/TeamSelection.vue'
import BattleArena from './components/BattleArena.vue'
import BattleLog from './components/BattleLog.vue'
import TimelineReplay from './components/TimelineReplay.vue'
import TypeChart from './components/TypeChart.vue'

const API_BASE = 'http://localhost:8000/api'

const battle = useBattleStore()
const team = useTeamStore()
const spriteAssets = useSpriteAssetStore()

const skillMap = ref({})
const typeChart = ref({})

const showTimeline = ref(false)

// 导入现有 TEST_TEAM / TEST_OPPONENT 数据
const TEST_TEAM = [
  { name: '棋契陛下', skills: ['冰锋横扫', '钢钻', '回旋踢', '三连破', '丰饶', '虫群智慧', '不可接触', '充分燃烧'] },
  { name: '迪莫', skills: ['闪击', '光球', '冥想', '主场优势', '晒太阳', '生日蛋糕', '龙吟'] },
  { name: '卡洛儿', skills: ['勾魂', '假寐', '冬至', '冰冻光线', '击鼓传花', '远程访问', '借用', '加大功率'] },
  { name: '游蛇魔使', skills: ['水炮', '听桥', '潮汐', '打湿', '休息回复', '冰墙', '力量增效', '丢冰块'] },
  { name: '黑羽夫人', skills: ['恶意逃离', '恶念交换', '欺诈契约', '隐藏条款', '落井下毒', '以毒攻毒', '偷袭'] },
  { name: '大头骨龙', skills: ['龙吟', '三鼓作气', '破绽', '硬门', '冰点', '羽毛舞', '杠杆置换', '根吸收'] },
]

const TEST_OPPONENT = [
  { name: '双灯鱼', skills: ['水炮', '求雨', '水刃', '潮汐', '休息回复', '冰冻光线'] },
  { name: '大头骨龙', skills: ['龙吟', '龙威', '三鼓作气', '硬门', '崩拳', '破绽'] },
  { name: '游蛇魔使', skills: ['听桥', '打湿', '冰墙', '力量增效', '暗箱操作', '等价交换'] },
  { name: '黑羽夫人', skills: ['恶意逃离', '偷袭', '羽毛舞', '落井下毒', '乘风', '突袭'] },
]

onMounted(async () => {
  try {
    const [skillsRes, chartRes] = await Promise.all([
      fetch(`${API_BASE}/skills`),
      fetch(`${API_BASE}/type-chart`)
    ])
    if (skillsRes.ok) {
      const data = await skillsRes.json()
      skillMap.value = data.skills || {}
    }
    if (chartRes.ok) {
      const data = await chartRes.json()
      typeChart.value = data.chart || {}
    }
  } catch (e) {
    console.error('Failed to load data:', e)
  }
})

async function handleStartBattle({ team, leadIndex, item }) {
  try {
    battle.isProcessing = true
    const res = await fetch(`${API_BASE}/battle/init`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ team, opponent_team: TEST_OPPONENT, lead_index: leadIndex, item: item || undefined })
    })
    if (!res.ok) throw new Error('战斗初始化失败')
    const data = await res.json()
    battle.updateFromResponse(data)
    battle.appendLogs(['战斗开始！'])
    battle.battlePhase = 'action'

    // 预加载双方精灵图
    const allNames = [
      ...(data.player_a?.team || []).map(s => s.name),
      ...(data.player_b?.team || []).map(s => s.name),
    ]
    spriteAssets.preloadTeam(allNames)
  } catch (error) {
    console.error('Error starting battle:', error)
    alert('初始化战斗失败，请确认后端已启动。')
  } finally {
    battle.isProcessing = false
  }
}

async function handleAction({ type, payload }) {
  if (battle.isProcessing || battle.isFinished) return

  try {
    battle.isProcessing = true
    const reqBody = { session_id: battle.sessionId, action_type: type }
    if (type === 'skill') reqBody.skill_name = payload
    if (type === 'switch') reqBody.switch_index = payload

    const res = await fetch(`${API_BASE}/battle/action`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(reqBody)
    })
    if (!res.ok) throw new Error('操作执行失败')

    const data = await res.json()
    battle.updateFromResponse(data.state)
    battle.appendLogs(data.log)
  } catch (error) {
    console.error('Error executing action:', error)
  } finally {
    battle.isProcessing = false
  }
}

async function handleQuickTest() {
  try {
    battle.isProcessing = true
    const res = await fetch(`${API_BASE}/battle/init`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ team: TEST_TEAM, opponent_team: TEST_OPPONENT, lead_index: 0 })
    })
    if (!res.ok) throw new Error('战斗初始化失败')
    const data = await res.json()
    battle.updateFromResponse(data)
    battle.appendLogs(['快速测试开始！'])
    battle.battlePhase = 'action'
  } catch (error) {
    console.error('Error starting battle:', error)
    alert('初始化战斗失败，请确认后端已启动。')
  } finally {
    battle.isProcessing = false
  }
}

function restartGame() {
  battle.resetBattle()
}
</script>

<template>
  <div class="min-h-screen relative" style="background: var(--bg-main); z-index: 1;">
    <!-- Header -->
    <header class="border-b px-5 py-3 flex items-center gap-3" style="background: linear-gradient(180deg, rgba(251,247,240,0.98) 0%, rgba(245,240,230,0.95) 100%); border-color: var(--border-default);">
      <div class="text-xl font-[family-name:var(--font-title)] font-bold text-[#3D2B1F] tracking-wide">
        ✦ 格斗小九 ✦
      </div>

      <template v-if="battle.battlePhase === 'selection'">
        <button
          @click="handleQuickTest"
          :disabled="battle.isProcessing"
          class="ml-auto px-4 py-1.5 bg-[#5C8D6E] hover:bg-[#4A7D5E] disabled:opacity-40 text-white text-xs font-bold rounded-xl transition-colors mr-2 shadow-[0_2px_0_rgba(61,43,31,0.15)]"
        >
          快速测试
        </button>
        <span class="text-xs text-[#B0A595]">v0.2</span>
      </template>

      <template v-if="battle.battlePhase === 'action' || battle.battlePhase === 'result'">
        <span class="text-sm text-[#6B5E4F]">回合 {{ battle.turn }}/{{ battle.maxTurn }}</span>
        <span class="text-[#D4C8B8]">|</span>
        <span class="text-sm text-[#6B5E4F]">
          {{ battle.weather ? `天气: ${battle.weather}` : '无天气' }}
        </span>

        <button
          @click="showTimeline = true"
          class="ml-auto px-4 py-1.5 bg-[#3D2B1F] hover:bg-[#5C3D2F] text-white text-xs font-bold rounded-xl transition-colors mr-2"
        >
          ⏸ 时间线回放
        </button>
      </template>
    </header>

    <!-- Main Content -->
    <main class="mx-auto" style="max-width: 1200px">
      <Transition name="page" mode="out-in">
        <TeamSelection
          v-if="battle.battlePhase === 'selection'"
          :skill-map="skillMap"
          @start-battle="handleStartBattle"
        />

        <div v-else-if="battle.battlePhase === 'action' || battle.battlePhase === 'result'" class="flex">
          <div class="flex-1 min-w-0">
            <BattleArena
              :skill-map="skillMap"
              @action="handleAction"
            />
          </div>
          <div class="w-80 flex-shrink-0 flex flex-col">
            <div class="flex-1">
              <BattleLog :logs="battle.logEntries" />
            </div>
            <div class="border-t border-[#D4C8B8]">
              <TypeChart :type-chart="typeChart" />
            </div>
          </div>
        </div>
      </Transition>
    </main>

    <!-- Timeline Replay Dialog -->
    <TimelineReplay
      :is-open="showTimeline"
      @close="showTimeline = false"
    />

    <!-- Game Over -->
    <Transition
      enter="transition duration-200 ease-out"
      enterFrom="opacity-0"
      enterTo="opacity-100"
      leave="transition duration-150"
      leaveFrom="opacity-100"
      leaveTo="opacity-0"
    >
      <div v-if="battle.battlePhase === 'result'" class="fixed inset-0 bg-[#3D2B1F]/60 flex items-center justify-center z-50">
        <div class="card p-10 text-center shadow-xl max-w-sm w-full mx-4">
          <div class="text-xs text-[#B0A595] tracking-widest uppercase mb-3">战斗结果</div>
          <h2 class="text-3xl font-bold mb-2 font-[family-name:var(--font-title)]"
              :class="battle.winner === '玩家' ? 'text-[#5C8D6E]' : 'text-[#D4534A]'">
            {{ battle.winner === '玩家' ? '胜利 ✦' : '失败' }}
          </h2>
          <p class="text-sm text-[#6B5E4F] mb-8">{{ battle.winner }} 赢得了战斗！</p>
          <button
            @click="restartGame"
            class="btn btn-primary px-8 py-2.5 text-sm"
          >
            再来一局
          </button>
        </div>
      </div>
    </Transition>
  </div>
</template>
```

- [ ] **Step 3: 启动 dev server 验证前端加载无错误**

```bash
cd frontend; npx vite --host 0.0.0.0
```

Open browser → check console for no import/rendering errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.vue frontend/src/style.css
git commit -m "重构: App.vue + style.css — Pinia 状态管理 + 自然奇幻风全视觉升级"
```

---

### Task 15: 端到端验证 + 最终提交

- [ ] **Step 1: 启动后端 API 服务器**

```bash
python -m backend.api.main
```

Expected: Server starts on port 8000

- [ ] **Step 2: 启动前端 dev server**

```bash
cd frontend; npx vite --host 0.0.0.0
```

Expected: Server starts on port 5173

- [ ] **Step 3: 手动验证对战流程**

1. Open `http://localhost:5173` in browser
2. Verify team selection UI loads with natural fantasy style
3. Click "快速测试" to start a battle
4. Verify three-column layout renders: opp sprite left, arena center, log+typechart right
5. Click a skill button — verify animation plays and log updates
6. Verify buff/debuff cards show with Popover tooltips
7. Click "时间线回放" — verify TimelineReplay dialog opens
8. Verify type chart heatmap renders and Popovers work
9. Play until game over — verify victory overlay

- [ ] **Step 4: 运行后端测试确保无回归**

```bash
python -m pytest backend/engine/test_integration.py backend/engine/test_battle_replay.py backend/vm/ -x --tb=short
```

Expected: All tests pass

- [ ] **Step 5: 最终 Commit**

```bash
git add -A
git status
git diff --stat
git commit -m "完成: 精灵对战可视化 — 自然奇幻风 UI 重构 + GSAP 动画 + Pinia 状态管理 + 时间线回放"
```

---

## Spec Coverage Checklist

| 规范章节 | 覆盖任务 |
|---------|---------|
| 一、设计 Token 体系 | Task 2 (tokens.css + elements.css) |
| 二、布局架构 (队伍选择) | Task 12 (TeamSelection 重写) |
| 二、布局架构 (对战界面) | Task 13 (BattleArena 重写) + Task 14 (App.vue) |
| 三、对战界面 — 精灵展示 | Task 5 (SpriteCard) + Task 4 (GSAP composable) |
| 三、对战界面 — HP/能量条 | Task 5 (SpriteCard) |
| 三、对战界面 — 增益/异常卡片 | Task 6 (EffectCard) |
| 三、对战界面 — 技能按钮 | Task 7 (SkillButton) |
| 三、对战界面 — 右栏(日志+克制) | Task 11 (BattleLog) + Task 8 (TypeChart) |
| 四、回合时间线回放 | Task 9 (TimelineReplay) + Task 10 (Backend) |
| 五、精灵资产管线 | Task 1 (解压 sprites + Vite 静态资源) |
| 六、队伍选择重设计 | Task 12 (TeamSelection) |
| 七、动画系统 GSAP | Task 4 (useSpriteAnim) |
| 七-B、状态管理 Pinia | Task 3 (3 stores) |
| 七-C、组件库 Headless UI | Tasks 6,7,8,9,12 (Popover/Dialog/Listbox/Transition) |
| 八、文件变更清单 | 全部 Tasks 1-15 |
