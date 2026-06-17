# 格斗小九 对战可视化 设计文档

**目标**：基于现有对战引擎，重构前端 UI 为自然奇幻风格，实现精灵可视化对战 + buff/debuff 清晰展示 + 属性克制可视化 + 回合时间线回放。

**架构**：设计 Token 体系驱动 + 组件重建（方案 B 升级），Vue 3 + Vite + Tailwind CSS + **GSAP**（动画引擎）+ **Pinia**（状态管理）+ **Headless UI**（可访问组件库）。不引入路由。

**素材**：467 PNG 精灵图（`_attachments/sprites/精灵名.png`）

---

## 一、设计 Token 体系

### 色板

| 角色 | 色值 | 用途 |
|------|------|------|
| 羊皮纸底 | `#FBF7F0` | 主背景，暖白仿纸 |
| 深木色 | `#3D2B1F` | 标题/主文字 |
| 苔绿 | `#5C8D6E` | 主按钮、我方元素 |
| 魔法金 | `#C9A96E` | 强调色、边框装饰 |
| 暗紫 | `#4A3B5C` | 敌方标识、暗属性 |
| 火红 | `#D4534A` | 伤害数字、警告 |
| 治愈绿 | `#6DBF7C` | 回复数字、增益 |

元素属性色（18 系别）沿用现有克制表配色，加深饱和度适配深色卡片。

### 字体

- 标题：衬线体（思源宋体 / Georgia），体现奇幻感
- 正文/数据：无衬线（思源黑体 / Verdana）

### 形状

- 圆角 8-12px（有机感）
- 卡片微内阴影模拟羊皮纸压印
- 按钮 2px bottom-shadow 模拟凸起
- 边框 1-2px，悬浮时柔和光晕

---

## 二、布局架构

单页应用，两个主视图通过 `<Transition>` 切换：

### 队伍选择界面 — 全宽居中卡片布局

```
顶栏：✦ 格斗小九 ✦ / ——— 队伍编成 ———
中部：6 槽位精灵卡片网格（2x3 或 3x2）
     每槽：精灵 PNG 小图(80×80) + 属性标签 + 名称 + 血脉技能名
     空槽：虚线边框 + "选择精灵"
     首发标记：★ 金色徽章
底部：血脉选择 / 道具选择 / 已保存队伍 / [开始战斗]
```

### 对战界面 — 三栏布局

```
顶栏：回合 N/150 | 天气指示器 | ⏸ 时间线回放按钮
左栏(窄)：对方精灵大图(180-220px) + HP条 + 能量条 + 增益/异常标签
中栏：对战区（技能特效区，覆盖精灵动画层）
右栏(窄)：战斗日志(70%) + 属性克制缩略热力图(30%)
底部：我方精灵大图 + HP条 + 能量条 + 增益/异常标签 + 技能按钮4个 + [聚能][道具][换宠]
```

---

## 三、对战界面

### 3.1 精灵展示

**图片加载**：Vite 静态资源，按需加载双方队伍精灵图（最多 12 张）。

**动画状态**：

| 状态 | 效果 | 触发 |
|------|------|------|
| 待机 | 微呼吸动画（scale 1.0→1.02 循环），底部椭圆阴影 | 回合等待 |
| 受击 | 红色叠加层(30% opacity, 200ms) + 水平抖动 | 受到伤害 |
| 攻击 | 向前位移 20px + 元素色背景光晕 | 释放技能 |
| 力竭 | 灰度 100% + opacity 60% + 向下位移 10px | HP=0 |
| 入场 | 从上方滑入 + 短暂白色闪光 | 入场/换宠 |

**后备方案**：图片加载失败 → 属性色圆形占位符 + 精灵名文字。

### 3.2 HP / 能量条

- HP条：粗 12px，深色底 + 渐变填充(绿→黄→红)，右侧数字 `85/120`
- 能量条：10 格分段渲染，每格独立。填充=魔法金光晕，空=暗灰
- 两条合并在一张"属性卡片"上，卡片左侧元素图标

### 3.3 增益/异常卡片

当前问题：buff/debuff 以紧凑文字标签堆叠，看不清层数和来源。

