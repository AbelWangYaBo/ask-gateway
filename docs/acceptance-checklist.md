# 验收清单（手工）

单人内部工具，无浏览器 E2E；按本清单逐项手工验收。除标注「需真实 DSH」外，
其余项均可离线完成。命令默认在项目根目录执行。

## 0. 环境准备

- [ ] `uv sync` 成功，`uv run pytest` 全绿（当前基线 116 例）
- [ ] `cd frontend && npm ci && npm test`：22 例通过；`npm run build` 成功
- [ ] `cp .env.example .env`，填好 `ADMIN_PASSWORD`、`UPSTREAM_KEYS`（真实 Key）
- [ ] 日志目录与 DB：默认全部落在项目内 `data/`，不产生 `~/` 下的运行数据

## 1. 启动与迁移（一次性命令不进 lifespan）

- [ ] `uv run alembic upgrade head` 建表 9 张；重复执行无副作用
- [ ] `uv run python scripts/seed_pricing.py` 写入 6 条峰谷定价（空闲全时段 + 工作日
      9–12 / 14–18 两段高峰 × 2 模型）；重复执行跳过不重复
- [ ] `uv run python dsh_profile/build_profile.py` 输出 `profile built`，
      `data/dsh/homes/upstream-1/profiles/ask-gateway/cordis.patch.yml` 存在
- [ ] 启动 `uv run uvicorn ask_gateway.main:app`，日志出现池预热，无异常堆栈
- [ ] 删掉 `UPSTREAM_KEYS` 再启动：服务正常、日志明确告警「管理面可用，问答请求将失败」
- [ ] `curl http://127.0.0.1:8000/healthz` 返回 `{"status":"ok"}`
- [ ] 浏览器打开 `http://127.0.0.1:8000/`：控制台登录页；刷新 `/requests` 深链不 404

## 2. 管理面与 Key 生命周期

- [ ] 错误口令登录 401；正确口令登录拿到 token，`X-Admin-Token` 访问 `/admin/keys` 200
- [ ] 创建 Key（holder_name + label）：响应里**明文只出现这一次**；列表接口不再返回明文
- [ ] 数据库直查：`api_keys` 表只存 SHA-256 哈希，无明文列
- [ ] 编辑配额（rpm / 日预算 / 并发）；传 `null` 回落全局默认
- [ ] 吊销 Key：再用它调 `/v1` 返回 401
- [ ] 以上每个操作在「管理操作日志」页都有一条记录（动作、IP、时间）

## 3. 问答链路（需真实 DSH）

- [ ] 非流式：`/v1/chat/completions` 返回 OpenAI 形状（choices/usage/model）
- [ ] 流式：`"stream": true` 返回 `text/event-stream`，chunk 序列以 `data: [DONE]` 结束
- [ ] 问一个必须联网的问题，回答中体现实时信息
- [ ] `/v1/models` 只返回白名单模型；白名单外模型名请求被拒
- [ ] 无 `UPSTREAM_KEYS` 时发起问答：返回明确错误信封，服务不崩、runtime 不残留

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-ask-..." -H "Content-Type: application/json" \
  -d '{"model":"deepseek-flash","messages":[{"role":"user","content":"今天有什么科技新闻？"}]}'
```

## 4. 审计完整性（核心需求）

- [ ] 控制台「请求审计」出现上一步请求，详情抽屉包含：
  请求/回复全文、思考过程、联网搜索链路（调用与结果按 callId 配对）、
  完整事件时间线、usage（input/output/cache token）、费用快照
- [ ] 界面看不到的背后事件也在库里：`events` 表行数 ≥ 界面时间线条数
- [ ] 按持有人/Key/模型/状态/时间筛选生效；CSV 导出可用 Excel 打开（BOM + 转义正确）
- [ ] 用量汇总按持有人/Key/模型/天四个维度可切，费用合计与请求明细一致
- [ ] 同一持有人多 Key：分别用两个 Key 各发一次，审计能按 Key 区分到人
- [ ] 重启服务后历史请求与事件全部仍在（永久保留，无清理任务）

## 5. 配额与计费

- [ ] 把某 Key 的 `rpm_limit` 调成 1，连发请求：超限返回 429 风格错误信封
- [ ] `daily_budget_cents` 调到极小值：预算耗尽后新请求被拒，已有记录不受影响
- [ ] `max_concurrency` 调 1：并发请求中超出的排队，超过 `POOL_ACQUIRE_TIMEOUT_SECONDS` 失败
- [ ] 高峰时段（工作日 9–12、14–18，北京时区）的请求按高峰定价快照，
      其余时段（含午休 12–14、周末）按空闲全时段定价

## 6. 安全核对

- [ ] 应用日志全文 grep 不到：上游 Key 明文、平台 Key 明文、请求/回复消息体
- [ ] 日志中 `Authorization`、`X-Admin-Token`、`sk-ask-` 形态字段均被脱敏
- [ ] 上游 Key 只存在于 DSH 子进程环境（`ps eww` 不可见应用进程），DB 与日志均无
- [ ] 未携带/伪造平台 Key 的请求一律 401；管理面未登录一律 401
- [ ] DSH profile 工具面：`uv run pytest tests/test_dsh_profile_surface.py` 8 例通过，
      bash/pwsh/fs/skill/subagent/workflow 共 10 个工具 id 全部 `disabled: true`

## 7. 池与关停

- [ ] 10+ 并发请求下池大小在 min/max 间伸缩，队列不超过 `POOL_QUEUE_MAXSIZE`
- [ ] 空闲 runtime 超过 `POOL_IDLE_TTL_SECONDS` 被 reaper 回收
- [ ] SIGTERM：日志出现 graceful shutdown，在 `SHUTDOWN_GRACE_SECONDS` 内退出，无僵尸子进程

## 8. Docker 部署（Linux）

- [ ] `docker compose --env-file .env.example config` 校验通过；
      缺 `POSTGRES_PASSWORD` 时明确报错
- [ ] `docker build -f docker/Dockerfile .` 多阶段构建成功，镜像内无 `.env`、无 `data/`
- [ ] 按 README 顺序执行迁移 → profile 构建 → `up -d`；healthcheck 变 healthy
- [ ] 卷 `pgdata`/`dsh_homes`/`dsh_workspaces`/`dsh_sessions` 挂载正确，
      `docker compose down` 后数据仍在
- [ ] 端口默认只绑 `127.0.0.1`；entrypoint 日志确认未自动跑迁移

## 9. 备份恢复演练

- [ ] `pg_dump` 备份 → 删除一条请求 → 恢复 → 数据回来
- [ ] DSH 卷随 PG 备份一同保留；恢复后新问答可继续写入同一审计库

## 10. 待真实环境确认项（不阻塞交付）

- [ ] 真实 DSH 通知形状与 `EventProjector` 投影的一致性（`network` 标记冒烟）
- [ ] 请求级联网搜索开关经 SDK patches/system prompt 的实际生效方式
- [x] 定价按 DeepSeek 官方 2026 报价核对（2026-11-11，
      api-docs.deepseek.com/zh-cn/quick_start/pricing）；官方排除法定节假日，
      现按「工作日」近似，节假日误计高峰可临时停用高峰档
- [ ] 官方调价后通过控制台「定价规则」编辑或停用旧档，无需改代码
