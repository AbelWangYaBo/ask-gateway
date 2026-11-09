# ask-gateway 设计文档

- 日期：2026-11-09
- 位置：`/Volumes/APFS_2/abel/ask-gateway`
- 状态：已评审（关键决策经逐项确认）

## 1. 目标与范围

在自有系统与 DeepSeek 之间建设一个生产级问答网关：自有系统持本平台签发的 API Key 调用，网关通过 DeepSeek Harness（DSH）驱动 DeepSeek 模型，返回与 chat.deepseek.com 同源的「联网搜索 + 深度思考」答案，并对全部调用做永久、完整、可按人追溯的合规审计。

### 已确认的决策

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 能力边界 | 纯问答 + 联网搜索；禁用 bash/文件/子代理等一切 coding agent 工具 |
| 2 | 网关技术栈 | Python 3.12 + FastAPI + 官方 `deepseek-harness-sdk`（进程外 JSON-RPC 驱动捆绑 runtime） |
| 3 | 上游 Key 模型 | 平台统一 DeepSeek Key 池，经环境变量注入 runtime，调用方不接触上游凭据 |
| 4 | 本平台 Key 模型 | 一人多 Key（个人/各系统各一把），Key 是唯一审计抓手；不追踪调用来源系统 |
| 5 | 审计内容 | 请求与回复全文、完整事件链路、thinking 思考链、usage 与成本，界面不可见的后台链路同样留存 |
| 6 | 留存周期 | 永久保留，不自动删除 |
| 7 | 部署 | macOS 本地开发；Linux 服务器 + Docker 部署 |
| 8 | API 形态 | 对外 OpenAI 兼容（含 SSE）；管理面独立 `/admin` REST |
| 9 | 管理界面 | 完整多页 Web 控制台（Vue 3 + Element Plus + ECharts 5），单人管理员 |
| 10 | 并发规模 | 高（峰值 10+ 并发），需有界弹性 runtime 池 + 队列 + 配额 |
| 11 | 数据库 | PostgreSQL 16，SQLAlchemy 2.0 + Alembic |
| 12 | 命名 | 目录 `ask-gateway`，Python 包 `ask_gateway` |

### 非目标（YAGNI）

- 不做多角色/多租户权限体系（只有一个管理员）
- 不做调用来源系统识别、不做 BYOK
- 不做自动数据清理/TTL
- 不做 DSH Web UI 托管、不做桌面端
- 不做计费收款，只做成本计量与预算熔断

## 2. 总体架构

```
自有系统 --Bearer sk-ask-...--> [FastAPI 网关]
                                 │
  鉴权中间件(Key hash/持有人/配额)
  问答路由(OpenAI 兼容, SSE 桥接)
  管理 REST(/admin)
        │
        ▼
  有界弹性 runtime 池 + 中央有界队列
        │  JSON-RPC over stdio (deepseek-harness-sdk)
        ▼
  DSH runtime 子进程 ×N（自定义 ask-gateway profile，仅对话+web 工具）
        │  env: DEEPSEEK_API_KEY（按上游 Key 池分组注入）
        ▼
  DeepSeek 官方 API（含服务端 web_search）

事实源：
  PostgreSQL   —— 审计主库（请求/事件/usage/Key/定价/管理审计），永久
  DSH JSONL    —— 每会话 durable Session Log，挂载卷永久归档，重放兜底
  stdout JSON  —— 结构化应用日志（request_id 贯穿）
```

### 核心机制

1. **无状态问答**：每次请求生成新的 DSH `session_id`，OpenAI `messages` 全量喂入，任意 runtime 可处理，池无需会话亲和。
2. **有界弹性池**：预热 `min_size` 个长驻 runtime，忙时扩到 `max_size`，空闲缩容；借用语义为「借出—归还」，崩溃实例销毁重建。
3. **三层背压**：每 Key 并发槽 → 全局有界队列（排队超时返回 429）→ 池硬上限；每 Key 另有 RPM 限流与日费用预算熔断。
4. **失败语义**：runtime 崩溃只影响该实例；prompt 提交前失败可安全重试一次，提交后失败如实报错不重放（避免重复计费/重复回答）。
5. **双事实源一致**：PG 事件行与 JSONL Session Log 来自同一条 SDK 通知流，`dsh_session_id` 可交叉核验。
6. **定价快照**：峰谷定价规则可配置，计费时把生效规则快照进 usage 记录，规则后续变更不影响历史账单。

## 3. 数据模型

