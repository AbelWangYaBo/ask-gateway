# ask-gateway

基于官方 DeepSeek Harness（DSH）SDK 的多租户问答网关：对外提供 OpenAI 兼容接口，
对内把每一次问答的**完整调用链路、思考过程、联网搜索、usage 与费用**落库审计，
凭平台签发的 API Key 定位到具体持有人。纯问答 + 联网搜索，无 shell/文件系统/
子代理/工作流工具面。

## 架构要点

- **OpenAI 兼容面**：`POST /v1/chat/completions`（非流式 + SSE），调用方持平台 Key（`sk-ask-...`）
- **管理面**：`/admin/*` REST + Vue3 控制台（7 个页面），单管理员口令登录
- **执行面**：有界弹性 DSH runtime 进程池，上游 DeepSeek Key 只经环境变量注入子进程，不入库不入日志
- **审计面**：PostgreSQL 永久留存请求/回复全文、事件时间线、usage、账单快照、管理员操作日志
- **工具面裁剪**：`dsh_profile/` 在 profile 层禁用 10 个越权工具 id，契约测试 + 钉版本二进制双重校验

## 本地开发（macOS / Linux）

```bash
uv sync                                              # 安装依赖（含钉版本 DSH runtime）
uv run alembic upgrade head                          # 1. 建表（一次性，人工执行）
uv run python scripts/seed_pricing.py                # 2. 定价种子（幂等）
uv run python dsh_profile/build_profile.py           # 3. 构建 DSH profile（按 UPSTREAM_KEYS 派生 home）
uv run uvicorn ask_gateway.main:app --reload         # 4. 启动后端，默认 127.0.0.1:8000

cd frontend && npm ci && npm run dev                 # 前端开发服务器（:5173，代理到 :8000）
```

无 `UPSTREAM_KEYS` 时服务照常启动（管理面可用、池不预热），问答请求会在 runtime
启动时给出明确配置错误。配置项全量见 `.env.example`。

## 发 Key 与问答

1. 控制台登录（`ADMIN_PASSWORD`）→ Key 管理 → 创建 Key，**明文仅展示一次**，数据库只存 SHA-256
2. 调用问答：

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-ask-..." \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-flash","messages":[{"role":"user","content":"今天有什么科技新闻？"}]}'
```

加 `"stream": true` 即 SSE。控制台「请求审计」可查看该请求的全文、思考过程、
联网搜索链路、完整事件时间线与 usage/费用。

## Docker 部署（Linux 服务器）

```bash
cp .env.example .env            # 填 ADMIN_PASSWORD / UPSTREAM_KEYS / POSTGRES_PASSWORD
docker compose run --rm app uv run alembic upgrade head        # 迁移（人工，绝不进 entrypoint）
docker compose run --rm app uv run python dsh_profile/build_profile.py
docker compose up -d
```

端口默认只映射到宿主机回环；对外暴露改 `compose.yaml` 的 ports 并自行加网关与令牌管控。

## 备份

- PostgreSQL：`pg_dump` 定期备份（compose 卷 `pgdata`）
- DSH 运行时事实：卷 `dsh_homes` / `dsh_workspaces` / `dsh_sessions`（审计事实库以 PG 为准，卷为双事实源之一）

## 测试

```bash
uv run pytest                     # 离线全量（真实 DSH/网络用例标 network，默认不选）
uv run ruff check .               # E/F/I 规则集
cd frontend && npm test           # Vitest 22 例
npm run build                     # 类型检查 + 构建，产物提交到 src/ask_gateway/server/static/dist
```

---

## English

**ask-gateway** is a multi-tenant Q&A gateway built on the official DeepSeek
Harness (DSH) SDK. It exposes an OpenAI-compatible API while permanently
auditing every call — full event chain, reasoning, web searches, token usage
and cost — in PostgreSQL, keyed by the platform API key held by each user.
The tool surface is restricted to Q&A + web search: shell, filesystem,
subagent, skill and workflow tools are disabled at the DSH profile layer.

- `POST /v1/chat/completions` (plain JSON + SSE), authenticated with a platform key
- Admin console (`/admin/*` + Vue 3 SPA), single admin password
- Bounded elastic pool of persistent DSH runtimes; upstream keys are injected
  via environment variables only, never stored or logged
- See `.env.example` for all settings; Docker deployment above is the production path
