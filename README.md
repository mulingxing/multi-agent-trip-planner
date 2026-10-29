# 智能旅行助手

这是基于 Datawhale [《Hello-Agents》第 13 章旅行助手示例](https://github.com/datawhalechina/hello-agents/tree/main/code/chapter13/helloagents-trip-planner)继续开发的学习项目，原项目作者为 Datawhale 的 Hello-Agents 贡献者。这里保留了原示例的 FastAPI、Vue 和高德 MCP 基础，增加了餐饮 Agent、行程结构校验、景点 POI 详情核验及相应的离线回归测试，并调整了前端展示与错误处理。原项目与本仓库均按 **CC BY-NC-SA 4.0** 分享，详见 [LICENSE](LICENSE) 和[来源与许可](#来源与许可)。

## 能做什么

- 用户填写目的地、日期、天数及偏好后，由景点、天气、酒店、餐饮四个检索 Agent 收集信息，再交给行程规划 Agent 生成多日计划。
- 后端解析模型返回的 JSON，用 Pydantic 和业务规则检查城市、日期、天数、每日景点与三餐；格式错误时最多要求模型修正一次。
- 对计划中的**每个景点**重新调用高德 POI 搜索和详情工具，核对名称、城市、POI ID 与经纬度。核验失败则返回错误，不展示模型猜测的景点坐标。
- 前端展示每日行程、天气、预算和景点地图，可导出 PDF。地图上的连线只是按游览顺序连接景点的示意线。

```text
旅行需求
  ├─ 景点搜索 Agent ─┐
  ├─ 天气查询 Agent ─┤
  ├─ 酒店推荐 Agent ─┼─> 行程规划 Agent ─> JSON/业务校验 ─> 景点 POI 核验 ─> 前端展示
  └─ 餐饮推荐 Agent ─┘
```

## 技术栈

| 部分 | 技术 |
| --- | --- |
| 后端 | Python、FastAPI、Pydantic、HelloAgents `SimpleAgent` |
| 地图数据 | 高德地图 MCP 服务；前端高德 JavaScript API |
| 前端 | Vue 3、TypeScript、Vite、Ant Design Vue |
| 验证 | `unittest` 离线模拟模型与 MCP 响应；前端类型检查和构建 |

## 本地运行

需要 Python 3.10+、Node.js 20+，以及可用的模型 API Key、高德 Web 服务 Key 与高德 JS API Key。高德 MCP 服务由 `uvx` 启动，后端依赖中已包含 `uv`。首次运行可能需要下载 Python 包和 MCP 服务。

### 1. 启动后端

以下命令在 `backend` 目录执行；Windows PowerShell 激活示例：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python run.py
```

编辑 `backend/.env`：填写 `LLM_MODEL_ID`、`LLM_API_KEY`、`LLM_BASE_URL` 和 `AMAP_API_KEY`。`UNSPLASH_ACCESS_KEY` 仅用于可选的景点图片。后端默认监听 `http://localhost:8000`，接口文档位于 `http://localhost:8000/docs`。

### 2. 启动前端

另开终端，在 `frontend` 目录执行：

```powershell
cd frontend
npm ci
Copy-Item .env.example .env
npm run dev
```

编辑 `frontend/.env`：填写 `VITE_AMAP_WEB_JS_KEY` 和 `VITE_AMAP_SECURITY_JS_CODE`，然后访问 `http://localhost:5173`。`VITE_API_BASE_URL` 默认指向本地后端。以 `VITE_` 开头的变量会进入浏览器构建产物，不能当作服务端密钥保存；请在地图平台为浏览器 Key 设置适当的域名限制和额度。真实 `.env`、构建产物和本地依赖均由 `.gitignore` 排除。

## 验证

后端离线测试在项目根目录运行，不连接真实模型或高德服务：

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

前端类型检查与构建：

```powershell
cd frontend
npm run build
```

联网生成行程还需要有效的模型和地图服务配置；离线测试通过不代表外部 API 的实时可用性。

## 接口与边界

- `POST /api/trip/plan` 是主要行程生成接口；`GET /health` 用于基础服务检查。
- 景点名称和坐标经高德 POI 搜索、详情二次核验；酒店、餐饮、天气和预算**没有**同等的详情核验，出行前仍需向官方渠道确认营业时间、价格与天气。
- 前端景点连线不计算道路距离或真实导航路线。当前 `AmapService` 中独立的 `/api/map/poi`、`/api/map/weather`、`/api/map/route` 接口仍有占位解析逻辑，可能返回空列表或空对象；不应将它们用于实际路线导航。

## 来源与许可

- 教程示例来源：[Datawhale / hello-agents，`code/chapter13/helloagents-trip-planner`](https://github.com/datawhalechina/hello-agents/tree/main/code/chapter13/helloagents-trip-planner)。感谢 Datawhale 及 Hello-Agents 贡献者提供原始示例。本仓库的改动内容已在开头说明。
- 使用的框架：[jjyaoao / HelloAgents](https://github.com/jjyaoao/HelloAgents)，依赖版本限定为 `0.2.4` 至 `0.2.9`。
- 本仓库按上游要求以 **CC BY-NC-SA 4.0** 分享；许可证全文见 [LICENSE](LICENSE)。复用时请保留原作者署名、来源、许可链接及改动说明，并遵守非商业和相同方式共享条款。各第三方依赖、地图与图片服务遵循各自条款。