PostgreSQL，SQLAlchemy 2.0 声明式模型位于 `ask_gateway/models/`，Alembic 迁移位于 `alembic/versions/`。迁移由人工 one-shot 执行，不进入应用启动生命周期。

### 3.1 `api_keys` — 本平台签发的 Key

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID PK | |
| key_prefix | VARCHAR(16) | 展示前缀，如 `sk-ask-a1b2c3d4` |
| key_hash | CHAR(64) UNIQUE NOT NULL | SHA-256  hex；明文仅创建响应出现一次 |
| holder_name | VARCHAR(128) NOT NULL | 持有人，如「用户A」 |
| label | VARCHAR(128) NOT NULL | 用途标签：个人/系统1/系统2 |
| status | VARCHAR(16) NOT NULL | `active` / `revoked` |
| rpm_limit | INT NULL | NULL 用全局默认 |
| daily_budget_cents | INT NULL | 日预算（分），NULL 用全局默认 |
| max_concurrency | INT NULL | 并发槽，NULL 用全局默认 |
| created_at / last_used_at / revoked_at | TIMESTAMPTZ | |

Key 只吊销不删除：`status=revoked` + `revoked_at`，保留行以维持历史 FK 与归因。

### 3.2 `requests` — 每次问答一行（审计主表）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID PK | |
| request_id | UUID UNIQUE NOT NULL | 对外 ID，写入响应头 `X-Request-Id` |
| api_key_id | UUID FK → api_keys | |
| holder_name | VARCHAR(128) NOT NULL | 冗余快照 |
| key_label | VARCHAR(128) NOT NULL | 冗余快照 |
| model | VARCHAR(64) NOT NULL | |
| stream | BOOLEAN NOT NULL | |
| enable_web_search | BOOLEAN NOT NULL | |
| thinking_effort | VARCHAR(16) NULL | |
| client_ip | INET | |
| user_agent | TEXT | |
| status | VARCHAR(32) NOT NULL | `queued/running/succeeded/failed/client_disconnected/rate_limited` |
| prompt_messages | JSONB NOT NULL | OpenAI messages 原样保真 |
| final_answer | TEXT NULL | 失败为空 |
| finish_reason | VARCHAR(32) NULL | |
| error_code / error_message | TEXT NULL | |
| dsh_session_id | VARCHAR(128) NULL | DSH 侧会话 ID |
| upstream_key_label | VARCHAR(64) NULL | 实际使用的上游 Key 标签（非明文） |
| queued_at/started_at/finished_at | TIMESTAMPTZ | 派生 queue_wait_ms、duration_ms |
| total_input_tokens / total_output_tokens | INT NULL | |
| cache_read_tokens / cache_write_tokens / reasoning_tokens | INT NULL | |
| search_count | INT NOT NULL DEFAULT 0 | |
| total_cost_cents | NUMERIC(12,3) NOT NULL DEFAULT 0 | |

索引：`(holder_name, created_at)`、`(api_key_id, created_at)`、`(status, created_at)`；`prompt_messages` 不加索引，关键词检索走 `final_answer` 的 pg_trgm 或独立词向量留给后续版本（v1 用 ILIKE 模糊匹配）。

### 3.3 `events` — 事件溯源（一次问答 N 行）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGSERIAL PK | |
| request_id | UUID FK → requests NOT NULL | |
| seq | INT NOT NULL | 同请求内有序 |
| event_type | VARCHAR(64) NOT NULL | 两类：① DSH 原始 SessionEvent 类型（turn/start、step/start、assistant/attempt、assistant/message、tool/call、tool/result、step/end、turn/end 等）；② 适配层投影类型（`thinking`、`text`，从 assistant/message content block 拆出，便于控制台直接渲染） |
| payload | JSONB NOT NULL | 事件完整原始内容（thinking 文本、web_search query 与结果摘要均在内） |
| created_at | TIMESTAMPTZ NOT NULL | |

约束：`UNIQUE(request_id, seq)`。索引：`(request_id, seq)`、`(event_type, created_at)`。

### 3.4 `usage_records` — step 粒度计量

每 `step/end` 一行：`request_id FK`、`step INT`、`input_tokens/output_tokens/cache_read_tokens/cache_write_tokens/reasoning_tokens`、`complete BOOLEAN`（沿用 DSH 口径：任一 attempt 未报 usage 则 FALSE，省略伪造合计）、`pricing_snapshot JSONB`、`cost_cents NUMERIC(12,3)`、`search_count INT`、`created_at`。

### 3.5 `pricing_rules` — 峰谷定价

