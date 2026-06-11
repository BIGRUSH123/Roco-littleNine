# 格斗小九前端

Vue 3 + Pinia + Tailwind CSS 前端 SPA，用于可视化对战、队伍编辑、回合时间线回放、对局导入导出、批量对战和 AI 对手选择。

## 开发启动

后端 API 默认运行在 `http://localhost:8000`：

```bash
python -m uvicorn backend.api.main:app --reload --port 8000
```

前端开发服务器：

```bash
cd frontend
npm install
npm run dev
```

浏览器打开 Vite 输出的地址，通常是 `http://localhost:5173`。

## 脚本

| 命令 | 说明 |
|---|---|
| `npm run dev` | 启动 Vite 开发服务器 |
| `npm run build` | 生产构建 |
| `npm run preview` | 本地预览生产构建 |

## 目录

| 路径 | 说明 |
|---|---|
| `src/App.vue` | 应用主入口 |
| `src/components/` | 对战界面、技能按钮、时间线、导入导出、批量结果、AI 选择等组件 |
| `src/stores/battle.js` | 对战状态和 API 调用 |
| `src/stores/spriteAssets.js` | 精灵素材状态 |
| `src/design/` | 设计 token、基础元素和动画 |
| `src/composables/` | 可复用组合逻辑 |