改为独立卡片，分开"增益"和"异常"两个区域：

```
增益区（苔绿底 + 上箭头图标）：
  ⚔ 攻击 +2         来源技能名
  🛡 防御 +1         来源技能名

异常区（暗紫底 + 火焰/骷髅/雪花图标）：
  🔥 灼烧 x2        来源技能名
  💀 中毒 x1        来源技能名
```

- 每张卡片右上角角标数字表示层数
- 同类型效果合并显示（`防御 +3` ≠ 3 张 `防御 +1` 卡）
- 卡片显示**来源技能名**

### 3.4 技能按钮

每张技能卡片含：元素 emoji + 类型标签 + 威力值 + 能量图标(⚡N) + 先手标记(☆)

- 能量不足：整卡灰色 + 红色"能量不足"提示
- 悬停：卡片上浮 4px + 柔和光晕 + tooltip 显示完整技能描述
- 点击：卡片缩放弹跳动画

### 3.5 右栏

- **战斗日志**(70%)：回合分隔线，伤害红色/回复绿色/状态紫色
- **属性克制热力图**(30%)：18×18 缩略热力图，悬停放大显示完整克制倍率

---

## 四、回合时间线回放

### 4.1 触发

点击顶栏 `⏸ 时间线回放` → 对战区中央弹出回放面板。

### 4.2 交互

- **拖拽圆点**：沿时间线拖到任意回合，精灵状态立即回溯到该时刻
- **◀ ▶ 按钮**：逐回合跳转
- **自动播放**：1.5s/回合，再次点击停止
- **快照恢复**：选择某回合后，双方 HP/能量/增益标签全部回溯

### 4.3 数据来源

- 引擎每回合产出 `Journal`（mutations 列表）
- 保存轻量快照：`{turn, hp_self, hp_opp, energy[], effects[], log_entries[]}`
- 最多 150 回合快照（约 50KB）
- 回放时根据快照渲染，不重新执行引擎

### 4.4 视觉

- 已过回合：魔法金实心圆
- 未来回合：虚线
- 当前回合圆形脉冲动画
- 详情卡片：半透明深木色底 + 白色文字

---

## 五、精灵资产管线

### 5.1 图片管理

- 467 PNG 精灵图，命名格式 `{精灵名}.png`
- 存放路径：`_attachments/sprites/`
- Vite 开发服务器直接提供静态资源

### 5.2 加载策略

- 按需加载：只加载双方队伍精灵图（最多 12 张），首屏约 2.4MB
- Vite 自动缓存，后续对局复用
- 路径映射：中文名 → Vite 资源 URL

### 5.3 性能预算

- 精灵图目标：单张 ≤ 200KB（PNG 压缩）
- 首屏加载：12 张 × 200KB = 2.4MB
- 后续对局：从浏览器缓存加载

---

## 六、队伍选择重设计

### 6.1 精灵槽位卡片

- **空槽位**：羊皮纸色虚线边框 + `+` 图标 + "选择精灵"
- **已选槽位**：精灵 PNG(80×80) + 属性标签 + 名称 + 血脉技能名
- **首发标记**：★ 金色徽章右上角
- **悬停**：卡片上浮，显示属性六维雷达缩略图

### 6.2 精灵选择弹窗

- 搜索栏 + 属性过滤标签（18 系别横向排列）
- 3 列精灵卡片网格，无限滚动
- 每卡：精灵图(小) + 名称 + 属性标签 + HP值
- 已选精灵标记 ✓

### 6.3 技能选择

- 选中精灵后展开可用技能列表
- 点击切换选中/取消，最多 4 个技能
- 已选技能高亮蓝边框，未选灰色
- 悬停显示完整技能描述

### 6.4 血脉/道具选择

- 血脉下拉 + 关联技能展示
- 道具卡片（含描述和冷却说明）

---

## 七、动画系统（GSAP）

引入 GSAP（GreenSock Animation Platform）替代纯 CSS 动画，提供精细的时间线编排、缓动函数和精灵变形能力。

### 核心用法