`id`、`model VARCHAR`、`rule_name`、`peak_start_hour/peak_end_hour INT NULL`（NULL=全时段）、`weekdays_only BOOLEAN`、`input_price_per_mtok/output_price_per_mtok/reasoning_price_per_mtok NUMERIC`、`cache_read_price_per_mtok/cache_write_price_per_mtok NUMERIC`、`search_price_per_k NUMERIC`、`effective_from TIMESTAMPTZ`、`is_active BOOLEAN`、`created_at`。同模型同时段只允许一条 active（应用层校验）。

### 3.6 `upstream_keys` — 上游 Key 池元数据

`id`、`env_var_name VARCHAR`（如 `DSH_UPSTREAM_KEY_1`，**只存变量名**）、`label`、`is_active`、`notes`、`created_at`。明文密钥只存在于环境变量，不进库、不进日志。

### 3.7 `admin_sessions` — 管理员登录态

`id PK`、`token_hash CHAR(64) UNIQUE`、`expires_at`、`created_at`、`client_ip`、`user_agent`。

### 3.8 `admin_audit_logs` — 管理动作自审计

`id BIGSERIAL`、`action VARCHAR`（login/key_create/key_revoke/key_update/pricing_update/audit_view/audit_export）、`target_type/target_id`、`detail JSONB`、`client_ip`、`created_at`。

## 4. 对外 API 契约

### 4.1 `POST /v1/chat/completions`

请求（OpenAI 兼容 + 两个扩展字段）：

```json
{
  "model": "deepseek-flash",
  "messages": [{"role": "user", "content": "..."}],
  "stream": false,
  "enable_web_search": true,
  "thinking_effort": "high"
}
```

- 鉴权：`Authorization: Bearer sk-ask-...`，SHA-256 后查 `api_keys.key_hash`，无效/吊销 → 401
- 模型白名单校验，不在配置内 → 400
- 配额：RPM 滑动窗口、并发槽、日预算（当日累计 `total_cost_cents`），超限 → 429 并落 `rate_limited` 审计行
- 非流式响应：标准 OpenAI `{id, object, choices:[{message:{role,content,reasoning_content?}, finish_reason}], usage:{prompt_tokens,completion_tokens,total_tokens,reasoning_tokens,cache_read_tokens,cache_write_tokens,search_count}}`
- 流式响应（SSE）：`chat.completion.chunk` 序列；thinking 走 `delta.reasoning_content`，正文走 `delta.content`；DSH 工具/搜索过程以 `event: x-dsh-event` 发送（不破坏 OpenAI 客户端兼容，忽略自定义事件的客户端不受影响）；收尾 chunk 带 usage 与 `X-Request-Id`（首个事件即下发）
- 客户端断连：状态置 `client_disconnected`，已产生的事件/usage 照常落库
- 错误体：`{error:{code,message,request_id}}`

### 4.2 `GET /v1/models`

返回配置白名单模型 `{object:"list", data:[{id, object:"model", owned_by:"ask-gateway"}]}`。

### 4.3 管理 REST（`/admin`）

- `POST /admin/auth/login`：body `{password}`，校验环境变量 `ADMIN_PASSWORD` 的哈希；成功发随机 token（存 hash，有效期可配置，默认 12h）
- 后续请求头 `X-Admin-Token`；中间件统一校验并写管理审计
- `GET/POST /admin/keys`、`POST /admin/keys/{id}/revoke`、`PATCH /admin/keys/{id}`（配额字段）
- `GET /admin/requests`（query：holder_name、key_id、status、date_from、date_to、keyword、page、page_size）
- `GET /admin/requests/{request_id}`（含 events 时间线、usage_records、thinking）
- `GET /admin/usage/summary`（group_by=holder|key|model|day，时间范围，ECharts 数据结构）
- `GET/PUT /admin/pricing`
- `GET /admin/audit-logs`
- `GET /admin/dashboard`（总请求数、成功率、P50/P95 延迟、今日费用、活跃 Key 数、按状态分布）
- `GET /admin/requests/{request_id}/export`（单请求完整 JSON 导出，记 audit_view/export）

## 5. DSH profile 与 runtime 池

### 5.1 自定义 profile `ask-gateway`

