# dsh_profile — DSH ask-gateway profile 裁剪层

ask-gateway 只提供**纯问答 + 联网搜索**。DSH 运行时随包的 `sdk` profile 自带
shell、文件系统、子代理、技能、工作流等工具，必须在 profile 层显式裁剪后才能上线。

## 文件

| 文件 | 职责 |
| --- | --- |
| `tool-denylist.patch.yml` | 唯一权威裁剪清单：10 个工具 id 全部 `disabled: true`，构建时写入每个 profile 的 `cordis.patch.yml` |
| `build_profile.py` | 一次性人工构建脚本：从随包 `sdk` 模板生成 `ask-gateway` profile、写入裁剪层、dump 校验工具面 |
| `fixtures/ask-gateway.dump.yml` | 钉版本 runtime（0.2.1-rc.2）组合后的完整 `--dump-config` 快照，离线契约夹具 |

## 裁剪口径

禁用（模型不可见、不可调用）：

- shell：`tool-bash`、`tool-pwsh`
- 文件系统：`tool-fs`、`tool-fs-search`
- 技能：`tool-skill`
- 子代理：`tool-subagent-control`、`tool-subagent-list-agents`、`tool-subagent`、`tool-subagent-fork`
- 工作流：`tool-workflow`

保留：`tool-web`（联网搜索 + 网页抓取，含 `web-search-deepseek` 后端）及问答、会话、
计量、压缩等基础设施插件。

## 构建

```bash
# 按 UPSTREAM_KEYS 派生的全部 label home 构建（data/dsh/homes/upstream-1 ...）
uv run python dsh_profile/build_profile.py

# 显式指定 DSH_HOME（无上游 Key 的环境/冒烟）
uv run python dsh_profile/build_profile.py --dsh-home /path/to/dsh-home
```

脚本幂等，可反复执行：每次都从模板全新复制 profile 再覆盖裁剪层，结果只取决于
仓库里的 patch 文件与 runtime 版本。runtime 版本与 `uv.lock` 中的
`deepseek-harness-sdk==0.2.1rc2` 对齐，漂移直接失败。

## 部署顺序（与迁移同级，绝不进应用 lifespan）

```bash
# 容器内
docker compose run --rm app uv run alembic upgrade head
docker compose run --rm app uv run python dsh_profile/build_profile.py
docker compose up -d
```

## 夹具更新流程

runtime 升级后需要重新捕获 `fixtures/ask-gateway.dump.yml`：

```bash
uv run python dsh_profile/build_profile.py --dsh-home /tmp/dsh-capture
BIN=$(uv run python -c "from deepseek_harness_runtime import resolve_bundled_launch_args; print(resolve_bundled_launch_args()[0])")
DSH_HOME=/tmp/dsh-capture "$BIN" --profile ask-gateway --dump-config > dsh_profile/fixtures/ask-gateway.dump.yml
# dump 的 patched-by 注释带捕获机绝对路径，归一化为占位符
uv run python -c "import re,pathlib; p=pathlib.Path('dsh_profile/fixtures/ask-gateway.dump.yml'); p.write_text(re.sub(r'patched by .*?/profiles/ask-gateway/cordis\.patch\.yml', 'patched by \$DSH_HOME/profiles/ask-gateway/cordis.patch.yml', p.read_text()), encoding='utf-8')"
```

## 契约测试

`tests/test_dsh_profile_surface.py`：静态夹具校验、夹具可移植性（无本机绝对路径）、
patch 与禁用清单同源检查、三类负向校验，以及用钉版本二进制在临时 `DSH_HOME`
实构建并与夹具比对。