- **GSAP Timeline**：编排多阶段动画序列（如：入场→ 受击→ 退场）
- **gsap.to / gsap.fromTo**：精灵位移、缩放、旋转、透明度、滤镜
- **Easing**：`power2.out`（入场）、`back.out(1.2)`（弹跳）、`rough({ strength: 3 })`（受击抖动）
- **ScrollTrigger 不用**：对战界面无滚动触发需求

### 动画规格

| 动画 | 实现 | 时长 |
|------|------|------|
| 精灵待机呼吸 | `gsap.to(sprite, { scale: 1.02, duration: 1, yoyo: true, repeat: -1, ease: 'sine.inOut' })` | 2s 循环 |
| 受击抖动 | `gsap.to(sprite, { x: ±5, duration: 0.05, repeat: 5, yoyo: true, ease: 'rough' })` | 250ms |
| 受击红闪 | `gsap.to(sprite, { filter: 'brightness(1.5) hue-rotate(-30deg)', duration: 0.1, yoyo: true })` | 200ms |
| 攻击前冲 + 光晕 | timeline: 前冲 20px → 元素色 boxShadow 爆发 → 回弹 | 400ms |
| 力竭灰化 | `gsap.to(sprite, { filter: 'grayscale(1)', opacity: 0.6, y: 10, duration: 0.5 })` | 500ms |
| 入场滑入 | `gsap.fromTo(sprite, { y: -60, opacity: 0 }, { y: 0, opacity: 1, ease: 'power2.out' })` | 400ms |
| HP/能量条过渡 | `gsap.to(bar, { width: newPercent, duration: 0.5, ease: 'power2.out' })` | 500ms |
| 技能按钮悬停 | `gsap.to(btn, { y: -4, boxShadow: glow, duration: 0.15 })` | 150ms |
| 卡片错开入场 | `gsap.fromTo(cards, { y: 20, opacity: 0 }, { y: 0, opacity: 1, stagger: 0.05, ease: 'back.out(1.2)' })` | stagger 50ms |
| 伤害数字弹出 | `gsap.fromTo(numEl, { scale: 0.5, opacity: 0 }, { scale: 1.3, opacity: 1, duration: 0.2 })` + 上浮消失 | 1s 总长 |

### Composable 封装

`frontend/src/composables/useSpriteAnim.js` — Vue composable，封装精灵动画方法：
```js
// 返回 { playHit, playAttack, playFaint, playEntry, playIdle }
const { playHit } = useSpriteAnim(spriteRef)
await playHit()  // 返回 Promise，动画完成后 resolve
```

---

## 七-B、状态管理（Pinia）

引入 Pinia 替代 props 传递 + composables 的散落状态，对战数据集中管理。

### Store 设计

**`useBattleStore`** — 对战核心状态

```js
{
  // 双方精灵
  selfSprite: SpriteState | null,
  oppSprite: SpriteState | null,
  
  // 队伍
  selfTeam: SpriteSummary[],    // 后备精灵
  oppTeam: SpriteSummary[],
  
  // 回合
  turn: number,
  maxTurn: number,
  weather: string | null,
  
  // 技能
  selfSkills: SkillSummary[],   // 4 个当前可用技能
  selectedSkill: number | null,  // 玩家选中的技能 index
  
  // 时间线回放
  turnSnapshots: TurnSnapshot[],  // 每回合快照
  replayMode: boolean,
  replayTurn: number,
  
  // UI 状态
  isProcessing: boolean,
  battlePhase: 'selection' | 'action' | 'result',
  winner: string | null,
  logEntries: LogEntry[],
}
```

**`useTeamStore`** — 队伍编成状态

```js
{
  slots: (SpriteSummary | null)[],  // 6 槽位
  selectedBloodline: string | null,
  selectedItem: string | null,
  savedTeams: SavedTeam[],
}
```

**`useSpriteAssetStore`** — 精灵图片缓存

```js
{
  cache: Map<string, string>,  // 精灵名 → Vite URL
  loading: Set<string>,
  errors: Set<string>,
  
  getSprite(name: string): string | null,
  preloadTeam(names: string[]): Promise<void>,
}
```

