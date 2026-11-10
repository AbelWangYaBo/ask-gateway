"""DSH ask-gateway profile 的构建与校验（运行时与脚本共用）。

动态上游 Key 的每个 home（data/dsh/homes/upstream-<id>）在第一次
spawn DSH 子进程前必须存在裁剪版 profile；本模块提供：
- provision_home：从随包 sdk 模板全新复制 + 写入裁剪层 + dump 校验
- ensure_profile：profile 已存在则跳过，否则构建（spawn 前的懒初始化）
- assert_runtime_version / validate_surface：版本与工具面断言

dsh_profile/build_profile.py 是本模块的 CLI 壳（人工部署命令不变）。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import yaml

from ask_gateway.config import Settings

EXPECTED_RUNTIME_VERSION = "0.2.2-rc.1"
PROFILE_NAME = "ask-gateway"
TEMPLATE_PROFILE = "sdk"

# 与 dsh_profile/tool-denylist.patch.yml 保持同源；构建结果必须满足这两份清单
DENIED_TOOL_IDS = (
    "tool-bash",
    "tool-pwsh",
    "tool-fs",
    "tool-fs-search",
    "tool-skill",
    "tool-subagent-control",
    "tool-subagent",
    "tool-subagent-fork",
    "tool-workflow",
    "tool-schedule",
    "tool-ask-user",
    "present",
)
REQUIRED_ENABLED_IDS = ("tool-web", "web-search-deepseek")


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

    _run_runtime(
        binary, home, "--profile", PROFILE_NAME, "--from-default-profile", TEMPLATE_PROFILE,
        "--dump-config",
    )
    profile_dir = home / "profiles" / PROFILE_NAME
    patch_path = profile_dir / "cordis.patch.yml"
    if not profile_dir.is_dir():
        raise ProfileBuildError(f"模板复制后未找到 profile 目录：{profile_dir}")

    patch_path.write_text(patch_text, encoding="utf-8")

    dump = _run_runtime(binary, home, "--profile", PROFILE_NAME, "--dump-config")
    entries = _load_yaml_with_js_tags(dump)
    validate_surface(entries)
    return entries


def patch_text_from_repo() -> str:
    """裁剪层唯一权威来源：仓库 dsh_profile/tool-denylist.patch.yml。"""
    # 项目根 = src/ask_gateway/services/ 上溯三级
    repo_root = Path(__file__).resolve().parents[3]
    patch = repo_root / "dsh_profile" / "tool-denylist.patch.yml"
    if not patch.is_file():
        raise ProfileBuildError(f"找不到裁剪层文件：{patch}")
    return patch.read_text(encoding="utf-8")


def ensure_profile(settings: Settings, home: Path) -> None:
    """spawn 前的懒初始化：profile 已存在即跳过，否则构建并校验。"""
    if (home / "profiles" / PROFILE_NAME / "cordis.yml").is_file():
        return
    binary = resolve_binary(settings)
    provision_home(binary, home, patch_text_from_repo())
