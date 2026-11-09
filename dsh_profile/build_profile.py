"""DSH ask-gateway profile 构建：一次性人工脚本，幂等，可反复执行。

用法（在项目根目录）：
    # 默认按 UPSTREAM_KEYS 派生的全部 label home 构建（data/dsh/homes/<label>）
    uv run python dsh_profile/build_profile.py
    # 显式指定一个或多个 DSH_HOME（无 UPSTREAM_KEYS 的环境/冒烟测试）
    uv run python dsh_profile/build_profile.py --dsh-home /path/to/dsh-home

设计约束：
- 绝不放进应用 lifespan；profile 是部署前置条件，由人工在目标环境执行，
  与 alembic 迁移同级。
- 每个 DSH_HOME 下重建 profiles/ask-gateway（从运行时随包 sdk 模板复制），
  再把同目录 tool-denylist.patch.yml 覆盖写入其 cordis.patch.yml。
  --from-default-profile 语义即“用模板全新替换自定义 profile”，
  重复执行得到同一结果。
- 构建后立即 --dump-config 校验工具面：禁用清单之外不得出现 shell/文件系统/
  子代理/技能/工作流工具，tool-web（联网搜索）必须保留。
- 运行时版本钉在 EXPECTED_RUNTIME_VERSION，与 uv.lock 的
  deepseek-harness-sdk==0.2.1rc2 对齐；漂移直接失败，不静默吃新版本。
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
from typing import Any

import yaml

from ask_gateway.config import Settings, get_settings
from ask_gateway.logging_conf import configure_logging, get_logger

log = get_logger(component="profile-build")

EXPECTED_RUNTIME_VERSION = "0.2.1-rc.2"
PROFILE_NAME = "ask-gateway"
TEMPLATE_PROFILE = "sdk"

# 与 tool-denylist.patch.yml 保持同源；构建结果必须满足这两份清单
DENIED_TOOL_IDS = (
    "tool-bash",
    "tool-pwsh",
    "tool-fs",
    "tool-fs-search",
    "tool-skill",
    "tool-subagent-control",
    "tool-subagent-list-agents",
    "tool-subagent",
    "tool-subagent-fork",
    "tool-workflow",
)
REQUIRED_ENABLED_IDS = ("tool-web", "web-search-deepseek")

_PATCH_FILE = Path(__file__).with_name("tool-denylist.patch.yml")


class ProfileBuildError(RuntimeError):
    """profile 构建或工具面校验失败。"""


def _construct_unknown(loader: yaml.Loader, tag_suffix: str, node: yaml.Node) -> Any:
    """Cordis 的 !!js 表达式在配置快照里只取静态值，不做求值。"""
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


class _CordisLoader(yaml.SafeLoader):
    """SafeLoader + 未知标签（!!js 等）惰性构造，不污染全局加载器。"""


# !!js 展开为 tag:yaml.org,2002:js 标准前缀，! 通配只接二级标签，两个前缀都要注册
_CordisLoader.add_multi_constructor("!", _construct_unknown)
_CordisLoader.add_multi_constructor("tag:yaml.org,2002:", _construct_unknown)


def _load_yaml_with_js_tags(text: str) -> list[dict[str, Any]]:
    """解析 Cordis entry-list YAML；!!js 表达式只取标量值，不做求值。"""
    loaded = yaml.load(text, Loader=_CordisLoader)
    if not isinstance(loaded, list):
        raise ProfileBuildError("dump-config 顶层不是 entry-list 数组")
    return loaded


def resolve_binary(settings: Settings) -> Path:
    """dsh_bin 显式配置优先，否则取 SDK 捆绑 runtime。"""
    if settings.dsh_bin:
        return Path(settings.dsh_bin).expanduser().resolve()
    try:
        from deepseek_harness_runtime import resolve_bundled_launch_args
    except ImportError as exc:
        raise ProfileBuildError(
            "找不到 deepseek-harness-runtime 捆绑运行时，请确认 runtime-bin 包已安装"
        ) from exc
    return Path(resolve_bundled_launch_args()[0])


def _run_runtime(binary: Path, home: Path, *extra_args: str) -> str:
    env = {**os.environ, "DSH_HOME": str(home)}
    completed = subprocess.run(
        [str(binary), *extra_args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        raise ProfileBuildError(
            f"dsh 执行失败 args={extra_args} stderr={completed.stderr.strip()}"
        )
    return completed.stdout


def assert_runtime_version(binary: Path) -> None:
    completed = subprocess.run(
        [str(binary), "--version"], capture_output=True, text=True, check=False, timeout=30
    )
    version = completed.stdout.strip()
    if version != EXPECTED_RUNTIME_VERSION:
        raise ProfileBuildError(
            f"runtime 版本漂移：期望 {EXPECTED_RUNTIME_VERSION}，实际 {version!r}；"
            "请先评估新版工具面再更新 EXPECTED_RUNTIME_VERSION 与夹具"
        )


def validate_surface(entries: list[dict[str, Any]]) -> None:
    """禁用清单必须全部 disabled: true；搜索工具必须存在且未被禁用。"""
    by_id = {entry.get("id"): entry for entry in entries if isinstance(entry, dict)}
    for tool_id in DENIED_TOOL_IDS:
        entry = by_id.get(tool_id)
        if entry is None:
            raise ProfileBuildError(f"工具面缺少预期插件 {tool_id}，上游模板可能已改名")
        if entry.get("disabled") is not True:
            raise ProfileBuildError(f"{tool_id} 未被禁用，工具面裁剪失效")
    for tool_id in REQUIRED_ENABLED_IDS:
        entry = by_id.get(tool_id)
        if entry is None:
            raise ProfileBuildError(f"联网搜索能力缺失：找不到 {tool_id}")
        if entry.get("disabled") is True:
            raise ProfileBuildError(f"{tool_id} 被意外禁用，问答搜索链路不可用")


def provision_home(binary: Path, home: Path, patch_text: str) -> list[dict[str, Any]]:
    """在单个 DSH_HOME 内构建并校验 ask-gateway profile，返回组合后的配置树。"""
    home.mkdir(parents=True, exist_ok=True)

    # 1) 从随包 sdk 模板全新复制 profile（不启动 app，dump 后即退出）
    _run_runtime(
        binary,
        home,
        "--profile",
        PROFILE_NAME,
        "--from-default-profile",
        TEMPLATE_PROFILE,
        "--dump-config",
    )
    profile_dir = home / "profiles" / PROFILE_NAME
    patch_path = profile_dir / "cordis.patch.yml"
    if not profile_dir.is_dir():
        raise ProfileBuildError(f"模板复制后未找到 profile 目录：{profile_dir}")

    # 2) 覆盖写入裁剪层（同源仓库文件，保证各 home 与版本库一致）
    patch_path.write_text(patch_text, encoding="utf-8")

    # 3) 重新组合配置并校验工具面
    dump = _run_runtime(binary, home, "--profile", PROFILE_NAME, "--dump-config")
    entries = _load_yaml_with_js_tags(dump)
    validate_surface(entries)
    return entries


def _target_homes(settings: Settings, explicit: list[Path]) -> list[Path]:
    if explicit:
        return [path.expanduser().resolve() for path in explicit]
    if not settings.upstream_keys:
        raise ProfileBuildError(
            "未配置 UPSTREAM_KEYS 且未传 --dsh-home，没有可构建的目标 home"
        )
    return [
        settings.dsh_home_root / f"upstream-{i + 1}"
        for i in range(len(settings.upstream_keys))
    ]


def build(patch_text: str, homes: list[Path], binary: Path) -> None:
    assert_runtime_version(binary)
    for home in homes:
        entries = provision_home(binary, home, patch_text)
        log.info(
            "profile built",
            home=str(home),
            profile=PROFILE_NAME,
            plugins=len(entries),
            denied=len(DENIED_TOOL_IDS),
        )


def main() -> None:
    configure_logging(log_json=False)
    parser = argparse.ArgumentParser(description="构建 ask-gateway DSH profile")
    parser.add_argument(
        "--dsh-home",
        type=Path,
        action="append",
        default=[],
        help="目标 DSH_HOME，可重复；缺省按 UPSTREAM_KEYS 派生全部 label home",
    )
    args = parser.parse_args()

    settings = get_settings()
    binary = resolve_binary(settings)
    homes = _target_homes(settings, args.dsh_home)
    patch_text = _PATCH_FILE.read_text(encoding="utf-8")
    build(patch_text, homes, binary)
    log.info("all profiles ready", homes=[str(home) for home in homes])


if __name__ == "__main__":
    main()