### 数据流

```
API /api/battle/action → JSON
  → battleStore.updateFromResponse(data)
    → selfSprite / oppSprite 更新
    → GSAP 动画触发 (watch sprite state changes)
    → turnSnapshots.push(snapshot)
    → logEntries 追加
```

---

## 七-C、组件库（Headless UI）

引入 Headless UI v2（Vue 版本）提供可访问的基础组件，保持 Tailwind 风格自由度：

| 组件 | 用途 |
|------|------|
| `Dialog` | 精灵选择弹窗、回合回放面板、对战结果覆盖层 |
| `Popover` | 技能 tooltip、增益/异常来源 tooltip |
| `Listbox` | 血脉下拉选择、道具下拉选择 |
| `Switch` | 调试模式开关、自动播放开关 |
| `Transition` | 弹窗/面板的入场/退场动画（已有 Vue Transition，Headless UI 提供更强的键盘焦点管理） |

所有 Headless UI 组件是 **无头（headless）的**——只有逻辑和可访问性，样式完全由 Tailwind Token 控制，与自然奇幻风无缝配合。

---

## 八、文件变更清单

### 依赖新增

```json
// package.json 新增
{
  "gsap": "^3.12",
  "pinia": "^2.1",
  "@headlessui/vue": "^2.0"
}
```

### 新建

| 文件 | 说明 |
|------|------|
| `frontend/src/design/tokens.css` | CSS 变量定义（色板/字体/间距/圆角/阴影） |
| `frontend/src/design/animations.css` | 辅助 CSS（GSAP 不覆盖的静态样式） |
| `frontend/src/design/elements.css` | 元素属性色（18 系别） |
| `frontend/src/stores/battle.js` | Pinia — 对战核心状态 |
| `frontend/src/stores/team.js` | Pinia — 队伍编成 |
| `frontend/src/stores/spriteAssets.js` | Pinia — 精灵图片缓存 + 按需加载 |
| `frontend/src/composables/useSpriteAnim.js` | GSAP 精灵动画 composable |
| `frontend/src/components/TimelineReplay.vue` | 回合时间线回放面板（Dialog + GSAP） |
| `frontend/src/components/TypeChart.vue` | 18×18 属性克制缩略热力图（Popover 放大） |
| `frontend/src/components/SpriteCard.vue` | 精灵卡片（复用：选队/对战） |
| `frontend/src/components/EffectCard.vue` | 增益/异常卡片（Popover 来源 tooltip） |
| `frontend/src/components/SkillButton.vue` | 技能按钮（Popover 技能详情 tooltip） |

### 重写

| 文件 | 说明 |
|------|------|
| `frontend/src/components/BattleArena.vue` | 对战主组件，使用 Pinia stores + GSAP 动画 |
| `frontend/src/components/TeamSelection.vue` | 队伍选择，Dialog 弹出精灵选择 + Listbox 血脉/道具 |
| `frontend/src/components/BattleLog.vue` | 战斗日志，增强样式 |
| `frontend/src/App.vue` | 引入 Pinia plugin + 初始化 stores |
| `frontend/src/style.css` | 视觉升级（引入 design tokens） |

### 后端变更

| 文件 | 说明 |
|------|------|
| `backend/api/main.py` | `/api/battle/action` 响应中追加 `turn_snapshot` |
| `backend/api/schemas.py` | 新增 `TurnSnapshot` Pydantic model |

### 不修改

- `backend/vm/`、`backend/sim/`、`backend/engine/` — 引擎核心不动
- `frontend/src/main.js` — 只加 Pinia plugin 注册，架构不变

---

## 九、已知设计约束

1. **无音效**：首次迭代不做音频，CSS 动画负责全部视觉反馈
2. **无路由**：保持单页结构，不引入 Vue Router
3. **无状态管理库**：继续用 props + composables，不引入 Pinia
4. **精灵图加载失败**：所有精灵图展示点都有后备占位符
5. **467 张精灵图命名**：必须严格匹配 `精灵名.png`，编译期验证缺失文件
6. **18 系别**：克制表已有后端数据，前端只需渲染