- 通过 `dsh plugin --profile ask-gateway add ...` 初始化（开发期一次），产物固化进仓库 `dsh_profile/` 并在 Docker 构建期复制到 `DSH_HOME/profiles/ask-gateway`
- 挂载插件（白名单）：`dsh-sdk-app`（提供 jsonrpc-server）、`dsh-agent`、`dsh-agent-loop`、`dsh-llm`、`dsh-llm-deepseek-api-key`、`dsh-llm-retry`、`dsh-session` + `dsh-session-persistence-jsonl` + `dsh-session-log-deepseek`、`dsh-system-prompt`、`dsh-web`、`dsh-tool-web`、`dsh-web-search-deepseek`、`dsh-tools`、`dsh-invariants`
- 禁用方式：不挂载即不存在；启动后用 `dsh --profile ask-gateway --dump-config` 生成实际插件树快照入测试夹具，断言不含 bash/pwsh/fs/subagent/skill/workflow 工具
- 联网搜索：`dsh-web-search-deepseek` 经 Anthropic 兼容端点做服务端搜索，凭据取注入的 `DEEPSEEK_API_KEY`；请求级开关通过 SDK `patches`/初始化参数与 system 提示共同控制（开发期实测确认最终机制并固化）
- 每槽位独立目录：`data/dsh/homes/slot-N/`（DSH_HOME）、`data/dsh/workspaces/slot-N/`（cwd），JSONL 统一根 `data/dsh/sessions/`（配置 persistence root 指向它，便于归档）

### 5.2 池设计（`ask_gateway/runtime_pool/`）

- `RuntimeHandle`：封装一个 `DeepSeekHarness` 实例、所属上游 Key label、当前状态（idle/running/broken）、借出计数、最后使用时间、启动时间
- `RuntimePool`：
  - 配置 `min_size`（默认 2）、`max_size`（默认 12）、`idle_ttl_seconds`（默认 600）、`queue_maxsize`（默认 32）、`acquire_timeout_seconds`（默认 60）
  - `acquire()`：优先取 idle；无 idle 且未达上限则同步启动新 runtime（懒扩容）；达上限进队列等待；超时抛 `QueueTimeout` → 429
  - `release(handle, broken)`：broken 则关闭并从池移除（后台补到 min_size），否则归还 idle
  - 后台 reaper 定时回收空闲超 TTL 的实例到 min_size
  - 上游 Key 分组：每组维护独立子池，轮询选取；某组连续失败自动熔断冷却（默认 5 分钟）
- 健康检查：借出声望检查（SDK 轻量 initialize 握手状态）；后台每 60s 对 idle 实例做 ping（一次最小 prompt 的代价不可接受，因此用进程存活 + JSON-RPC `initialize` 状态位判断，不发模型请求）
- 优雅关停：SIGTERM → 池关闭入口、队列拒绝新请求、等待在跑请求（上限 `shutdown_grace_seconds` 默认 45）、逐个 `harness.close()` 收割子进程

### 5.3 请求处理管线

1. 鉴权与配额，创建 `requests` 行（status=queued）
2. `acquire()` 池句柄，status=running，记 started_at、upstream_key_label
3. 生成 `dsh_session_id`，`Session.run(messages)`，`on_notification` 回调内：
   - 事件按 seq 落 PG（批量缓冲，每 20 条或 200ms flush）
   - SSE 模式同时投影成 OpenAI chunk 推送
   - 累计 step usage（`step/end`）写 usage_records 并按 pricing 快照算费
4. 收尾：final_answer、finish_reason、汇总 token/费用，status=succeeded
5. 异常分类：SDK 协议错误/进程崩溃 → 502（broken 句柄回收）；超时 → 504；客户端断连 → client_disconnected；全部落库

## 6. 日志

- structlog，JSON renderer 输出 stdout；本地开发可用 console renderer（环境切换）
-  contextvar 绑定 `request_id/api_key_id/holder_name/dsh_session_id`，每条日志自动携带
- 日志事件：请求生命周期（queued/running/succeeded/failed）、池伸缩与借用等待、配额拒绝、上游 Key 熔断、DSH 协议异常、管理操作
- 脱敏器：`Authorization`、`X-Admin-Token`、`DEEPSEEK_API_KEY`、任何匹配 `sk-ask-` 的字符串
- 不记录消息正文到应用日志（正文只进 PG 审计库），避免日志与审计两套留存口径

## 7. 前端控制台

Vite + Vue 3 + TypeScript + Element Plus + ECharts 5 + Pinia + Vue Router，源码 `frontend/`，构建到 `ask_gateway/server/static/dist/` 由 FastAPI 托管（`/` 走 SPA fallback，`/api`、`/v1`、`/admin` 不被静态路由吞掉）。

