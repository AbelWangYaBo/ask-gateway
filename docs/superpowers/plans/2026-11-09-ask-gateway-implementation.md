# ask-gateway 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans。步骤用 `- [ ]` 跟踪。

**Goal:** 交付一个生产可用的 DSH 驱动问答网关：OpenAI 兼容 API + 有界 runtime 池 + 全量永久审计 + Vue3 控制台 + Docker 部署。

**Architecture:** FastAPI 网关经官方 `deepseek-harness-sdk`（JSON-RPC over stdio）驱动一组长驻 DSH runtime 子进程；自定义 profile 只保留对话与联网搜索工具；PostgreSQL 事件溯源存全部调用链与 usage；双事实源（PG + DSH JSONL）。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2.0 / Alembic / structlog / uv / pytest；Vue 3 + TS + Vite + Element Plus + ECharts 5 + Pinia；PostgreSQL 16；Docker Compose。

**关键约束（来自用户规范）**：三层分离（services 纯函数 / apis 薄路由 1–3 行 / schemas 独立）；SQLAlchemy+Alembic，迁移人工 one-shot；运行数据进 `data/`；uv 管理；无占位符、无半成品。

**离线测试策略**：定义 `Runner` Protocol，生产实现 `DshRunner`（包 SDK），测试实现 `FakeRunner`（脚本化 SessionEvent）。全部默认测试离线、不烧 token；真实 runtime smoke 标 `network` marker。

---

## Phase 0 — 项目骨架

- [ ] **Task 0.1 uv 工程**：`uv init --package`，pyproject 写依赖（fastapi、uvicorn[standard]、sqlalchemy、alembic、psycopg[binary]、pydantic-settings、structlog、httpx、deepseek-harness-sdk==0.2.1rc2、sse-starlette；dev：pytest、pytest-asyncio、ruff、mypy），`uv sync` 验证
- [ ] **Task 0.2 配置** `ask_gateway/config.py`：pydantic-settings 的 `Settings`，环境变量全量（DATABASE_URL、ADMIN_PASSWORD、上游 key 列表、池参数、配额默认值、ALLOWED_MODELS、ADMIN_SESSION_TTL_HOURS），`.env.example` 同清单
- [ ] **Task 0.3 日志** `ask_gateway/logging_conf.py`：structlog JSON/stdout 双 renderer，contextvar 绑定 request_id，密钥脱敏处理器（sk-ask-、Authorization、API_KEY）
- [ ] **Task 0.4 目录骨架**：db/models/schemas/services/apis/runtime_pool/billing/server/static；各包 `__init__.py`
- 验收：`uv run python -c "from ask_gateway.config import Settings; print(Settings())"` 成功；提交

## Phase 1 — 数据库与模型

- [ ] **Task 1.1** `db/base.py`（DeclarativeBase、统一 id/时间戳 mixin）、`db/session.py`（engine、sessionmaker、get_session 依赖）
- [ ] **Task 1.2** `models/` 八张表按 spec §3 全量实现：api_keys、requests、events、usage_records、pricing_rules、upstream_keys、admin_sessions、admin_audit_logs（JSONB 用 sqlalchemy 类型，INET 用通用类型兼容 sqlite 测试）
- [ ] **Task 1.3** Alembic 初始化 + 首个迁移（autogenerate 后人工核对），`alembic.ini` 与 `alembic/env.py` 读 Settings.DATABASE_URL
- [ ] **Task 1.4** 测试：sqlite 内存建表（conftest fixture），插入各表一行并回查；`pytest -k model` 绿；提交

## Phase 2 — 基础服务（services/，纯函数）

- [ ] **Task 2.1 安全** `services/security.py`：`hash_key`(sha256 hex)、`generate_api_key`（sk-ask- 前缀+随机）、`hash_token`、`verify_password`(pbkdf2)、常量时间比较
- [ ] **Task 2.2 Key 服务** `services/key_service.py`：创建（返回明文一次）、按 hash 查 active、吊销、配额更新、last_used_at 刷新
- [ ] **Task 2.3 配额** `services/quota_service.py`：RPM 滑动窗口（内存 deque + 时间戳）、并发槽（asyncio.Semaphore per key）、日预算（查当日 cost 汇总）；拒绝时抛 `QuotaError(code)`
- [ ] **Task 2.4 定价与计费** `billing/pricing.py`（峰谷规则匹配，北京时区）、`billing/cost.py`（usage+快照→cost，search 按次计）；测试覆盖高峰/非高峰/缓存/推理 token/不完整 usage 不计费
- [ ] **Task 2.5 管理员认证** `services/admin_auth.py`：登录签发 token、hash 存 admin_sessions、校验中间件依赖、过期清理
- [ ] **Task 2.6 审计写入** `services/audit_service.py`：管理动作写 admin_audit_logs；请求/事件/usage 的持久化函数（批量缓冲 flush）
- 验收：对应 `tests/test_security.py / test_quota.py / test_billing.py / test_admin_auth.py` 全绿；提交

## Phase 3 — DSH 适配与 runtime 池

