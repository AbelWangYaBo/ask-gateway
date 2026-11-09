# syntax=docker/dockerfile:1
# 多阶段：node:22 构建前端 → python3.12-slim（uv 基镜像）装运行时。
# runtime-bin wheel 自带 Node 运行时，运行阶段不装系统 Node。

# ---- 前端构建 ----
FROM node:22-slim AS frontend
WORKDIR /app/frontend
# 锁文件先行，依赖层可缓存
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# 产物输出到 /app/src/ask_gateway/server/static/dist（vite.config.ts 的相对 outDir）
RUN npm run build

# ---- Python 运行时 ----
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS runtime
WORKDIR /app

# 依赖层：只在 pyproject/lock 变化时重装
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 应用源码与部署期资产
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY dsh_profile/ ./dsh_profile/
COPY scripts/ ./scripts/
# uv_build 构建本项目时需要 readme 元数据文件
COPY README.md ./pyproject.toml ./
# 前端产物以镜像内新鲜构建为准，覆盖仓库提交的 dist
COPY --from=frontend /app/src/ask_gateway/server/static/dist ./src/ask_gateway/server/static/dist
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data

RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 8000

# 健康检查只打只读探针，stdlib 实现，镜像里不需要 curl
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

# 入口只启动服务。迁移与 DSH profile 构建是人工一次性命令，绝不进启动路径：
#   docker compose run --rm app uv run alembic upgrade head
#   docker compose run --rm app uv run python dsh_profile/build_profile.py
CMD ["uvicorn", "ask_gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