页面：
1. **登录**：口令登录，token 存 localStorage，路由守卫
2. **Dashboard**：四个统计卡 + 费用趋势折线 + 状态分布饼图 + 持有人 Top 柱状图
3. **API Key 管理**：表格（前缀/持有人/标签/状态/配额/最后使用）、创建对话框（成功后一次性明文展示+复制按钮+明确警告仅显示一次）、吊销（二次确认）、配额编辑
4. **审计检索**：筛选栏 + 分页表格 + 详情抽屉（基本信息、请求/回复全文、事件时间线、thinking 折叠块、搜索链路卡片、usage 明细表）
5. **费用看板**：group_by 切换、ECharts 图表、CSV 导出按钮
6. **定价规则**：列表 + 新建/编辑/停用对话框
7. **管理操作日志**：只读表格

前端 Vitest 单测覆盖 store 与适配函数；不引入浏览器 E2E（单人内部工具，验收走手工清单）。

## 8. 部署与配置

### 8.1 目录结构

```
ask-gateway/
├── pyproject.toml uv.lock
├── alembic.ini  alembic/
├── dsh_profile/                # 固化的 ask-gateway profile（构建期复制）
├── docker/  Dockerfile  compose.yaml  .env.example
├── ask_gateway/
│   ├── main.py  config.py  logging_conf.py
│   ├── db/        # engine/session/base
│   ├── models/    # SQLAlchemy 模型
│   ├── schemas/   # Pydantic
│   ├── services/  # 纯业务函数：keys/audit/usage/pricing/quota/auth
│   ├── apis/      # 薄路由：v1(OpenAI 兼容)、admin
│   ├── runtime_pool/  # DSH 池与事件适配
│   ├── billing/   # 定价折算
│   └── server/static/dist/  # 前端构建产物
├── frontend/
├── tests/
└── data/          # 运行数据（gitignore）
```

### 8.2 配置（环境变量，`.env.example` 全量列出）

`DATABASE_URL`、`ADMIN_PASSWORD`、`DSH_UPSTREAM_KEY_1..N`、`POOL_MIN/MAX/QUEUE_MAXSIZE/ACQUIRE_TIMEOUT/IDLE_TTL`、`DEFAULT_RPM/DEFAULT_DAILY_BUDGET_CENTS/DEFAULT_CONCURRENCY`、`ADMIN_SESSION_TTL_HOURS`、`ALLOWED_MODELS`、`DSH_BIN`（可选，默认用 SDK 捆绑 runtime）、`LOG_LEVEL`。

### 8.3 Docker

多阶段：`node:22` 构建前端 → `python:3.12-slim` 装依赖（runtime-bin wheel 自带 Node 运行时，不装系统 Node）；compose 服务 `app` + `postgres:16`；卷 `pgdata`、`dsh_sessions`、`dsh_homes`；healthcheck 打 `/healthz`；迁移命令 `docker compose run --rm app uv run alembic upgrade head` 人工执行，entrypoint 不跑迁移。

## 9. 测试策略

- 单测：billing 峰谷折算、配额判定、事件→OpenAI 格式投影、usage 聚合（含 complete=FALSE 口径）、Key hash 鉴权、定价快照
- 集成：用 SDK 自带 fake runtime 测试夹具（`test-support`/mock LLM 机制）驱动完整请求管线，覆盖成功/流式/超时/断连/池满 429/预算熔断/runtime 崩溃恢复
- API 测试：httpx + FastAPI TestClient，鉴权与错误码契约
- 前端：Vitest 覆盖 Pinia store、SSE 解析、格式工具
- marker：真实 DSH/真实网络的 smoke 标 `network`，默认套件全离线
- 验收：交付文档 `docs/acceptance-checklist.md`，含 compose 起栈、迁移、发 Key、问答（流式/非流式/联网/思考）、审计检索、费用看板、断连与熔断的手工步骤

## 10. 已知风险与对冲

| 风险 | 对冲 |
|---|---|
| DSH 处于 rc 期，线协议可能变 | 锁 `deepseek-harness-sdk==0.2.1rc2`；升级先在分支跑全量离线测试 + network smoke |
| 高并发下 runtime 内存压力 | max_size 可配置；Docker 内存限额；reaper 缩容；监控池占用指标 |
| DSH 工具面挂载出错导致 shell 暴露 | 启动 dump-config 断言入测试夹具；profile 白名单固化进仓库 |
| 上游 Key 泄漏 | 只走环境变量、不进库不进日志；容器 secret 由 compose env 注入 |
| 永久留存导致磁盘增长 | JSONL 挂载独立卷；文档给出容量规划与手动归档流程（不做自动删除） |