- [ ] **Task 3.1 Runner 协议** `runtime_pool/runner.py`：`Runner` Protocol（`start()/run(messages, session_id, on_event) -> RunOutcome/close()`）、`RunOutcome` dataclass（final_response、finish_reason、events、aggregated_usage、search_count）
- [ ] **Task 3.2 DshRunner** `runtime_pool/dsh_runner.py`：包 `deepseek_harness.DeepSeekHarness`，长驻复用；env 注入上游 key 与独立 DSH_HOME；notification 回调转内部事件；usage 按 step 聚合（complete 口径）；搜索计数从 tool/call(web_search) 统计
- [ ] **Task 3.3 事件投影** `runtime_pool/events.py`：DSH SessionEvent → 内部 `StoredEvent(type, seq, payload)`，assistant/message 的 reasoning/text block 拆成 thinking/text 投影；统一 seq 编号
- [ ] **Task 3.4 池** `runtime_pool/pool.py`：acquire/release/懒扩容/reaper/上游 key 分组/熔断/优雅关停，参数走 Settings；自定义异常 `PoolTimeout/PoolBroken`
- [ ] **Task 3.5 FakeRunner** `tests/fakes.py`：脚本化事件序列，可模拟崩溃/超时/多 step/搜索/重试，供全部离线测试
- 验收：`tests/test_pool.py`（扩容、归还、队列超时 429、broken 回收、reaper 缩容、熔断）全绿；提交

## Phase 4 — 对外问答 API（apis/v1.py，薄路由）

- [ ] **Task 4.1 schemas** `schemas/chat.py`：OpenAI ChatCompletion 请求/响应/choice/usage 模型 + 扩展字段
- [ ] **Task 4.2 编排服务** `services/chat_service.py`：建请求行→配额→acquire→run→事件落库→usage 计费→汇总→响应；断连标记 client_disconnected；错误码映射（401/429/502/504）
- [ ] **Task 4.3 非流式路由** `POST /v1/chat/completions`、`GET /v1/models`、`GET /healthz`
- [ ] **Task 4.4 SSE**：stream=true 走 sse-starlette，thinking→reasoning_content、text→content、x-dsh-event 自定义事件、收尾 usage；首帧发 X-Request-Id
- 验收：`tests/test_chat_api.py` 用 FakeRunner 覆盖成功/流式/鉴权失败/限流/预算熔断/断连/runtime 崩溃；提交

## Phase 5 — 管理 REST（apis/admin.py）

- [ ] **Task 5.1 schemas** `schemas/admin.py`：Key、请求列表、详情、usage 汇总、定价、审计日志的请求/响应模型
- [ ] **Task 5.2 路由**：auth/login、keys CRUD、requests 列表（筛选+分页）、详情（含事件时间线）、usage/summary、pricing GET/PUT、audit-logs、dashboard、export；每个写操作记 admin_audit_logs
- 验收：`tests/test_admin_api.py` 全绿（含未授权 401、审计落库断言）；提交

## Phase 6 — 应用组装

- [ ] **Task 6.1** `main.py`：lifespan（池启动/reaper、优雅关停）、路由挂载、异常处理器统一错误体、SPA static 托管（API 路由不被吞）
- [ ] **Task 6.2** 种子脚本 `scripts/seed_pricing.py`：DeepSeek Flash/Pro 默认峰谷定价（幂等，人工执行，不进 lifespan）
- 验收：`uv run uvicorn ask_gateway.main:app` 起栈，healthz 200；提交

## Phase 7 — 前端控制台（frontend/）

- [ ] **Task 7.1** Vite+Vue3+TS 脚手架、Element Plus、ECharts、Pinia、Router、axios 封装（X-Admin-Token）
- [ ] **Task 7.2** 登录页 + auth store + 路由守卫
- [ ] **Task 7.3** Dashboard（统计卡+折线/饼/柱图）
- [ ] **Task 7.4** Key 管理页（创建一次性明文展示、吊销二次确认、配额编辑）
- [ ] **Task 7.5** 审计检索页（筛选、分页、详情抽屉：全文/事件时间线/thinking 折叠/搜索链路/usage）
- [ ] **Task 7.6** 费用看板（group_by 切换、图表、CSV 导出）
- [ ] **Task 7.7** 定价规则页、管理操作日志页
- [ ] **Task 7.8** Vitest（store/SSE 解析/格式化）+ `npm run build` 产物进 `server/static/dist/`
- 验收：Vitest 绿、build 成功、FastAPI 托管可访问；提交

## Phase 8 — DSH profile 与 Docker

- [ ] **Task 8.1** `dsh_profile/`：profile 构建脚本（pin 0.2.1-rc.2 白名单插件）、dump-config 夹具与工具面断言测试（不含 bash/pwsh/fs/subagent）
- [ ] **Task 8.2** Dockerfile 多阶段（node:22 构建前端→python:3.12-slim）、compose.yaml（app+postgres:16+三卷+healthcheck）、`.env.example`、entrypoint 不跑迁移
- [ ] **Task 8.3** `docs/acceptance-checklist.md` 手工验收清单；README 双语要点（启动、迁移、发 Key、问答、审计、备份）
- 验收：`docker compose config` 通过；离线全量 pytest 绿；提交

## 自检对齐

spec §1 决策 1–12 → Phase 0–8 全覆盖；八张表 → Task 1.2；两类 API → Phase 4/5；池机制六要点 → Task 3.4；双事实源 → Task 3.2（DSH JSONL 由 runtime 自身写挂载卷）+ Task 4.2（PG）；前端七页 → Task 7.2–7.7；风险对冲（版本锁定/工具面断言/密钥不入库）→ Task 0.1/8.1/2.1。
