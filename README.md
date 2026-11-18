# ask-gateway

基于官方 DeepSeek Harness（DSH）SDK 的多租户问答网关：对外提供 OpenAI 兼容接口，
对内把每一次问答的**完整调用链路、思考过程、联网搜索、usage 与费用**落库审计，
凭平台签发的 API Key 定位到具体持有人。纯问答 + 联网搜索，无 shell / 文件系统 /
子代理 / 工作流工具面。

## 功能总览

**API（OpenAI 兼容）**
- `POST /v1/chat/completions`：非流式 + SSE 流式，全量 messages 无状态多轮
- SSE 过程反馈：`x-dsh-event` 自定义事件实时透出搜索/工具调用进度
- `GET /v1/models` 模型白名单

**管理控制台**（`/admin`，`/api/admin/*`，单管理员口令）
- 看板：请求量、成功率、状态分布、P50/P95 延迟、当日费用
- API Key 管理：创建（明文仅展示一次，库存 SHA-256）、启停、到期时间、
  RPM / 日预算 / 并发配额、Key 验证（平台 Key 与上游 Key 均可比对是哪一个）
- 上游 Key 管理：AES-GCM 加密入库，管理端增删改**即时生效不重启**，
  池按 Key 分组轮换、熔断隔离
- 请求审计：请求/回复全文、思考过程、联网搜索链路、完整事件时间线、usage
- 费用报表：按天/Key/模型/上游 Key 分组，时间范围筛选，CSV 导出
- 定价规则：编辑、启停、生效周期与权重（见下）

**用户门户**（`/portal`，`/api/portal/*`，用户持 API Key 登录）
- 我的消耗：请求记录、用量与费用
- 内置 chatbot：流式对话、多轮上下文、联网搜索开关
- 对话体系：API 与 chatbot 的请求统一归入对话，持久化保存，
  支持从任意消息点反复分叉（a-b-c → c-d / c-e / c-f 平行分支）

## 架构要点

- **执行面**：有界弹性 DSH runtime 进程池（min/max 可配、空闲回收、熔断、
  池运行中重配置）；上游 Key 经 AES-GCM 加密存库，运行时注入子进程环境
- **审计面**：请求/回复全文、事件时间线、usage、账单快照、管理员操作日志
  永久留存；数据库默认 PostgreSQL（本地开发 sqlite 即可）
- **工具面裁剪**：`dsh_profile/` 在 profile 层禁用 12 个越权工具 id，
  契约测试 + 钉版本二进制双重校验
- **密钥安全**：平台 Key 只存哈希、上游 Key 密文存储 + 指纹/前缀比对；
  日志统一脱敏 Authorization / X-Admin-Token / `sk-` 串

## 定价与计费

- 单价口径：人民币分 / 百万 token（搜索为分 / 千次），内置 DeepSeek 官方
  2026 峰谷报价种子（空闲/高峰两档，高峰 = 工作日 9–12、14–18 两段）
- **生效周期**：`effective_from` / `effective_to` 构成半开区间，支持提前录入
  未来调价、临时优惠到期自动回落，无需人工守点改价
- **权重**：同时刻多条规则匹配时大者胜——常规价 0、临时优惠 1，优惠期
  整体覆盖常规；分档优惠则各档分别建高权重规则
- **账单冻结**：每次计费把当时规则完整快照进 usage 记录，此后改规则 /
  停用 / 加优惠均不影响历史账单（历史费用只读，修正需走专门脚本）

## 本地开发（macOS / Linux）

```bash
uv sync                                              # 安装依赖（含钉版本 DSH runtime）
uv run alembic upgrade head                          # 1. 建表（一次性，人工执行）
uv run python scripts/seed_pricing.py                # 2. 定价种子（幂等）
uv run uvicorn ask_gateway.main:app                  # 3. 启动后端，默认 127.0.0.1:8000

cd frontend && npm ci && npm run dev                 # 前端开发服务器（:5173，代理到 :8000）
```

上游 Key 在管理台「上游 Key 管理」页维护；`UPSTREAM_KEYS` 仅作首次启动的
一次性引导（DB 为空时导入，此后以数据库为准）。无上游 Key 时服务照常启动
（管理面可用、池不预热）。配置项全量见 `.env.example`。

## 发 Key 与问答

1. 控制台登录（`ADMIN_PASSWORD`）→ Key 管理 → 创建 Key，**明文仅展示一次**
2. 调用问答：

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-ask-..." \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-flash","messages":[{"role":"user","content":"今天有什么科技新闻？"}]}'
```

加 `"stream": true` 即 SSE。控制台「请求审计」可查看该请求的全文、思考过程、
联网搜索链路、完整事件时间线与 usage/费用；用户自己则用同一个 Key 登录
`/portal` 查看消耗与对话。

## Docker 部署（Linux 服务器）

```bash
cp .env.example .env            # 填 ADMIN_PASSWORD / POSTGRES_PASSWORD（上游 Key 可启动后在管理台录入）
docker compose run --rm app uv run alembic upgrade head        # 迁移（人工，绝不进 entrypoint）
docker compose up -d
```

端口默认只映射到宿主机回环；对外暴露改 `compose.yaml` 的 ports 并自行加网关与令牌管控。

## 备份

- PostgreSQL：`pg_dump` 定期备份（compose 卷 `pgdata`）
- DSH 运行时事实：卷 `dsh_homes` / `dsh_workspaces` / `dsh_sessions`（审计事实库以 PG 为准，卷为双事实源之一）
- 加密主密钥：`SECRETS_MASTER_KEY` 或 `data/secrets/master_key`——**与库同级别重要**，丢失则上游密文不可恢复

## 测试

```bash
uv run pytest                     # 离线全量（真实 DSH/网络用例标 network，默认不选）
uv run ruff check .               # E/F/I 规则集
cd frontend && npm run build      # 类型检查 + 构建，产物提交到 src/ask_gateway/server/static/dist
```

---

## English

**ask-gateway** is a multi-tenant Q&A gateway built on the official DeepSeek
Harness (DSH) SDK. It exposes an OpenAI-compatible API while permanently
auditing every call — full event chain, reasoning, web searches, token usage
and cost — keyed by the platform API key held by each user. The tool surface
is restricted to Q&A + web search: shell, filesystem, subagent, skill and
workflow tools are disabled at the DSH profile layer.

- `POST /v1/chat/completions` (JSON + SSE with live `x-dsh-event` progress),
  stateless multi-turn via full message history
- Admin console (`/admin`, backed by `/api/admin/*`): dashboard, API key
  lifecycle with quotas, encrypted upstream-key management (hot reload,
  pool reconfiguration without restart), full request audit, cost reports
  with CSV export, pricing rules with effective periods & priority weights
- User portal (`/portal`, backed by `/api/portal/*`): self-service usage &
  spend, built-in streaming chatbot, persistent conversations with repeated
  forking from any message
- Billing: peak/off-peak hourly rules (Beijing time), priority weights for
  promos, future-dated price switches; every usage row freezes the pricing
  snapshot so history never changes
- See `.env.example` for all settings; Docker deployment above is the
  production path
