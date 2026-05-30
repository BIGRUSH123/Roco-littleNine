# 格斗小九 (Roco) — 洛克王国 PVP 对战模拟器

洛克王国世界 PVP 对战模拟器，提供可视化战斗回放与交互式对战体验。v1.0 聚焦模拟器核心功能。

## Quickstart

```bash
# 1. 安装后端依赖
pip install -e .

# 2. 启动后端 API 服务
python -m uvicorn backend.api.main:app --reload --port 8000

# 3. 启动前端开发服务器（新终端）
cd frontend
npm install
npm run dev

# 4. 浏览器打开 http://localhost:5173 即可开始对战
```

## 功能

- **可视化对战** — 精灵选择、技能释放、血量/能量变化的实时渲染
- **战斗回放** — 时间线回放，可逐步查看每回合详情
- **属性克制** — 完整的 18 属性克制表
- **特性系统** — 100+ 精灵特性，支持 JSON 数据驱动
- **队伍编辑** — 自由组建 6 精灵队伍

## Architecture

```
backend/api/       FastAPI 服务端（对战 API、精灵数据接口）
backend/engine/    VM 执行器、快照、回放、特性/印记/异常状态
backend/vm/        Battle VM 编译器管线（Parse → Expand → Validate → Execute）
backend/sim/       模拟层（精灵、技能、对战流程、回合记录）
backend/common/    共享模型、公式、常量、精灵数据库
frontend/src/      Vue 3 + Pinia + Tailwind CSS 前端 SPA
data/              精灵/技能/特性 JSON 数据
```

## Development

```bash
pip install -e ".[dev]"
pytest -x --tb=short       # 运行测试
ruff check .                # 代码检查
```

## License

MIT
